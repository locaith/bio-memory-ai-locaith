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
        if _marker_only(content):
            # live pipeline đã ghi quyết định → không phải nợ lịch sử.
            d.cls = "ALREADY_MANAGED" if statuses else "EVENT_ONLY_SKIP"
            if d.cls == "EVENT_ONLY_SKIP":
                d.proof = "substantive_gate_v1"
            continue
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

        legacy = mem_by_event.get(eid, [])
        if not legacy:
            d.cls = "TRUE_MISSING"
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


def _triple_state(conn: sqlite3.Connection, key: str, eid: str) -> tuple[bool, bool, bool]:
    ob = conn.execute(
        "SELECT 1 FROM projection_outbox WHERE event_id=? AND "
        "projection_type=? AND projection_version=?",
        (eid, _PTYPE, spec(_PTYPE).version)).fetchone() is not None
    lg = conn.execute("SELECT 1 FROM projection_ledger WHERE projection_key=?",
                      (key,)).fetchone() is not None
    au = conn.execute(
        "SELECT 1 FROM projection_adoption_audit WHERE projection_key=?",
        (key,)).fetchone() is not None
    return ob, lg, au


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
                   proof: str | None, contract: str | None) -> None:
            conn.execute(
                "INSERT INTO projection_adoption_audit VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, d.event_id, d.target_id, origin, action, adopted_at,
                 migration_run_id, d.projection_hash,
                 d.canonical_candidate_hash, proof, contract,
                 1, d.observed_at_delta_ms, d.structured_content_status,
                 version, source_snapshot_sha256,
                 json.dumps(d.reasons, ensure_ascii=False)))
            result.audit_inserted += 1

        for d in report.rows:
            key = projection_key(d.event_id, _PTYPE, version)
            if d.cls in ADOPT_CLASSES:
                ob, lg, au = _triple_state(conn, key, d.event_id)
                if ob and lg and au:
                    result.noop_reapply += 1
                    continue
                if ob or lg or au:
                    raise AdmissibilityError(
                        f"partial triple tại {d.event_id[:8]}: "
                        f"outbox={ob} ledger={lg} audit={au}")
                conn.execute(
                    "INSERT INTO projection_outbox (job_id, event_id, "
                    "projection_type, projection_version, projection_key, "
                    "tenant_id, status, attempts, available_at, locked_by, "
                    "locked_at, last_error, payload_json, created_at, "
                    "completed_at) VALUES (?,?,?,?,?,?,?,0,?,?,NULL,NULL,"
                    "'{}',?,?)",
                    (str(uuid4()), d.event_id, _PTYPE, version, key,
                     d.tenant_id, JobStatus.COMPLETED.value, now,
                     MIGRATION_ACTOR, now, now))
                conn.execute(
                    "INSERT INTO projection_ledger (projection_key, event_id, "
                    "projection_type, projection_version, tenant_id, "
                    "target_id, worker_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (key, d.event_id, _PTYPE, version, d.tenant_id,
                     d.target_id, MIGRATION_ACTOR, now))
                result.outbox_inserted += 1
                result.ledger_inserted += 1
                _audit(key, d, "legacy_projection", "adopted", d.proof,
                       d.contract_name)
                result.adopted += 1
            elif d.cls == "EVENT_ONLY_SKIP":
                ob, _, au = _triple_state(conn, key, d.event_id)
                if ob and au:
                    result.noop_reapply += 1
                    continue
                if ob or au:
                    raise AdmissibilityError(
                        f"partial event-only tại {d.event_id[:8]}: "
                        f"outbox={ob} audit={au}")
                conn.execute(
                    "INSERT INTO projection_outbox (job_id, event_id, "
                    "projection_type, projection_version, projection_key, "
                    "tenant_id, status, attempts, available_at, locked_by, "
                    "locked_at, last_error, payload_json, created_at, "
                    "completed_at) VALUES (?,?,?,?,?,?,?,0,?,?,NULL,"
                    "'historical_event_only','{}',?,?)",
                    (str(uuid4()), d.event_id, _PTYPE, version, key,
                     d.tenant_id, JobStatus.SKIPPED.value, now,
                     MIGRATION_ACTOR, now, now))
                result.outbox_inserted += 1
                _audit(key, d, "historical_event_only", "skipped_event_only",
                       "substantive_gate_v1", None)
                result.skipped_event_only += 1
            elif d.cls == "TOMBSTONE_EXCLUDE":
                # KHÔNG outbox row — bia mộ là authority; audit chỉ ghi sổ.
                if conn.execute(
                        "SELECT 1 FROM projection_adoption_audit "
                        "WHERE projection_key=?", (key,)).fetchone():
                    result.noop_reapply += 1
                    continue
                _audit(key, d, "tombstoned", "excluded_tombstoned", None, None)
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
    """Không bộ ba nào được lệch — tất cả phải bằng 0."""
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
            "SELECT COUNT(*) FROM projection_outbox o WHERE o.locked_by=? "
            "AND o.status=? AND NOT EXISTS (SELECT 1 FROM projection_ledger "
            "l WHERE l.projection_key=o.projection_key)",
            (MIGRATION_ACTOR, JobStatus.COMPLETED.value)).fetchone()[0],
        "migration_outbox_skipped_without_audit": q(
            "SELECT COUNT(*) FROM projection_outbox o WHERE o.locked_by=? "
            "AND o.status=? AND NOT EXISTS (SELECT 1 FROM "
            "projection_adoption_audit a WHERE "
            "a.projection_key=o.projection_key)",
            (MIGRATION_ACTOR, JobStatus.SKIPPED.value)).fetchone()[0],
        "migration_rows_total": q(
            "SELECT COUNT(*) FROM projection_outbox WHERE locked_by=?",
            (MIGRATION_ACTOR,)).fetchone()[0],
    }
