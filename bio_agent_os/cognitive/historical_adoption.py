"""HBF-2 — nhập tịch lịch sử: adopt legacy projections, OFFLINE, một transaction.

    MISSING LEDGER != MISSING PROJECTION.
    Having a rollback point does not make live in-place mutation
    a safe migration protocol.

Đây là migration QUẢN TRỊ, không phải historical cleanup: memory lịch sử được
adopt AS-IS — không sửa observed_at của hàng drift, không ép curated giống
builder hiện tại. "Chúng ta hiểu vì sao khác" không bao giờ trở thành "chúng
ta được phép rewrite quá khứ." Audit ghi delta thật từng hàng; proof mang đúng
chữ `except_...`; không có đường promote lên FULL.

Module này chỉ được chạy trên CANDIDATE offline. Đường về canonical duy nhất
là `store_generation.install_generation` — không có nhánh nào mở write
connection vào store thật.

CORRECTION HBF-2.1 (19/08, chủ bắt trên commit 7c3ee9f): bản đầu nhét
provenance vào `locked_by` của outbox terminal — biến field có nghĩa LEASE
OWNERSHIP thành túi đựng migration provenance, để lại "worker ma" sống vĩnh
viễn trong mọi query ownership. Terminal row của migration giờ mang ĐÚNG
hình dạng terminal của production `complete()`/`skip()`: locked_by=NULL,
locked_at=NULL. Provenance ở đúng nhà: `ledger.worker_id` + bảng audit.
Cùng nhát dao đó: audit không được điền cột NULL-able chỉ vì có sẵn một số
nguyên — curated không có builder comparison thì `builder_version_checked`
là NULL; NOT_APPLICABLE phải phân biệt được với version 1.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from .outbox import JobStatus, OUTBOX_SCHEMA, projection_key
from .projection_equivalence import compare
from .projection_registry import ProjectionType, spec
from .reconciliation_worker import LEDGER_SCHEMA

MIGRATION_ACTOR = "migration:hbf-adopt"
_PTYPE = ProjectionType.COGNITIVE_MEMORY.value

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_adoption_audit (
    projection_key            TEXT PRIMARY KEY,
    event_id                  TEXT NOT NULL,
    target_id                 TEXT,
    origin                    TEXT NOT NULL,
    management_action         TEXT NOT NULL,
    adopted_at                TEXT NOT NULL,
    migration_run_id          TEXT NOT NULL,
    projection_hash           TEXT,
    canonical_candidate_hash  TEXT,
    equivalence_proof         TEXT,
    contract_name             TEXT,
    contract_version          INTEGER,
    observed_at_delta_ms      REAL,
    structured_content_status TEXT,
    builder_version_checked   INTEGER,
    source_snapshot_sha256    TEXT,
    reasons_json              TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_adoption_event ON projection_adoption_audit(event_id);
CREATE INDEX IF NOT EXISTS idx_adoption_run ON projection_adoption_audit(migration_run_id);
"""

#: Bốn lớp adopt — proof phải YẾU HƠN HOẶC BẰNG measurement (HBF-1.1).
PROOFS = {
    "ADOPT_FULL_CONTRACT": "full_projection_contract_v1",
    "ADOPT_HISTORICAL_PARTIAL": "full_contract_except_observed_at_epsilon_v1",
    "ADOPT_CURATED_PRESERVED": "content_plus_curated_provenance_v1",
    "ADOPT_CURATED_PARTIAL":
        "content_plus_curated_provenance_except_observed_at_v1",
}
ADOPT_CLASSES = tuple(PROOFS)
#: Lớp nào >0 thì cổng đóng — migration không được phép chạy.
BLOCKING_CLASSES = ("TRUE_MISSING", "AMBIGUOUS", "DIVERGENT", "ANOMALY",
                    "UNEXPLAINED")
ALL_CLASSES = ADOPT_CLASSES + ("EVENT_ONLY_SKIP", "TOMBSTONE_EXCLUDE",
                               "ERASED_EXCLUDE", "ALREADY_MANAGED",
                               "LIVE_QUEUE") + BLOCKING_CLASSES


class AdmissibilityError(RuntimeError):
    """Population chưa đủ điều kiện, hoặc phát hiện partial state."""


class InjectedAbort(RuntimeError):
    """Chỉ dùng cho bài kiểm admissibility: abort TRƯỚC COMMIT."""


@dataclass(frozen=True)
class DriftPolicy:
    """Admissibility policy ĐÃ ĐO trên population lịch sử này — KHÔNG phải
    định luật "same call luôn <100ms". OS scheduling không cho guarantee đó.

    Đo được (HBF-1.1, 46 hàng): drift luôn DƯƠNG, 0.505–53.861ms — cơ chế là
    đường ghi trước khi `observed_at` inheritance được thêm vào `remember()`
    đóng dấu đồng hồ riêng, trễ hơn event trong cùng lời gọi. Hàng lọt policy
    vẫn mang proof `except_observed_at...` với delta thật trong audit —
    không có đường promote lên FULL."""

    positive_only: bool = True
    max_ms: float = 100.0

    def admits(self, delta_ms: float) -> bool:
        if self.positive_only and delta_ms <= 0:
            return False
        return abs(delta_ms) < self.max_ms


@dataclass(slots=True)
class EventDecision:
    event_id: str
    tenant_id: str
    cls: str
    contract_name: str | None = None
    proof: str | None = None
    target_id: str | None = None
    observed_at_delta_ms: float | None = None
    structured_content_status: str | None = None
    projection_hash: str | None = None
    canonical_candidate_hash: str | None = None
    reasons: list = field(default_factory=list)


@dataclass(slots=True)
class ClassificationReport:
    rows: list = field(default_factory=list)
    total_events: int = 0

    @property
    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in ALL_CLASSES}
        for r in self.rows:
            out[r.cls] = out.get(r.cls, 0) + 1
        return out

    def of(self, cls: str) -> list:
        return [r for r in self.rows if r.cls == cls]

    @property
    def drift_stats(self) -> dict[str, float | int]:
        deltas = [r.observed_at_delta_ms for r in self.rows
                  if r.observed_at_delta_ms is not None]
        if not deltas:
            return {"n": 0}
        return {"n": len(deltas), "min_ms": min(deltas), "max_ms": max(deltas)}


@dataclass(slots=True)
class AdoptionResult:
    migration_run_id: str
    adopted: int = 0
    skipped_event_only: int = 0
    excluded_tombstoned: int = 0
    noop_reapply: int = 0
    outbox_inserted: int = 0
    ledger_inserted: int = 0
    audit_inserted: int = 0
    committed: bool = False


# ---------------------------------------------------------------------------
# phân loại
# ---------------------------------------------------------------------------

def _marker_only(content: str) -> bool:
    """Cùng luật substantive của adapter, áp lên content đã render (HBF-0)."""
    stripped = content.strip()
    return (not stripped
            or (stripped.startswith("hook=") and " | " not in stripped)
            or (stripped.startswith("hook=")
                and stripped.split(" | ", 1)[1].strip() == ""))


def _iso_delta_ms(event_iso: Any, memory_iso: Any) -> float | None:
    try:
        ev = datetime.fromisoformat(str(event_iso))
        mv = datetime.fromisoformat(str(memory_iso))
    except (TypeError, ValueError):
        return None
    return (mv - ev).total_seconds() * 1000.0


def _norm_obj(obj: Any) -> str:
    return json.dumps(obj or {}, sort_keys=True, ensure_ascii=False,
                      default=str)


def _downgrade_structured(proof: str) -> str:
    """full_..._v1 → full_..._except_structured_content_v1; nếu đã có except_
    thì nối bằng _and_. Tên proof chỉ được YẾU đi, không mạnh lên."""
    stem = proof[: -len("_v1")] if proof.endswith("_v1") else proof
    joiner = "_and_" if "_except_" in stem else "_except_"
    return f"{stem}{joiner}structured_content_v1"


#: Khung trường của hash — đúng tập comparator FULL đo (HBF-1.1), cộng
#: structured slot và echo metadata tái tạo được. metadata.state là ngữ cảnh
#: LÚC GỌI (mode/stress) — không tái tạo được từ event, nên KHÔNG nằm trong
#: hash; comparator kiểm nó bằng presence + đối chiếu echo, không bằng giá trị.
_HASH_FIELDS = ("content", "tenant_id", "workspace_id", "trust_tier",
                "security_label", "valid_from", "valid_to", "observed_at",
                "modality", "confidence", "importance", "salience", "utility",
                "memory_type", "lifecycle_state", "verification_status")


def _skeleton_hash(values: dict[str, Any], structured_norm: str,
                   meta_echo: dict[str, Any]) -> str:
    body = {k: (None if values.get(k) is None else str(values.get(k)))
            for k in _HASH_FIELDS}
    body["structured"] = structured_norm
    body["meta_echo"] = {k: meta_echo.get(k) for k in
                         ("hook", "session_id", "tool")}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stored_hash(mem: dict[str, Any]) -> str:
    meta = json.loads(mem.get("metadata_json") or "{}")
    meta.pop("projection", None)
    stored_structured = json.loads(mem.get("structured_json") or "{}") \
        if mem.get("structured_json") else {}
    return _skeleton_hash(mem, _norm_obj(stored_structured), meta)


def _canonical_hash(event: dict[str, Any], content: str, hook: str,
                    derived_slot: Any) -> str:
    from .projection_equivalence import hook_contract
    values: dict[str, Any] = {k: event.get(k) for k in _HASH_FIELDS}
    values["content"] = content
    values.update(hook_contract(hook))
    values.update({"memory_type": "episodic", "lifecycle_state": "proposed",
                   "verification_status": "unverified"})
    ev_meta = json.loads(event.get("metadata_json") or "{}")
    return _skeleton_hash(values, _norm_obj(derived_slot), ev_meta)


def classify_store(conn: sqlite3.Connection, *,
                   slot_fn: Callable[[str, str | None], Any] | None = None,
                   drift_policy: DriftPolicy = DriftPolicy(),
                   ) -> ClassificationReport:
    """Phân lớp TƯƠI toàn bộ population — số đo mới thắng số cũ.

    `slot_fn(content, source)` đóng nốt trường `structured_content` TRƯỚC
    transaction (ràng buộc chữ ký HBF-2): tái tạo được → đóng; không tái tạo
    được → DOWNGRADE proof, không ép count. `slot_fn=None` (chỉ phân lớp) ghi
    `not_measured` — trạng thái đó KHÔNG đủ điều kiện adopt."""
    conn.row_factory = sqlite3.Row
    report = ClassificationReport()

    # Store lịch sử có thể TIỀN-outbox: thiếu bảng không phải lỗi, nó là
    # chính hình dạng dữ liệu mà migration này tồn tại để xử lý.
    try:
        ledger_events = {str(r["event_id"]) for r in conn.execute(
            "SELECT event_id FROM projection_ledger WHERE projection_type=?",
            (_PTYPE,))}
    except sqlite3.OperationalError:
        ledger_events = set()
    try:
        tombstoned = {str(r["event_id"]) for r in conn.execute(
            "SELECT event_id FROM memory_tombstones "
            "WHERE event_id IS NOT NULL AND lifted=0")}
    except sqlite3.OperationalError:
        tombstoned = set()
    try:
        erased = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM event_erasures")}
    except sqlite3.OperationalError:
        erased = set()
    outbox_status: dict[str, set[str]] = {}
    try:
        for r in conn.execute(
                "SELECT event_id, status FROM projection_outbox "
                "WHERE projection_type=?", (_PTYPE,)):
            outbox_status.setdefault(str(r["event_id"]), set()).add(
                str(r["status"]))
    except sqlite3.OperationalError:
        pass

    mem_by_event: dict[str, list[str]] = {}
    for r in conn.execute("SELECT event_id, memory_id FROM memory_source_events"):
        mem_by_event.setdefault(str(r["event_id"]), []).append(
            str(r["memory_id"]))

    events = [dict(r) for r in conn.execute("SELECT * FROM cognitive_events")]
    report.total_events = len(events)

    for ev in events:
        eid = str(ev["event_id"])
        tenant = str(ev.get("tenant_id") or "")
        try:
            payload = json.loads(ev.get("payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        content = str(payload.get("content", ""))
        d = EventDecision(event_id=eid, tenant_id=tenant, cls="UNEXPLAINED")
        report.rows.append(d)

        if eid in tombstoned:
            d.cls = "TOMBSTONE_EXCLUDE"
            continue
        if eid in erased:
            d.cls = "ERASED_EXCLUDE"
            continue
        statuses = outbox_status.get(eid, set())
        if eid in ledger_events:
            d.cls = "ALREADY_MANAGED"
            continue
        if statuses & {JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value,
                       JobStatus.DEAD_LETTER.value}:
            d.cls = "LIVE_QUEUE"          # nợ của pipeline sống, không của HBF
            continue
        if JobStatus.COMPLETED.value in statuses:
            d.cls = "ANOMALY"
            d.reasons = ["outbox COMPLETED nhưng không có ledger"]
            continue
        if JobStatus.SKIPPED.value in statuses:
            d.cls = "ALREADY_MANAGED"
            continue

        # MARKER-ONLY KHÔNG ĐỦ ĐỂ NÓI "KHÔNG NỢ GÌ".
        #
        # Bản đầu xét marker-only TRƯỚC khi nhìn có ký ức hay không, nên 3
        # event `hook=SessionStart` từ 07/08 — thời legacy còn materialize cả
        # marker — bị ghi audit là "skipped_event_only", tức là khai "không có
        # projection nào ở đây" trong khi projection ĐANG NẰM ĐÓ. Sai về
        # provenance, và tệ hơn: 3 ký ức ấy sẽ không có ledger, đúng thứ mà lễ
        # nhập tịch sinh ra để ngăn. Review đối kháng trước HBF-3 bắt được.
        #
        # Luật: hình dạng NỘI DUNG không quyết định có nợ hay không —
        # QUAN HỆ với ký ức đã materialize mới quyết định.
        legacy = mem_by_event.get(eid, [])
        if not legacy:
            d.cls = "EVENT_ONLY_SKIP" if _marker_only(content) \
                else "TRUE_MISSING"
            if d.cls == "EVENT_ONLY_SKIP":
                d.proof = "substantive_gate_v1"
            continue
        if len(legacy) > 1:
            d.cls = "AMBIGUOUS"
            d.reasons = [f"{len(legacy)} memory cùng trỏ một event"]
            continue
        mem_row = conn.execute(
            "SELECT * FROM cognitive_memories WHERE memory_id=? "
            "ORDER BY version DESC LIMIT 1", (legacy[0],)).fetchone()
        if mem_row is None:
            d.cls = "AMBIGUOUS"
            d.reasons = ["link trỏ tới memory đã biến mất phía serving"]
            continue
        mem = dict(mem_row)
        d.target_id = str(mem["memory_id"])

        verdict = compare(ev, mem)
        d.contract_name = verdict.contract_name
        d.reasons = list(verdict.reasons)
        curated = verdict.contract_name == "curated_seed_v1"

        if verdict.equivalent:
            d.cls = "ADOPT_CURATED_PRESERVED" if curated \
                else "ADOPT_FULL_CONTRACT"
        elif verdict.reasons and all(
                r.startswith("observed_at:") for r in verdict.reasons):
            delta = _iso_delta_ms(ev.get("observed_at"), mem.get("observed_at"))
            d.observed_at_delta_ms = delta
            if delta is not None and drift_policy.admits(delta):
                d.cls = "ADOPT_CURATED_PARTIAL" if curated \
                    else "ADOPT_HISTORICAL_PARTIAL"
            else:
                d.cls = "DIVERGENT"
                d.reasons.append(
                    f"drift {delta}ms ngoài policy đã đo "
                    f"(positive_only={drift_policy.positive_only}, "
                    f"<{drift_policy.max_ms}ms)")
                continue
        else:
            d.cls = "DIVERGENT"
            continue

        d.proof = PROOFS[d.cls]
        d.projection_hash = _stored_hash(mem)

        if curated:
            # Tác giả chọn tay semantics — CHÍNH ĐÓ là contract. Hash canonical
            # "đẹp nhưng vô nghĩa" sẽ nói dối; NULL trung thực hơn.
            d.structured_content_status = "curated_preserved_as_authored"
            d.canonical_candidate_hash = None
            continue

        # -- đóng trường structured_content TRƯỚC transaction (chữ ký HBF-2)
        source = str(ev.get("source") or "")
        hook = source.split(":", 1)[1] if source.startswith("claude-code:") \
            else ""
        if slot_fn is None:
            d.structured_content_status = "not_measured"
            continue
        derived = slot_fn(content, source)
        stored_structured = json.loads(mem.get("structured_json") or "{}") \
            if mem.get("structured_json") else {}
        if _norm_obj(stored_structured) == _norm_obj(derived):
            d.structured_content_status = "reconstructed_equal"
        else:
            d.structured_content_status = "not_reconstructable_downgraded"
            d.proof = _downgrade_structured(d.proof)
            d.reasons.append("structured_content không tái tạo được bằng "
                             "slot extractor tất định hiện tại — proof hạ, "
                             "không ép count")
        d.canonical_candidate_hash = _canonical_hash(ev, content, hook, derived)

    return report


# ---------------------------------------------------------------------------
# cổng + transaction
# ---------------------------------------------------------------------------

def adoption_gate(report: ClassificationReport) -> None:
    """Cổng admissibility: lớp chặn nào >0 → migration không được chạy."""
    counts = report.counts
    problems = [f"{k}={counts[k]}" for k in BLOCKING_CLASSES if counts[k]]
    unmeasured = [r for r in report.rows if r.cls in ADOPT_CLASSES
                  and r.structured_content_status in (None, "not_measured")
                  and r.cls in ("ADOPT_FULL_CONTRACT",
                                "ADOPT_HISTORICAL_PARTIAL")]
    if unmeasured:
        problems.append(
            f"structured_content chưa đóng trên {len(unmeasured)} hàng hook — "
            f"chữ ký HBF-2 yêu cầu đóng TRƯỚC transaction")
    total = sum(counts.values())
    if total != report.total_events:
        problems.append(f"counts không cân: {total} != {report.total_events}")
    if problems:
        raise AdmissibilityError("; ".join(problems))


@dataclass(slots=True)
class ClosureState:
    """HBF3-0 — CLOSURE LAW: ba row phải LIÊN KẾT ĐÚNG NGỮ NGHĨA, không phải
    chỉ đủ mặt.

        PRESENT != CLOSED.

    Bản HBF-2 trả ba boolean tồn tại, nên một triple đủ ba hàng nhưng sai
    `status` hoặc sai `target_id` vẫn được coi là reapply no-op — migration
    sẽ đi qua một hàng hỏng và báo "đã xong". K4 không bắt được vì nó chạy
    lại trên triple vừa tạo đúng. Khoá trước khi chạm canonical thật."""

    present: int = 0
    outbox: bool = False
    ledger: bool = False
    audit: bool = False
    conflicts: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.present == 0

    @property
    def complete(self) -> bool:
        return self.present == self.expected

    #: Số hàng mà lớp này ĐÁNG RA phải có (adopt=3, event-only=2).
    expected: int = 3

    @property
    def closed(self) -> bool:
        """Đóng = đủ hàng VÀ không mâu thuẫn nào."""
        return self.complete and not self.conflicts


def closure_state(conn: sqlite3.Connection, key: str, d: "EventDecision",
                  *, want_status: str, want_action: str,
                  want_ledger: bool) -> ClosureState:
    version = spec(_PTYPE).version
    st = ClosureState(expected=3 if want_ledger else 2)

    row = conn.execute(
        "SELECT status, projection_key, tenant_id FROM projection_outbox "
        "WHERE event_id=? AND projection_type=? AND projection_version=?",
        (d.event_id, _PTYPE, version)).fetchone()
    if row is not None:
        st.outbox = True
        st.present += 1
        if str(row[0]) != want_status:
            st.conflicts.append(
                f"outbox.status={row[0]!r} != {want_status!r}")
        if str(row[1]) != key:
            st.conflicts.append(
                f"outbox.projection_key lệch key phái sinh")
        if str(row[2]) != d.tenant_id:
            st.conflicts.append(
                f"outbox.tenant_id={row[2]!r} != {d.tenant_id!r}")

    if want_ledger:
        row = conn.execute(
            "SELECT target_id, projection_type, projection_version, tenant_id "
            "FROM projection_ledger WHERE projection_key=?", (key,)).fetchone()
        if row is not None:
            st.ledger = True
            st.present += 1
            if str(row[0]) != str(d.target_id):
                st.conflicts.append(
                    f"ledger.target_id={str(row[0])[:8]!r} != "
                    f"{str(d.target_id)[:8]!r} — nhập tịch trỏ SAI ký ức")
            if str(row[1]) != _PTYPE or int(row[2]) != version:
                st.conflicts.append("ledger type/version lệch hợp đồng")
            if str(row[3]) != d.tenant_id:
                st.conflicts.append(f"ledger.tenant_id={row[3]!r} lệch")
    elif conn.execute("SELECT 1 FROM projection_ledger WHERE projection_key=?",
                      (key,)).fetchone() is not None:
        st.conflicts.append("có ledger cho hàng KHÔNG được materialize")

    row = conn.execute(
        "SELECT management_action, event_id FROM projection_adoption_audit "
        "WHERE projection_key=?", (key,)).fetchone()
    if row is not None:
        st.audit = True
        st.present += 1
        if str(row[0]) != want_action:
            st.conflicts.append(
                f"audit.management_action={row[0]!r} != {want_action!r}")
        if str(row[1]) != d.event_id:
            st.conflicts.append("audit.event_id lệch hàng đang xét")
    return st


def adopt(conn: sqlite3.Connection, report: ClassificationReport, *,
          migration_run_id: str, source_snapshot_sha256: str,
          now: float | None = None, adopted_at: str | None = None,
          fail_before_commit: bool = False) -> AdoptionResult:
    """MỘT transaction trên candidate OFFLINE. Idempotent theo bộ ba
    (outbox terminal, ledger, audit): đủ ba → noop; thiếu lệch → lỗi;
    trống → ghi. Lễ nhập tịch chạy lần hai mà cấp thêm hộ chiếu thì
    không phải lễ, nó là máy photocopy (K4)."""
    adoption_gate(report)
    import time as _time
    now = _time.time() if now is None else now
    adopted_at = adopted_at or datetime.now(timezone.utc).isoformat()
    version = spec(_PTYPE).version
    result = AdoptionResult(migration_run_id=migration_run_id)

    # executescript tự COMMIT — phải xong TRƯỚC khi mở transaction.
    conn.executescript(OUTBOX_SCHEMA)
    conn.executescript(LEDGER_SCHEMA)
    conn.executescript(AUDIT_SCHEMA)

    old_isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")

        def _audit(key: str, d: EventDecision, origin: str, action: str,
                   proof: str | None, contract: str | None,
                   contract_version: int | None,
                   builder_version_checked: int | None) -> None:
            """Cột NULL-able chỉ được điền khi claim là THẬT: curated không
            có builder comparison → builder NULL; tombstone/event-only không
            có contract nào được áp → cả ba NULL. NOT_APPLICABLE phải phân
            biệt được với version 1 (HBF-2.1)."""
            conn.execute(
                "INSERT INTO projection_adoption_audit VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, d.event_id, d.target_id, origin, action, adopted_at,
                 migration_run_id, d.projection_hash,
                 d.canonical_candidate_hash, proof, contract,
                 contract_version, d.observed_at_delta_ms,
                 d.structured_content_status, builder_version_checked,
                 source_snapshot_sha256,
                 json.dumps(d.reasons, ensure_ascii=False)))
            result.audit_inserted += 1

        for d in report.rows:
            key = projection_key(d.event_id, _PTYPE, version)
            if d.cls in ADOPT_CLASSES:
                st = closure_state(conn, key, d,
                                   want_status=JobStatus.COMPLETED.value,
                                   want_action="adopted", want_ledger=True)
                if st.closed:
                    result.noop_reapply += 1
                    continue
                if st.conflicts:
                    raise AdmissibilityError(
                        f"closure conflict tại {d.event_id[:8]}: "
                        f"{'; '.join(st.conflicts)}")
                if not st.empty:
                    raise AdmissibilityError(
                        f"partial triple tại {d.event_id[:8]}: "
                        f"outbox={st.outbox} ledger={st.ledger} "
                        f"audit={st.audit}")
                # Terminal shape ĐÚNG của production complete(): locked_by
                # và locked_at đều NULL. Provenance nằm ở ledger + audit,
                # không nằm trong field lease (HBF-2.1).
                conn.execute(
                    "INSERT INTO projection_outbox (job_id, event_id, "
                    "projection_type, projection_version, projection_key, "
                    "tenant_id, status, attempts, available_at, locked_by, "
                    "locked_at, last_error, payload_json, created_at, "
                    "completed_at) VALUES (?,?,?,?,?,?,?,0,?,NULL,NULL,NULL,"
                    "'{}',?,?)",
                    (str(uuid4()), d.event_id, _PTYPE, version, key,
                     d.tenant_id, JobStatus.COMPLETED.value, now, now, now))
                conn.execute(
                    "INSERT INTO projection_ledger (projection_key, event_id, "
                    "projection_type, projection_version, tenant_id, "
                    "target_id, worker_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (key, d.event_id, _PTYPE, version, d.tenant_id,
                     d.target_id, MIGRATION_ACTOR, now))
                result.outbox_inserted += 1
                result.ledger_inserted += 1
                curated = d.contract_name == "curated_seed_v1"
                _audit(key, d, "legacy_projection", "adopted", d.proof,
                       d.contract_name, contract_version=1,
                       # curated: builder KHÔNG được áp — persisted memory
                       # chính là authored contract; NULL, không phải 1.
                       builder_version_checked=None if curated else version)
                result.adopted += 1
            elif d.cls == "EVENT_ONLY_SKIP":
                st = closure_state(conn, key, d,
                                   want_status=JobStatus.SKIPPED.value,
                                   want_action="skipped_event_only",
                                   want_ledger=False)
                if st.closed:
                    result.noop_reapply += 1
                    continue
                if st.conflicts:
                    raise AdmissibilityError(
                        f"closure conflict (event-only) tại "
                        f"{d.event_id[:8]}: {'; '.join(st.conflicts)}")
                if not st.empty:
                    raise AdmissibilityError(
                        f"partial event-only tại {d.event_id[:8]}: "
                        f"outbox={st.outbox} audit={st.audit}")
                conn.execute(
                    "INSERT INTO projection_outbox (job_id, event_id, "
                    "projection_type, projection_version, projection_key, "
                    "tenant_id, status, attempts, available_at, locked_by, "
                    "locked_at, last_error, payload_json, created_at, "
                    "completed_at) VALUES (?,?,?,?,?,?,?,0,?,NULL,NULL,"
                    "'historical_event_only','{}',?,?)",
                    (str(uuid4()), d.event_id, _PTYPE, version, key,
                     d.tenant_id, JobStatus.SKIPPED.value, now, now, now))
                result.outbox_inserted += 1
                _audit(key, d, "historical_event_only", "skipped_event_only",
                       "substantive_gate_v1", None,
                       contract_version=None, builder_version_checked=None)
                result.skipped_event_only += 1
            elif d.cls == "TOMBSTONE_EXCLUDE":
                # KHÔNG outbox row — bia mộ là authority; audit chỉ ghi sổ.
                row = conn.execute(
                    "SELECT management_action FROM projection_adoption_audit "
                    "WHERE projection_key=?", (key,)).fetchone()
                if row is not None:
                    if str(row[0]) != "excluded_tombstoned":
                        raise AdmissibilityError(
                            f"closure conflict (tombstone) tại "
                            f"{d.event_id[:8]}: audit.action={row[0]!r}")
                    result.noop_reapply += 1
                    continue
                _audit(key, d, "tombstoned", "excluded_tombstoned", None, None,
                       contract_version=None, builder_version_checked=None)
                result.excluded_tombstoned += 1
            # ALREADY_MANAGED / LIVE_QUEUE / ERASED_EXCLUDE: không chạm.

        if fail_before_commit:
            raise InjectedAbort("HBF-2 admissibility: abort trước COMMIT")
        conn.execute("COMMIT")
        result.committed = True
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = old_isolation
    return result


# ---------------------------------------------------------------------------
# bất biến sau adopt + digest
# ---------------------------------------------------------------------------

def verify_closure(conn: sqlite3.Connection,
                   report: ClassificationReport) -> dict[str, Any]:
    """HBF3-0 — kiểm closure NGỮ NGHĨA trên TOÀN population sau adopt.

    Không phải "đếm ba row". Mỗi hàng adopt phải: outbox COMPLETED đúng key
    và tenant · ledger trỏ ĐÚNG target_id đang được nhập tịch · audit đúng
    management_action. Trả về counts + danh sách vi phạm (phải rỗng)."""
    version = spec(_PTYPE).version
    out: dict[str, Any] = {"closed": 0, "not_closed": [], "checked": 0}
    for d in report.rows:
        if d.cls in ADOPT_CLASSES:
            want = (JobStatus.COMPLETED.value, "adopted", True)
        elif d.cls == "EVENT_ONLY_SKIP":
            want = (JobStatus.SKIPPED.value, "skipped_event_only", False)
        elif d.cls == "TOMBSTONE_EXCLUDE":
            row = conn.execute(
                "SELECT management_action FROM projection_adoption_audit "
                "WHERE projection_key=?",
                (projection_key(d.event_id, _PTYPE, version),)).fetchone()
            out["checked"] += 1
            if row is not None and str(row[0]) == "excluded_tombstoned":
                out["closed"] += 1
            else:
                out["not_closed"].append(
                    {"event": d.event_id[:8], "cls": d.cls,
                     "why": ["audit bia mộ thiếu/sai action"]})
            continue
        else:
            continue
        out["checked"] += 1
        st = closure_state(conn, projection_key(d.event_id, _PTYPE, version),
                           d, want_status=want[0], want_action=want[1],
                           want_ledger=want[2])
        if st.closed:
            out["closed"] += 1
        else:
            out["not_closed"].append(
                {"event": d.event_id[:8], "cls": d.cls,
                 "present": st.present, "expected": st.expected,
                 "why": st.conflicts or ["thiếu hàng"]})
    return out


def verify_closure_from_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Closure đọc từ CHÍNH SỔ AUDIT — dùng SAU khi migration đã cài.

    `verify_closure(report)` cần một ClassificationReport; nhưng sau khi cài,
    phân loại tươi thấy mọi hàng adopt là ALREADY_MANAGED, nên nó chỉ còn soi
    được 2 hàng bia mộ và báo "2/2 closed" — một màu xanh đo gần như không gì.
    Review đối kháng trước HBF-3 bắt đúng chỗ này.

    Ở đây population là 307 hàng audit đã ghi, và mỗi hàng phải tự chứng minh
    lại: outbox đúng status · ledger đúng target · không lease sót."""
    out: dict[str, Any] = {"checked": 0, "closed": 0, "not_closed": []}
    for row in conn.execute(
            "SELECT projection_key, event_id, target_id, management_action "
            "FROM projection_adoption_audit"):
        out["checked"] += 1
        key, eid = str(row[0]), str(row[1])
        target, action = row[2], str(row[3])
        why: list = []
        ob = conn.execute(
            "SELECT status, locked_by, locked_at, projection_key FROM "
            "projection_outbox WHERE event_id=? AND projection_type=?",
            (eid, _PTYPE)).fetchone()
        lg = conn.execute(
            "SELECT target_id, worker_id FROM projection_ledger "
            "WHERE projection_key=?", (key,)).fetchone()

        if action == "adopted":
            if ob is None:
                why.append("thiếu outbox")
            elif str(ob[0]) != JobStatus.COMPLETED.value:
                why.append(f"outbox.status={ob[0]!r}")
            if lg is None:
                why.append("thiếu ledger")
            elif str(lg[0]) != str(target):
                why.append("ledger.target_id trỏ sai ký ức")
            elif str(lg[1]) != MIGRATION_ACTOR:
                why.append(f"ledger.worker_id={lg[1]!r}")
        elif action == "skipped_event_only":
            if ob is None:
                why.append("thiếu outbox")
            elif str(ob[0]) != JobStatus.SKIPPED.value:
                why.append(f"outbox.status={ob[0]!r} != skipped")
            if lg is not None:
                why.append("event-only mà có ledger")
        elif action == "excluded_tombstoned":
            # Bia mộ CÓ ledger cũ là hợp lệ: nó nói "projection này đã từng
            # được dựng" — bằng chứng lịch sử, và chính nó chặn replay hồi
            # sinh. Cấm mọi ledger là cấm nhầm quá khứ (false-red bắt được
            # ngay sau install HBF-3: hai hàng bia mộ mang ledger của
            # `canary-a4` và `hook-22344`). Điều CẤM là migration tự cấp.
            if lg is not None and str(lg[1]) == MIGRATION_ACTOR:
                why.append("migration cấp ledger cho hàng đã chôn")
        else:
            why.append(f"management_action lạ: {action!r}")

        if ob is not None and (ob[1] is not None or ob[2] is not None):
            why.append("terminal row còn mang lease")
        if ob is not None and str(ob[3]) != key:
            why.append("outbox.projection_key lệch audit")

        if why:
            out["not_closed"].append({"event": eid[:8], "action": action,
                                      "why": why})
        else:
            out["closed"] += 1
    return out


#: Các bảng mà adoption KHÔNG ĐƯỢC PHÉP chạm — semantic delta phải bằng 0.
SEMANTIC_TABLES = ("cognitive_memories", "memory_source_events",
                   "memory_tombstones", "cognitive_events")


def tables_digest(conn: sqlite3.Connection,
                  tables: tuple[str, ...] = SEMANTIC_TABLES) -> str:
    h = hashlib.sha256()
    for t in tables:
        h.update(t.encode())
        try:
            rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        except sqlite3.OperationalError:
            h.update(b"<absent>")
            continue
        for row in sorted(repr(tuple(r)) for r in rows):
            h.update(row.encode("utf-8", "replace"))
    return h.hexdigest()


def adoption_invariants(conn: sqlite3.Connection) -> dict[str, int]:
    """Mọi giá trị (trừ migration_rows_total) phải bằng 0.

    HBF-2.1: migration rows được nhận diện qua AUDIT JOIN — sổ provenance
    thật — chứ không qua locked_by, vì terminal row đúng chuẩn không còn
    mang lease. Bốn bất biến provenance mới đi kèm hai mutant P1/P2."""
    q = conn.execute
    return {
        "audit_adopted_without_ledger": q(
            "SELECT COUNT(*) FROM projection_adoption_audit a WHERE "
            "a.management_action='adopted' AND NOT EXISTS (SELECT 1 FROM "
            "projection_ledger l WHERE l.projection_key=a.projection_key)"
        ).fetchone()[0],
        "migration_ledger_without_audit": q(
            "SELECT COUNT(*) FROM projection_ledger l WHERE l.worker_id=? "
            "AND NOT EXISTS (SELECT 1 FROM projection_adoption_audit a "
            "WHERE a.projection_key=l.projection_key)", (MIGRATION_ACTOR,)
        ).fetchone()[0],
        "migration_outbox_completed_without_ledger": q(
            "SELECT COUNT(*) FROM projection_outbox o "
            "JOIN projection_adoption_audit a "
            "  ON a.projection_key=o.projection_key "
            "WHERE a.management_action='adopted' AND o.status=? "
            "AND NOT EXISTS (SELECT 1 FROM projection_ledger l "
            "WHERE l.projection_key=o.projection_key)",
            (JobStatus.COMPLETED.value,)).fetchone()[0],
        "migration_outbox_skipped_without_audit": q(
            # đối xứng nghĩa cũ: outbox SKIPPED do migration đặt (reason là
            # historical_event_only) mà không có audit đi kèm
            "SELECT COUNT(*) FROM projection_outbox o WHERE "
            "o.last_error='historical_event_only' AND o.status=? "
            "AND NOT EXISTS (SELECT 1 FROM projection_adoption_audit a "
            "WHERE a.projection_key=o.projection_key)",
            (JobStatus.SKIPPED.value,)).fetchone()[0],
        # ---- bất biến provenance (HBF-2.1) — mutant P1/P2 phải làm đỏ
        "terminal_migration_rows_with_lock": q(
            "SELECT COUNT(*) FROM projection_outbox o "
            "JOIN projection_adoption_audit a "
            "  ON a.projection_key=o.projection_key "
            "WHERE o.status IN (?,?) AND "
            "(o.locked_by IS NOT NULL OR o.locked_at IS NOT NULL)",
            (JobStatus.COMPLETED.value, JobStatus.SKIPPED.value)
        ).fetchone()[0],
        "curated_builder_version_checked_nonnull": q(
            "SELECT COUNT(*) FROM projection_adoption_audit WHERE "
            "contract_name='curated_seed_v1' AND "
            "builder_version_checked IS NOT NULL").fetchone()[0],
        "tombstone_provenance_claims": q(
            "SELECT COUNT(*) FROM projection_adoption_audit WHERE "
            "management_action='excluded_tombstoned' AND "
            "(contract_name IS NOT NULL OR contract_version IS NOT NULL "
            "OR builder_version_checked IS NOT NULL)").fetchone()[0],
        # Bất biến cho chính defect review đối kháng bắt được: KHÔNG audit
        # nào được khai "không có projection nào ở đây" về một event mà ký ức
        # đang nằm đó.
        "event_only_claim_with_materialized_memory": q(
            "SELECT COUNT(*) FROM projection_adoption_audit a WHERE "
            "a.management_action='skipped_event_only' AND EXISTS ("
            "SELECT 1 FROM memory_source_events s WHERE s.event_id=a.event_id)"
        ).fetchone()[0],
        "event_only_false_builder_claim": q(
            "SELECT COUNT(*) FROM projection_adoption_audit WHERE "
            "management_action='skipped_event_only' AND "
            "(contract_version IS NOT NULL "
            "OR builder_version_checked IS NOT NULL)").fetchone()[0],
        "migration_rows_total": q(
            "SELECT COUNT(*) FROM projection_outbox o "
            "JOIN projection_adoption_audit a "
            "  ON a.projection_key=o.projection_key").fetchone()[0],
    }
