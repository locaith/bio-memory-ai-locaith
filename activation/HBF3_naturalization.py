"""HBF-3 — REAL HISTORICAL NATURALIZATION. Lần DUY NHẤT chạm canonical thật.

    STALE BASELINE != SAFE REPLACEMENT.

Candidate có thể hoàn hảo, nhưng nếu một prompt mới lọt vào canonical sau lúc
snapshot thì install candidate sẽ XOÁ prompt đó khỏi tương lai. Vì vậy:
writer im từ TRƯỚC snapshot đến hết install, và ngay sát thời điểm replace
vẫn phải chứng minh source generation chưa đổi (CAS guard).

5 → 20 → 50 là MEASUREMENT ladder trên candidate, KHÔNG phải mutation ladder.
Một lần thay generation thật là đủ; bốn lần không mua thêm bằng chứng, chỉ
mua thêm bốn vé xổ số SQLite.

    python activation/HBF3_naturalization.py                 # PREFLIGHT (không mutation)
    python activation/HBF3_naturalization.py --go            # install THẬT, một lần
    python activation/HBF3_naturalization.py --verify-installed <db> <pre-snapshot>
    python activation/HBF3_naturalization.py --rollback      # cài lại snapshot pre-HBF3
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bio_agent_os.cognitive.historical_adoption import (          # noqa: E402
    ADOPT_CLASSES, MIGRATION_ACTOR, adopt, adoption_gate,
    adoption_invariants, classify_store, tables_digest, verify_closure,
    verify_closure_from_audit)
from bio_agent_os.cognitive.outbox import projection_key          # noqa: E402
from bio_agent_os.cognitive.projection_engine import (            # noqa: E402
    ProjectionReplayEngine)
from bio_agent_os.cognitive.projection_equivalence import compare  # noqa: E402
from bio_agent_os.cognitive.store_generation import (             # noqa: E402
    certify_candidate, install_generation)
from bio_agent_os.cognitive.tombstones import buried_among        # noqa: E402

PLATFORM = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh")
SETTINGS = PLATFORM / ".claude" / "settings.json"
REAL = PLATFORM / ".bio-agent-os" / "memory.db"
WORK = Path(__file__).parent / "HBF3"
SNAP = WORK / "pre_hbf3_source.db"          # ĐIỂM ROLLBACK, certify trước
CANDIDATE = WORK / "hbf3_candidate.db"
SLOTCHECK = WORK / "hbf3_slotcheck.db"
QUARANTINE = WORK / "quarantine"
SETTINGS_BACKUP = WORK / "settings.pre-hbf3.json"
CANARY_TAG = "HBF3-CANARY"

report: dict = {"phase": "HBF-3", "verdict": None,
                "real_store_migration": "NOT PERFORMED"}
_steps: list = []


def step(name: str, ok: bool, detail: str = "") -> None:
    _steps.append({"step": name, "ok": bool(ok), "detail": str(detail)[:400]})
    report["steps"] = _steps
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _flush()
        raise SystemExit(f"HBF-3 DỪNG tại: {name}")


def _flush() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "hbf3_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(src: Path, dest: Path) -> Path:
    """sqlite backup API — KHÔNG BAO GIỜ copy2 một db đang mở (bài học A5).

    Ghi qua file tạm rồi `os.replace`: bản đầu unlink dest TRƯỚC khi backup,
    nên trong lúc chụp lại, ĐIỂM ROLLBACK không tồn tại. Một cú crash đúng
    cửa sổ đó lấy mất đường lui."""
    tmp = Path(str(dest) + ".building")
    for p in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if p.exists():
            p.unlink()
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(tmp))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    for side in ("-wal", "-shm"):
        p = Path(str(dest) + side)
        if p.exists():
            p.unlink()
    os.replace(tmp, dest)
    return dest


def _open(path: Path, ro: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) if ro \
        else sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# đo lịch sử — định nghĩa "rebuild" và "duplicate" bằng quan hệ, không bằng cảm
# ---------------------------------------------------------------------------

#: Sổ sách TẦNG PHỤC VỤ, không phải nội dung ký ức: mỗi lần `recall()` chạy —
#: kể cả cú recall mà chính hook canary gọi để nạp ngữ cảnh — hai cột này đổi
#: trên các hàng được trả về. Đo được: 10 hàng đổi sau canary, và ĐÚNG hai cột
#: này, không cột nào khác. Cùng tập mà SP-1 parity gate đã loại trừ.
ACCESS_COLUMNS = ("retrieval_count", "last_accessed_at")


def _memory_rows(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """(vân tay NGỮ NGHĨA, vân tay sổ-sách-truy-xuất) — tách hẳn hai thứ."""
    semantic, access = {}, {}
    for r in conn.execute("SELECT * FROM cognitive_memories"):
        key = (str(r["memory_id"]), int(r["version"]))
        d = dict(r)
        acc = {c: d.pop(c, None) for c in ACCESS_COLUMNS}
        semantic[key] = hashlib.sha256(
            repr(sorted(d.items())).encode("utf-8", "replace")).hexdigest()
        access[key] = repr(sorted(acc.items()))
    return semantic, access


def _event_memories(conn: sqlite3.Connection) -> dict:
    out: dict = {}
    for r in conn.execute("SELECT event_id, memory_id FROM memory_source_events"):
        out.setdefault(str(r["event_id"]), set()).add(str(r["memory_id"]))
    return out


def historical_delta(pre: sqlite3.Connection, post: sqlite3.Connection,
                     *, exclude_events: set | None = None) -> dict:
    """Rebuild = ký ức MỚI gắn vào một event LỊCH SỬ. Duplicate = event lịch
    sử giờ trỏ ≥2 ký ức mà trước đó không. Cả hai phải bằng 0."""
    exclude = exclude_events or set()
    pre_rows, pre_acc = _memory_rows(pre)
    post_rows, post_acc = _memory_rows(post)
    pre_map, post_map = _event_memories(pre), _event_memories(post)
    # Population là MỌI event lịch sử, không chỉ event đã có ký ức. Lấy từ
    # `memory_source_events` sẽ mù đúng với nhóm nguy hiểm nhất: event chưa
    # materialize mà bỗng mọc ra một ký ức sau migration.
    hist = {str(r[0]) for r in pre.execute(
        "SELECT event_id FROM cognitive_events")} - exclude

    rebuilds, dups, mutated, vanished = [], [], [], []
    for eid in hist:
        before, after = pre_map.get(eid, set()), post_map.get(eid, set())
        for mid in after - before:
            rebuilds.append({"event": eid[:8], "memory": mid[:8]})
        if len(after) > 1 and len(before) <= 1:
            dups.append({"event": eid[:8], "n": len(after)})
    touched_access = 0
    for key, h in pre_rows.items():
        if key not in post_rows:
            vanished.append(key[0][:8])
            continue
        if post_rows[key] != h:
            mutated.append(key[0][:8])
        if pre_acc.get(key) != post_acc.get(key):
            touched_access += 1
    return {"historical_rebuilds": len(rebuilds),
            "historical_duplicate_memories": len(dups),
            "historical_rows_mutated": len(mutated),
            "historical_rows_vanished": len(vanished),
            # Không giấu: đếm và khai riêng. Đọc một ký ức là một sự kiện thật,
            # chỉ không phải một sự kiện NGỮ NGHĨA.
            "historical_rows_access_bookkeeping_only": touched_access,
            "witness": {"rebuilds": rebuilds[:5], "dups": dups[:5],
                        "mutated": mutated[:5], "vanished": vanished[:5]}}


def migration_rows_digest(conn: sqlite3.Connection) -> str:
    """Dấu vân tay của TOÀN BỘ hàng do migration đặt — để chứng minh canary
    không chạm vào lịch sử đã nhập tịch."""
    h = hashlib.sha256()
    for sql in (
        "SELECT * FROM projection_adoption_audit ORDER BY projection_key",
        "SELECT * FROM projection_ledger WHERE worker_id=? "
        "ORDER BY projection_key",
        "SELECT o.* FROM projection_outbox o JOIN projection_adoption_audit a "
        "ON a.projection_key=o.projection_key ORDER BY o.projection_key",
    ):
        args = (MIGRATION_ACTOR,) if "worker_id=?" in sql else ()
        for row in conn.execute(sql, args):
            h.update(repr(tuple(row)).encode("utf-8", "replace"))
    return h.hexdigest()


def source_generation(db: Path) -> dict:
    """Danh tính thế hệ của SOURCE — đủ để CAS guard nhìn thấy MỌI đường ghi.

    Hash file chính KHÔNG đủ: ở chế độ WAL, một prompt mới commit vào
    `memory.db-wal`, còn `memory.db` có thể không đổi một byte. Guard chỉ đo
    file chính sẽ báo "chưa ai ghi" rồi install candidate, và cú ghi đó bị
    chuyển sang quarantine — mất khỏi tương lai của store sống. Đúng họ
    STALE BASELINE != SAFE REPLACEMENT, chỉ khác chỗ nấp.

    Nên guard đo BA lớp: file chính · sidecar -wal · và số đếm LOGIC đọc qua
    chính SQLite (reader thấy cả WAL)."""
    out: dict = {"main_sha256": _sha(db)}
    wal = Path(str(db) + "-wal")
    out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
    out["wal_sha256"] = _sha(wal) if wal.exists() and out["wal_bytes"] else None
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        logical: dict = {}
        for table in ("cognitive_events", "cognitive_memories",
                      "projection_outbox", "projection_ledger",
                      "memory_tombstones", "memory_source_events"):
            try:
                logical[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                logical[table] = None
        logical["max_event_observed_at"] = conn.execute(
            "SELECT MAX(observed_at) FROM cognitive_events").fetchone()[0]
        logical["max_memory_created_at"] = conn.execute(
            "SELECT MAX(created_at) FROM cognitive_memories").fetchone()[0]
        out["logical"] = logical
    finally:
        conn.close()
    return out


def actionable_replay_debt(conn: sqlite3.Connection, *,
                           exclude_events: set | None = None):
    """(nợ actionable ngoài tập loại trừ, giữ-vì-bia-mộ, nợ CỦA tập loại trừ).

    Tách hẳn ba số: gộp chúng lại là cách một phép đo "toàn cục" bị gán nhãn
    "lịch sử" rồi đỏ vì lý do không thuộc về nó."""
    scan = ProjectionReplayEngine(conn).scan()
    ev = {c.event_id for c in scan.candidates}
    tombs = buried_among(conn, ev)
    excl = exclude_events or set()
    actionable = ev - tombs
    if exclude_events is None:
        return len(actionable), len(ev & tombs)
    return len(actionable - excl), len(ev & tombs), len(actionable & excl)


# ---------------------------------------------------------------------------
# S-ladder: audit TỪNG HÀNG trên candidate, KHÔNG install
# ---------------------------------------------------------------------------

def _sample(decisions: list, n: int) -> list:
    """Mẫu TẤT ĐỊNH và PHÂN TẦNG theo proof class.

    Bản đầu lấy stride trên danh sách sắp theo event_id — trải đều về vị trí
    nhưng KHÔNG đảm bảo phủ lớp: ADOPT_CURATED_PARTIAL chỉ có 9/267 hàng nên
    một mẫu 5 hoàn toàn có thể không chạm lớp nào ngoài FULL, rồi báo "S5
    PASS" về một thứ chưa hề đo. Ở đây mỗi lớp được bốc ít nhất một hàng
    trước, phần còn lại chia theo tỉ lệ."""
    by_cls: dict = {}
    for d in sorted(decisions, key=lambda x: x.event_id):
        by_cls.setdefault(d.cls, []).append(d)
    if n >= len(decisions):
        return sorted(decisions, key=lambda x: x.event_id)

    picked: list = []
    classes = sorted(by_cls)
    for cls in classes:                       # tối thiểu 1 hàng mỗi lớp
        picked.append(by_cls[cls][0])
    remaining = max(0, n - len(picked))
    total_rest = sum(len(v) - 1 for v in by_cls.values()) or 1
    for cls in classes:
        pool = by_cls[cls][1:]
        if not pool or remaining <= 0:
            continue
        take = min(len(pool), max(0, round(remaining * len(pool) / total_rest)))
        stride = len(pool) / take if take else 1
        picked.extend(pool[int(i * stride)] for i in range(take))
    # Làm tròn theo tỉ lệ có thể để hụt so với n. Bù cho đủ, tất định —
    # một mẫu "S20" chỉ đo 19 hàng là đã âm thầm cắt cỡ mẫu.
    if len(picked) < n:
        seen = {id(d) for d in picked}
        for d in sorted(decisions, key=lambda x: x.event_id):
            if len(picked) >= n:
                break
            if id(d) not in seen:
                picked.append(d)
    return picked[:max(n, len(classes))]


def audit_rows(cand: sqlite3.Connection, src: sqlite3.Connection,
               decisions: list, n: int) -> dict:
    """Mỗi hàng mẫu phải tự chứng minh SÁU điều, không chỉ 'có tồn tại'."""
    version = 1
    problems: list = []
    checked = 0
    for d in _sample(decisions, n):
        checked += 1
        key = projection_key(d.event_id, "cognitive_memory", version)
        why: list = []

        # 1. ký ức KHÔNG bị sửa: hàng trong candidate byte-identical với nguồn
        a = src.execute("SELECT * FROM cognitive_memories WHERE memory_id=?",
                        (d.target_id,)).fetchone()
        b = cand.execute("SELECT * FROM cognitive_memories WHERE memory_id=?",
                         (d.target_id,)).fetchone()
        if a is None or b is None or tuple(a) != tuple(b):
            why.append("cognitive_memories row bị đổi trong candidate")

        # 2. ledger trỏ ĐÚNG ký ức, đúng chủ
        lg = cand.execute(
            "SELECT target_id, worker_id, tenant_id FROM projection_ledger "
            "WHERE projection_key=?", (key,)).fetchone()
        if lg is None:
            why.append("thiếu ledger")
        else:
            if str(lg["target_id"]) != str(d.target_id):
                why.append("ledger.target_id trỏ sai ký ức")
            if str(lg["worker_id"]) != MIGRATION_ACTOR:
                why.append(f"ledger.worker_id={lg['worker_id']!r}")

        # 3. outbox terminal ĐÚNG hình dạng complete(): không lease sót
        ob = cand.execute(
            "SELECT status, locked_by, locked_at, projection_key FROM "
            "projection_outbox WHERE event_id=? AND projection_type=?",
            (d.event_id, "cognitive_memory")).fetchone()
        if ob is None:
            why.append("thiếu outbox")
        else:
            if str(ob["status"]) != "completed":
                why.append(f"outbox.status={ob['status']!r}")
            if ob["locked_by"] is not None or ob["locked_at"] is not None:
                why.append("outbox terminal còn mang lease")
            if str(ob["projection_key"]) != key:
                why.append("outbox.projection_key lệch")

        # 4. audit mang ĐÚNG proof của lớp, và không khai builder khi không có
        au = cand.execute(
            "SELECT equivalence_proof, contract_name, builder_version_checked,"
            " observed_at_delta_ms, management_action FROM "
            "projection_adoption_audit WHERE projection_key=?", (key,)
        ).fetchone()
        if au is None:
            why.append("thiếu audit")
        else:
            if str(au["equivalence_proof"]) != str(d.proof):
                why.append(f"proof={au['equivalence_proof']!r} != {d.proof!r}")
            if str(au["management_action"]) != "adopted":
                why.append("audit.action != adopted")
            curated = d.contract_name == "curated_seed_v1"
            if curated and au["builder_version_checked"] is not None:
                why.append("curated khai builder chưa từng chạy")
            if not curated and au["builder_version_checked"] is None:
                why.append("hook adopt thiếu builder_version_checked")
            if "except_observed_at" in str(d.proof) and \
                    au["observed_at_delta_ms"] is None:
                why.append("proof except_observed_at mà không có delta thật")

        # 5. comparator chạy LẠI trên chính cặp trong candidate
        ev = cand.execute("SELECT * FROM cognitive_events WHERE event_id=?",
                          (d.event_id,)).fetchone()
        if ev is not None and b is not None:
            v = compare(dict(ev), dict(b))
            if d.cls in ("ADOPT_FULL_CONTRACT", "ADOPT_CURATED_PRESERVED"):
                if not v.equivalent:
                    why.append(f"comparator đổi ý: {v.reasons[:1]}")
            elif not all(r.startswith("observed_at:") for r in v.reasons):
                why.append(f"partial nhưng lệch ngoài observed_at: {v.reasons[:1]}")

        # 6. event bất biến: payload/checksum không đổi
        ea = src.execute("SELECT * FROM cognitive_events WHERE event_id=?",
                         (d.event_id,)).fetchone()
        if ea is None or ev is None or tuple(ea) != tuple(ev):
            why.append("cognitive_events row bị đổi")

        if why:
            problems.append({"event": d.event_id[:8], "cls": d.cls, "why": why})
    return {"sampled": checked, "problems": problems}


# ---------------------------------------------------------------------------
# quiesce / unquiesce
# ---------------------------------------------------------------------------

def quiesce() -> dict:
    """Gỡ mọi hook ghi vào memory.db khỏi deployed settings. Đây là hàng rào
    THỨ NHẤT; hàng rào thật là CAS guard ngay trước install."""
    WORK.mkdir(parents=True, exist_ok=True)
    raw = SETTINGS.read_text(encoding="utf-8")
    # KHÔNG ghi đè backup: chạy lần hai khi settings đang ở trạng thái quiesced
    # sẽ lấy chính bản quiesced làm "bản gốc" và hook không bao giờ trở lại.
    if not SETTINGS_BACKUP.exists():
        SETTINGS_BACKUP.write_text(raw, encoding="utf-8")
    elif "hook_cli" not in SETTINGS_BACKUP.read_text(encoding="utf-8"):
        raise SystemExit("backup settings hiện có KHÔNG chứa hook_cli — "
                         "nó không phải bản gốc. Dừng để người kiểm.")
    cfg = json.loads(raw)
    removed = []
    for event, groups in list(cfg.get("hooks", {}).items()):
        kept_groups = []
        for g in groups:
            inner = [h for h in g.get("hooks", [])
                     if "hook_cli" not in str(h.get("command", ""))]
            if len(inner) != len(g.get("hooks", [])):
                removed.append(event)
            if inner:
                kept_groups.append({**g, "hooks": inner})
        cfg["hooks"][event] = kept_groups
    SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return {"quiesced_hooks": sorted(set(removed)),
            "backup": str(SETTINGS_BACKUP),
            "residual_hook_cli_refs": SETTINGS.read_text(
                encoding="utf-8").count("hook_cli")}


def unquiesce() -> bool:
    if not SETTINGS_BACKUP.exists():
        return False
    SETTINGS.write_text(SETTINGS_BACKUP.read_text(encoding="utf-8"),
                        encoding="utf-8")
    return "hook_cli" in SETTINGS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fresh-process certification (chạy trong process RIÊNG, chỉ đọc)
# ---------------------------------------------------------------------------

def verify_installed(db: str, pre: str) -> None:
    p, q = Path(db), Path(pre)
    out: dict = {"file_sha256": _sha(p)}
    conn = _open(p, ro=True)
    pre_conn = _open(q, ro=True)
    try:
        out["integrity"] = [r[0] for r in conn.execute("PRAGMA integrity_check")][:1]
        out["fk_violations"] = len(list(conn.execute("PRAGMA foreign_key_check")))
        out["invariants"] = adoption_invariants(conn)
        out["audit_rows"] = {r[0]: r[1] for r in conn.execute(
            "SELECT management_action, COUNT(*) FROM projection_adoption_audit "
            "GROUP BY management_action")}
        fresh = classify_store(conn)
        out["remaining_adopt_classes"] = {k: fresh.counts[k]
                                          for k in ADOPT_CLASSES
                                          if fresh.counts[k]}
        # Population của closure là SỔ AUDIT (307 hàng), không phải phân loại
        # tươi — sau khi cài, phân loại tươi chỉ còn 2 hàng bia mộ để soi và
        # "2/2 closed" là màu xanh gần như rỗng.
        closure = verify_closure_from_audit(conn)
        out["closure"] = {"checked": closure["checked"],
                          "closed": closure["closed"]}
        out["closure_violations"] = closure["not_closed"][:5]
        # Tiêu chí là "HISTORICAL actionable replay debt = 0". Population của
        # nó là event có trong snapshot pre-HBF3; event sinh SAU install
        # (canary) thuộc về pipeline sống, phải đếm riêng — gộp lại là dán
        # nhãn "lịch sử" lên một phép đo toàn cục.
        pre_ids = {str(r[0]) for r in pre_conn.execute(
            "SELECT event_id FROM cognitive_events")}
        post_ids = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM cognitive_events")}
        debt, held, own = actionable_replay_debt(
            conn, exclude_events=post_ids - pre_ids)
        out["actionable_replay_debt"] = debt              # lịch sử
        out["replay_held_tombstoned"] = held
        out["post_install_debt_from_new_events"] = own
        out["events_written_since_install"] = len(post_ids - pre_ids)
        out["migration_rows_digest"] = migration_rows_digest(conn)
        out.update(historical_delta(pre_conn, conn))
    finally:
        conn.close()
        pre_conn.close()
    wal = Path(str(p) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        out["stale_wal_bytes"] = wal.stat().st_size
    print(json.dumps(out, ensure_ascii=False, default=str))


def _fresh_verify(db: Path, pre: Path) -> dict:
    # KHÔNG dùng text=True: con in JSON UTF-8 (lý do vắng `ensure_ascii`),
    # còn text=True decode bằng codepage hệ thống — cùng đúng cái bug mà
    # hook_cli đã trả học phí. Đọc bytes, tự decode utf-8.
    r = subprocess.run([sys.executable, __file__, "--verify-installed",
                        str(db), str(pre)], capture_output=True, timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"fresh verify lỗi: "
                         f"{r.stderr.decode('utf-8', 'replace')[-1500:]}")
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

def assert_pre_migration(db: Path, what: str) -> None:
    """Một 'điểm rollback' đã mang bảng adoption thì KHÔNG phải điểm rollback.

    Chạy lại `--go` sau khi install thành công sẽ chụp lại canonical ĐÃ
    migrate rồi ghi đè lên SNAP — và `--rollback` sau đó sẽ 'khôi phục' về
    đúng trạng thái đang muốn rời bỏ. Chốt ở cả hai đầu."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        has = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND "
            "name='projection_adoption_audit'").fetchone()[0]
        rows = conn.execute(
            "SELECT COUNT(*) FROM projection_adoption_audit").fetchone()[0] \
            if has else 0
    finally:
        conn.close()
    if rows:
        raise SystemExit(
            f"{what} đã mang {rows} hàng adoption — KHÔNG phải trạng thái "
            f"pre-HBF3. Dừng: một migration chỉ được chạy một lần, và điểm "
            f"rollback phải là trước nó.")


def rollback() -> None:
    print("HBF-3 ROLLBACK — cài lại snapshot pre-HBF3 bằng generation "
          "replacement (KHÔNG ghi đè file canonical đang sống)")
    if not SNAP.exists():
        raise SystemExit("không có snapshot pre-HBF3 — dừng")
    assert_pre_migration(SNAP, "snapshot rollback")
    sha = certify_candidate(SNAP)
    print(f"  pre-HBF3 snapshot certified {sha[:16]}")
    manifest = install_generation(REAL, SNAP, QUARANTINE / "rollback")
    print(f"  INSTALL_CONFIRMED {manifest['installed_sha256'][:16]}")
    conn = _open(REAL, ro=True)
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check")][:1]
    fk = len(list(conn.execute("PRAGMA foreign_key_check")))
    conn.close()
    print(f"  integrity={ic} fk={fk} — hook GIỮ NGUYÊN TRẠNG THÁI TẮT. STOP.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------



def post_install_phase(cand_sha: str, snap_sha: str, t0: float) -> None:
    """Mọi bước SAU lần install duy nhất.

    Tách riêng để có thể chạy tiếp mà KHÔNG phải thay generation thêm lần
    nữa: nếu một phép kiểm hậu-install đỏ vì chính bộ kiểm sai (chứ không
    phải vì dữ liệu sai), rollback rồi cài lại sẽ là BA lần chạm canonical
    thay vì một — thêm rủi ro mà không mua thêm một bit thông tin nhân quả."""
    # ---- HBF3-9. FRESH-PROCESS CERTIFICATION
    fresh = _fresh_verify(REAL, SNAP)
    report["fresh_process_certification"] = fresh
    # Hash phải khớp candidate CHỈ KHI chưa ai ghi gì sau install. Nếu canary
    # (hoặc một lần chạy trước) đã ghi hợp lệ thì byte khác là đúng, và thứ
    # phải chứng minh là hiện vật migration + lịch sử còn nguyên — do chính
    # các điều kiện bên dưới gánh.
    ok = ((fresh["file_sha256"] == cand_sha
           or fresh["events_written_since_install"] > 0)
          and fresh["integrity"] == ["ok"] and fresh["fk_violations"] == 0
          and not fresh["remaining_adopt_classes"]
          and not fresh["closure_violations"]
          and fresh["actionable_replay_debt"] == 0
          and fresh["historical_rebuilds"] == 0
          and fresh["historical_duplicate_memories"] == 0
          and fresh["historical_rows_mutated"] == 0
          and fresh["historical_rows_vanished"] == 0
          and "stale_wal_bytes" not in fresh
          and all(v == 0 for k, v in fresh["invariants"].items()
                  if k != "migration_rows_total"))
    step("fresh-process certification", ok,
         f"audit={fresh['audit_rows']} · closure="
         f"{fresh['closure']['closed']}/{fresh['closure']['checked']} "
         f"· debt={fresh['actionable_replay_debt']}")
    migration_digest_pre_canary = fresh["migration_rows_digest"]

    # ---- HBF3-10. CONTROLLED DEPLOYED-HOOK CANARY — VẪN ĐANG QUIESCED.
    #
    # Canary gọi THẲNG lệnh hook và env lấy từ backup settings (bản đã
    # deploy), nên nó vẫn đi đúng đường production; nhưng session hook giữ
    # im, nên không có writer thứ hai chen vào giữa hai lần đo. Bản đầu
    # bật hook lại TRƯỚC canary — đo một store đa-writer rồi gán mọi thay
    # đổi cho canary. Hook chỉ được bật lại ở bước cuối cùng.
    cfg = json.loads(SETTINGS_BACKUP.read_text(encoding="utf-8"))
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)
    env.update(cfg.get("env", {}))
    hook_cmd = None
    for g in cfg.get("hooks", {}).get("UserPromptSubmit", []):
        for h in g.get("hooks", []):
            if "hook_cli" in str(h.get("command", "")):
                hook_cmd = h["command"]
    exe = hook_cmd.split('"')[1]
    base_args = hook_cmd.split('"')[2].strip().split()[:-1]   # bỏ tên hook

    def run_hook(hook: str, payload: dict) -> None:
        rr = subprocess.run([exe, *base_args, hook],
                            input=json.dumps(payload).encode(),
                            capture_output=True, env=env,
                            cwd=str(PLATFORM), timeout=180)
        if rr.returncode != 0:
            raise SystemExit(f"hook {hook} exit {rr.returncode}: "
                             f"{rr.stderr[-400:]}")

    canary: dict = {"declared_mode_from_settings":
                    cfg["env"]["BIO_AGENT_PROJECTION_MODE"]}

    # RESET TRƯỚC CANARY (tiền lệ A5: "clean generation reset before deployed
    # acceptance"). Một lần chạy hỏng trước đó có thể để lại ký ức canary;
    # chạy canary chồng lên dấu vết cũ là đo một tập không thuần.
    from bio_agent_os.cognitive.facade import MemoryOS as _MOS
    from bio_agent_os.cognitive.forgetting import forget_derived as _forget
    cr = _open(REAL, ro=True)
    leftovers = [str(r[0]) for r in cr.execute(
        "SELECT memory_id FROM cognitive_memories WHERE content LIKE ?",
        (f"%{CANARY_TAG}%",))]
    cr.close()
    if leftovers:
        _m = _MOS(REAL, projection_mode="outbox")
        try:
            for mid in leftovers:
                _forget(_m, memory_id=mid, needle=CANARY_TAG)
        finally:
            _m.close()
    cr = _open(REAL, ro=True)
    still = cr.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{CANARY_TAG}%",)).fetchone()[0]
    cr.close()
    canary["pre_canary_reset"] = {"leftover_memories_forgotten": len(leftovers),
                                  "remaining": still}
    step("reset sạch trước canary (dấu vết lần chạy trước, nếu có)",
         still == 0, f"quên {len(leftovers)} ký ức canary cũ, còn {still}")

    # Baseline TRƯỚC canary. Non-substantive phải đo bằng DELTA: store
    # thật đã có sẵn memory `hook=SessionStart` từ thời legacy, nên
    # "đếm tổng = 0" sẽ đỏ vì lý do sai — đúng bài học live-canary A5.
    cb = _open(REAL, ro=True)
    before_counts = {
        "events": cb.execute(
            "SELECT COUNT(*) FROM cognitive_events").fetchone()[0],
        "memories": cb.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0],
        "sessionstart_memories": cb.execute(
            "SELECT COUNT(*) FROM cognitive_memories WHERE "
            "content='hook=SessionStart'").fetchone()[0]}
    # Tập event TRƯỚC canary — để sau này "cái gì là của canary" là một phép
    # trừ tập hợp, không phải một phép đoán qua marker. Marker không có mặt
    # trong event non-substantive (`hook=SessionStart`), nên lọc theo marker
    # sẽ bỏ sót đúng hàng dễ gây hiểu nhầm nhất.
    before_event_ids = {str(r[0]) for r in cb.execute(
        "SELECT event_id FROM cognitive_events")}
    cb.close()

    run_hook("UserPromptSubmit", {
        "hook_event_name": "UserPromptSubmit", "session_id": "hbf3",
        "cwd": str(PLATFORM),
        "prompt": f"{CANARY_TAG}-UPS: nhập tịch xong, kiểm đường ghi mới."})
    # `error` là CONTENT_KEY cấp cao nhất — nhét vào `tool_response` thì
    # renderer không đọc, và marker sẽ vắng mặt trong chính thứ ta đo
    # (diễn tập trên clone bắt đúng chỗ này).
    run_hook("PostToolUseFailure", {
        "hook_event_name": "PostToolUseFailure", "session_id": "hbf3",
        "cwd": str(PLATFORM), "tool_name": "Bash",
        "tool_input": {"command": "false"},
        "error": f"{CANARY_TAG}-PTF: lỗi giả lập để kiểm hợp đồng "
                 f"salience của đường ghi mới."})
    run_hook("SessionStart", {"hook_event_name": "SessionStart",
                              "session_id": "hbf3", "cwd": str(PLATFORM)})

    conn = _open(REAL, ro=True)
    try:
        fresh_ids = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM cognitive_events")} - before_event_ids

        def one(marker: str) -> dict:
            """Đo TRÊN CHÍNH event của lần chạy này.

            Bản đầu lọc theo marker toàn store, nên một lần chạy hỏng để lại
            dấu vết là phép đếm "phải bằng 1" tự động sai — vị từ đúng, tập
            sai, lần nữa."""
            ev = [str(r[0]) for r in conn.execute(
                "SELECT event_id FROM cognitive_events WHERE payload_json "
                "LIKE ?", (f"%{marker}%",)) if str(r[0]) in fresh_ids]
            if not ev:
                return {"events": 0, "memories": 0, "jobs": [], "ledger": 0,
                        "rows": []}
            q = ",".join("?" * len(ev))
            mem = conn.execute(
                f"SELECT m.memory_id, m.confidence, m.importance, m.salience, "
                f"m.utility, m.metadata_json FROM cognitive_memories m "
                f"JOIN memory_source_events s ON s.memory_id = m.memory_id "
                f"WHERE s.event_id IN ({q})", ev).fetchall()
            jobs = conn.execute(
                f"SELECT status FROM projection_outbox WHERE event_id IN ({q})",
                ev).fetchall()
            led = conn.execute(
                f"SELECT target_id FROM projection_ledger WHERE "
                f"event_id IN ({q})", ev).fetchall()
            return {"events": len(ev), "memories": len(mem),
                    "jobs": [str(j[0]) for j in jobs],
                    "ledger": len(led),
                    "rows": [dict(m) for m in mem]}

        ups = one(f"{CANARY_TAG}-UPS")
        ptf = one(f"{CANARY_TAG}-PTF")
        after_counts = {
            "events": conn.execute(
                "SELECT COUNT(*) FROM cognitive_events").fetchone()[0],
            "memories": conn.execute(
                "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0],
            "sessionstart_memories": conn.execute(
                "SELECT COUNT(*) FROM cognitive_memories WHERE "
                "content='hook=SessionStart'").fetchone()[0]}
        delta = {k: after_counts[k] - before_counts[k]
                 for k in before_counts}
        canary["substantive"] = {"UserPromptSubmit": ups,
                                 "PostToolUseFailure": ptf}
        canary["delta"] = delta
        canary["non_substantive"] = {
            "rule": "3 hook → +3 event; chỉ 2 substantive → +2 memory; "
                    "marker SessionStart → +0 memory (đo DELTA, vì store "
                    "thật đã có sẵn memory marker từ thời legacy)",
            "baseline_sessionstart_memories":
                before_counts["sessionstart_memories"]}
        non_mem = delta["sessionstart_memories"]
        ok_sub = all(
            x["events"] == 1 and x["memories"] == 1 and x["ledger"] == 1
            and x["jobs"] == ["completed"] for x in (ups, ptf))
        step("canary substantive: 1 event · 1 completed job · 1 ledger "
             "· 1 memory · 0 duplicate", ok_sub,
             f"UPS={ups['jobs']}/{ups['memories']} "
             f"PTF={ptf['jobs']}/{ptf['memories']}")

        u, p = ups["rows"][0], ptf["rows"][0]
        parity = (abs(u["confidence"] - 0.72) < 1e-9
                  and abs(u["importance"] - 0.55) < 1e-9
                  and abs(u["salience"] - 0.50) < 1e-9
                  and abs(p["salience"] - 0.75) < 1e-9
                  and abs(p["utility"] - 0.65) < 1e-9
                  and "state" in json.loads(p["metadata_json"] or "{}"))
        canary["contract_parity"] = {
            "ups": {k: u[k] for k in ("confidence", "importance",
                                      "salience", "utility")},
            "ptf": {k: p[k] for k in ("confidence", "importance",
                                      "salience", "utility")}}
        step("canary semantic contract parity theo call-site", parity,
             f"UPS sal={u['salience']} · PTF sal={p['salience']}")
        step("canary non-substantive: +1 event, +0 memory (đo DELTA)",
             non_mem == 0 and delta["events"] == 3
             and delta["memories"] == 2,
             f"Δevents={delta['events']} Δmemories={delta['memories']} "
             f"Δmarker_memories={non_mem}")

        digest_now = migration_rows_digest(conn)
        step("historical migration rows KHÔNG đổi sau canary",
             digest_now == migration_digest_pre_canary,
             f"{digest_now[:16]}")
        canary_event_ids = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM cognitive_events")} - before_event_ids
        # "LỊCH SỬ" chỉ có MỘT định nghĩa: có mặt trong snapshot pre-HBF3.
        # Loại trừ theo "event của lần chạy này" sẽ tính event canary của một
        # lần chạy TRƯỚC thành lịch sử — vẫn là vị từ đúng trên tập sai.
        _ps = _open(SNAP, ro=True)
        try:
            pre_snapshot_ids = {str(r[0]) for r in _ps.execute(
                "SELECT event_id FROM cognitive_events")}
        finally:
            _ps.close()
        post_install_ids = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM cognitive_events")} - pre_snapshot_ids
        debt2, held2, own = actionable_replay_debt(
            conn, exclude_events=post_install_ids)
        canary["canary_event_ids"] = sorted(e[:8] for e in canary_event_ids)
        canary["own_debt_from_non_substantive"] = own
        step("historical actionable replay debt vẫn = 0 (trừ event canary)",
             debt2 == 0 and len(canary_event_ids) == 3,
             f"historical={debt2} · held={held2} · canary sinh "
             f"{len(canary_event_ids)} event, trong đó {own} không có outbox "
             f"row (non-substantive — xem FINDING trong report)")
        # Mỗi ký ức đi kèm NEEDLE RIÊNG. Dùng chung một needle cho cả hai
        # thì lúc quên cái thứ nhất, verifier vẫn thấy chuỗi đó trong cái
        # thứ hai và báo "chưa sạch" — verifier đúng, câu hỏi sai.
        canary_ids = []
        for r in conn.execute(
                "SELECT memory_id, content FROM cognitive_memories "
                "WHERE content LIKE ?", (f"%{CANARY_TAG}%",)):
            ct = str(r["content"])
            needle = next((f"{CANARY_TAG}-{k}" for k in ("UPS", "PTF")
                           if f"{CANARY_TAG}-{k}" in ct), CANARY_TAG)
            canary_ids.append((str(r["memory_id"]), needle))
    finally:
        conn.close()
    report["canary"] = canary

    # ---- HBF3-11. QUÊN CHÍNH THỨC canary, GIỮ bằng chứng bia mộ
    from bio_agent_os.cognitive.facade import MemoryOS as MOS
    from bio_agent_os.cognitive.forgetting import forget_derived
    mos = MOS(REAL, projection_mode="outbox")
    forgot = []
    try:
        for mid, needle in canary_ids:
            rep_f = forget_derived(mos, memory_id=mid, needle=needle)
            forgot.append({"memory": mid[:8], "needle": needle,
                           "verified_clean": getattr(rep_f, "verified_clean",
                                                     None)})
    finally:
        mos.close()
    # `verified_clean` được thu vào report thì phải được KHẲNG ĐỊNH, nếu
    # không nó chỉ là số trang trí.
    step("forget tự xác minh sạch trên từng ký ức canary",
         all(f["verified_clean"] for f in forgot) if forgot else False,
         str([f["verified_clean"] for f in forgot]))
    conn = _open(REAL, ro=True)
    try:
        left = conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
            (f"%{CANARY_TAG}%",)).fetchone()[0]
        tombs = conn.execute(
            "SELECT COUNT(*) FROM memory_tombstones WHERE memory_id IN "
            "({})".format(",".join("?" * len(canary_ids))),
            [m for m, _n in canary_ids]).fetchone()[0] if canary_ids else 0
        digest_after_forget = migration_rows_digest(conn)
        debt3, _, own3 = actionable_replay_debt(
            conn, exclude_events=post_install_ids | canary_event_ids)
    finally:
        conn.close()
    report["canary_cleanup"] = {"forgotten": forgot,
                                "memories_left": left,
                                "tombstones_kept": tombs,
                                "actionable_debt_after": debt3}
    step("canary được QUÊN chính thức, bia mộ + audit giữ lại",
         left == 0 and tombs >= len(canary_ids) and debt3 == 0,
         f"còn {left} ký ức · {tombs} bia mộ · nợ lịch sử={debt3}")
    step("migration rows vẫn nguyên sau forget",
         digest_after_forget == migration_digest_pre_canary,
         digest_after_forget[:16])

    # Guard ngữ nghĩa CUỐI: canary + forget không được chạm một hàng lịch
    # sử nào. So với chính snapshot pre-HBF3, loại trừ event của canary.
    pre_conn, post_conn = _open(SNAP, ro=True), _open(REAL, ro=True)
    try:
        # Dùng ĐÚNG tập canary đã đo bằng phép trừ tập hợp — lọc theo
        # marker sẽ bỏ sót event non-substantive (không mang marker nào).
        final_hd = historical_delta(pre_conn, post_conn,
                                    exclude_events=canary_event_ids)
        final_closure = verify_closure_from_audit(post_conn)
    finally:
        pre_conn.close()
        post_conn.close()
    report["post_canary_historical_guard"] = final_hd
    report["post_canary_closure"] = {
        "checked": final_closure["checked"],
        "closed": final_closure["closed"],
        "not_closed": final_closure["not_closed"][:5]}
    step("guard ngữ nghĩa CUỐI: lịch sử nguyên vẹn sau canary+forget",
         final_hd["historical_rebuilds"] == 0
         and final_hd["historical_duplicate_memories"] == 0
         and final_hd["historical_rows_mutated"] == 0
         and final_hd["historical_rows_vanished"] == 0
         and not final_closure["not_closed"],
         f"rebuild={final_hd['historical_rebuilds']} "
         f"mutated={final_hd['historical_rows_mutated']} "
         f"closure={final_closure['closed']}/{final_closure['checked']}")

    # ---- HBF3-12. BẬT LẠI HOOK — bước CUỐI CÙNG, sau khi mọi phép đo xong
    restored = unquiesce()
    step("hook restored từ backup byte-exact (bước cuối)", restored,
         "settings.json có lại hook_cli")

    report["verdict"] = "HBF-3 REAL NATURALIZATION VERIFIED"
    report["elapsed_s"] = round(time.time() - t0, 1)
    _flush()
    print(f"\nHBF-3 REAL NATURALIZATION VERIFIED — {report['elapsed_s']}s")


def main(go: bool) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    report["mode"] = "GO — install thật" if go else "PREFLIGHT — không mutation"
    print(f"HBF-3 {report['mode']}\n")

    # ---- HBF3-0. CLOSURE LAW: chạy chính suite dao trước khi đụng gì
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_historical_adoption.py",
         "-q"], capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent.parent), timeout=900)
    passed = "failed" not in r.stdout.lower() and r.returncode == 0
    report["hbf3_0_closure_law"] = r.stdout.strip().splitlines()[-1] if r.stdout else ""
    step("HBF3-0 closure law (PRESENT != CLOSED) + 14 dao", passed,
         report["hbf3_0_closure_law"])

    # ---- HBF3-1. QUIESCE WRITERS (trước snapshot)
    if go:
        q = quiesce()
        report["quiesce"] = q
        step("writers quiesced — hook ghi memory.db đã gỡ khỏi settings",
             q["residual_hook_cli_refs"] == 0,
             f"gỡ {q['quiesced_hooks']}, còn {q['residual_hook_cli_refs']} tham chiếu")
    else:
        report["quiesce"] = "PREFLIGHT — không đụng settings"
        step("writers quiesced", True, "preflight: bỏ qua, không mutation")

    try:
        # ---- HBF3-2. FRESH SOURCE SNAPSHOT + CERTIFY (điểm rollback)
        # Migration này chạy MỘT lần. Nếu canonical đã mang hàng adoption thì
        # dừng ngay — đừng chụp nó làm "điểm rollback pre-HBF3".
        assert_pre_migration(REAL, "canonical thật")
        gen_at_snapshot = source_generation(REAL)
        _copy(REAL, SNAP)
        snap_sha = certify_candidate(SNAP)
        report["source_snapshot_sha256"] = snap_sha
        report["source_generation_at_snapshot"] = gen_at_snapshot
        step("fresh source snapshot certified (= điểm rollback)", True,
             f"snapshot {snap_sha[:16]} · canonical "
             f"{gen_at_snapshot['main_sha256'][:16]} · wal "
             f"{gen_at_snapshot['wal_bytes']}B · events "
             f"{gen_at_snapshot['logical']['cognitive_events']}")

        # ---- HBF3-3. POPULATION ADMISSIBLE + structured closure
        from bio_agent_os.cognitive.facade import MemoryOS
        _copy(SNAP, SLOTCHECK)
        mos = MemoryOS(SLOTCHECK, projection_mode="outbox")
        try:
            src_ro = _open(SNAP, ro=True)
            rep = classify_store(src_ro, slot_fn=lambda c, s:
                                 mos._structured_slot(c, s))
            src_ro.close()
        finally:
            mos.close()
        counts = {k: v for k, v in rep.counts.items() if v}
        closure_status: dict = {}
        proofs: dict = {}
        for d in rep.rows:
            if d.cls in ADOPT_CLASSES:
                closure_status[d.structured_content_status] = \
                    closure_status.get(d.structured_content_status, 0) + 1
                proofs[d.proof] = proofs.get(d.proof, 0) + 1
        report["fresh_population"] = {"total_events": rep.total_events,
                                      "counts": counts}
        report["structured_content_closure"] = closure_status
        report["proof_class_counts"] = proofs
        report["drift_stats"] = rep.drift_stats
        adoption_gate(rep)
        adopts = [d for d in rep.rows if d.cls in ADOPT_CLASSES]
        step("fresh population admissible", True,
             f"{rep.total_events} events · adopt {len(adopts)} · {counts}")

        # ---- HBF3-4. CANDIDATE OFFLINE + adopt + semantic delta 0 + closure
        _copy(SNAP, CANDIDATE)
        cand = _open(CANDIDATE)
        sem_before = tables_digest(cand)
        run_id = f"hbf3-{int(t0)}"
        result = adopt(cand, rep, migration_run_id=run_id,
                       source_snapshot_sha256=snap_sha)
        sem_after = tables_digest(cand)
        cand.commit()
        report["migration_transaction"] = {
            "migration_run_id": run_id, "adopted": result.adopted,
            "event_only_skipped": result.skipped_event_only,
            "tombstone_excluded": result.excluded_tombstoned,
            "outbox_inserted": result.outbox_inserted,
            "ledger_inserted": result.ledger_inserted,
            "audit_inserted": result.audit_inserted}
        step("offline candidate semantic delta = 0", sem_before == sem_after,
             f"adopt {result.adopted} · skip {result.skipped_event_only} "
             f"· tombstone {result.excluded_tombstoned}")

        inv = adoption_invariants(cand)
        report["candidate_invariants"] = inv
        step("candidate invariants (gồm 4 bất biến provenance)",
             all(v == 0 for k, v in inv.items() if k != "migration_rows_total"),
             f"terminal_lock={inv['terminal_migration_rows_with_lock']} "
             f"curated_builder={inv['curated_builder_version_checked_nonnull']}")

        # ---- HBF3-5. S5 → S20 → S50 MEASUREMENT LADDER (trên candidate)
        src_ro = _open(SNAP, ro=True)
        ladder: dict = {}
        for n in (5, 20, 50):
            res = audit_rows(cand, src_ro, adopts, n)
            ladder[f"S{n}"] = res
            step(f"S{n} sampled pre-install audit (6 phép/hàng)",
                 not res["problems"],
                 f"{res['sampled']} hàng · vi phạm {len(res['problems'])}"
                 + (f" · {res['problems'][:1]}" if res["problems"] else ""))
        report["measurement_ladder"] = {
            k: {"sampled": v["sampled"], "problems": v["problems"]}
            for k, v in ladder.items()}

        # ---- HBF3-6. WHOLE-POPULATION CLOSURE
        whole = verify_closure(cand, rep)
        report["whole_population_closure"] = {
            "checked": whole["checked"], "closed": whole["closed"],
            "not_closed": whole["not_closed"][:5]}
        step("whole-population closure (ngữ nghĩa, không phải đếm)",
             not whole["not_closed"] and whole["closed"] == whole["checked"],
             f"{whole['closed']}/{whole['checked']} closed")

        debt, held = actionable_replay_debt(cand)
        report["candidate_replay"] = {"actionable": debt, "held_tombstoned": held}
        step("candidate actionable replay debt = 0", debt == 0,
             f"actionable={debt} · giữ vì bia mộ={held}")

        hd = historical_delta(src_ro, cand)
        report["candidate_historical_delta"] = hd
        step("candidate: 0 rebuild · 0 duplicate · 0 hàng lịch sử bị đổi",
             hd["historical_rebuilds"] == 0
             and hd["historical_duplicate_memories"] == 0
             and hd["historical_rows_mutated"] == 0
             and hd["historical_rows_vanished"] == 0, str(hd)[:200])
        src_ro.close()

        # Checkpoint + ĐÓNG trước khi certify: certify_candidate fail-closed
        # trên -wal có nội dung, và một candidate còn WAL sống là candidate
        # chưa bền — hàng rào đó bắt đúng ở preflight lần đầu.
        cand.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cand.commit()
        cand.close()
        cand_sha = certify_candidate(CANDIDATE)
        report["candidate_sha256"] = cand_sha
        step("candidate checkpoint + certified (không -wal sống)", True,
             cand_sha[:16])

        # Readiness KHÔNG chỉ là "có artifact đúng hash". Thủ tục phải được
        # diễn tập: cài candidate lên một canonical dùng-một-lần rồi rollback,
        # và chứng minh nó quay về ĐÚNG baseline byte-for-byte.
        disp = WORK / "rollback_rehearsal_canonical.db"
        _copy(SNAP, disp)
        baseline = _sha(disp)
        install_generation(disp, CANDIDATE, QUARANTINE / "rollback_rehearsal")
        install_generation(disp, SNAP, QUARANTINE / "rollback_rehearsal")
        back = _sha(disp)
        dc = _open(disp, ro=True)
        ic = [r[0] for r in dc.execute("PRAGMA integrity_check")][:1]
        fkv = len(list(dc.execute("PRAGMA foreign_key_check")))
        audit_gone = dc.execute(
            "SELECT name FROM sqlite_master WHERE "
            "name='projection_adoption_audit'").fetchone() is None
        dc.close()
        report["rollback_readiness"] = {
            "certified_pre_snapshot": str(SNAP), "sha256": snap_sha,
            "procedure": "install_generation(REAL, SNAP, quarantine/rollback) "
                         "— generation replacement, KHÔNG ghi đè file sống",
            "hash_verified": certify_candidate(SNAP) == snap_sha,
            "procedure_rehearsed_on_disposable": {
                "returned_to_baseline": back == baseline,
                "integrity": ic, "fk_violations": fkv,
                "adoption_tables_gone": audit_gone}}
        step("rollback readiness — hash ĐÚNG và thủ tục ĐÃ DIỄN TẬP",
             report["rollback_readiness"]["hash_verified"] and back == baseline
             and ic == ["ok"] and fkv == 0 and audit_gone,
             f"disposable quay về baseline {back[:12]} · integrity {ic} · "
             f"bảng adoption biến mất={audit_gone}")

        if not go:
            report["verdict"] = "HBF-3 PREFLIGHT PASS — chưa install"
            report["real_store_migration"] = "NOT PERFORMED (preflight)"
            _flush()
            print(f"\nPREFLIGHT PASS — {round(time.time()-t0,1)}s. "
                  f"Chạy lại với --go để install một lần.")
            return

        # ---- HBF3-7. SOURCE-GENERATION CAS GUARD (ngay sát install)
        gen_now = source_generation(REAL)
        drift = {k: (gen_at_snapshot.get(k), gen_now.get(k))
                 for k in gen_now if gen_now.get(k) != gen_at_snapshot.get(k)}
        report["cas_guard"] = {"at_snapshot": gen_at_snapshot,
                               "at_install": gen_now,
                               "drift": drift, "match": not drift}
        step("source-generation CAS guard — main + WAL + số đếm logic "
             "(STALE BASELINE != SAFE REPLACEMENT)", not drift,
             f"main {gen_now['main_sha256'][:12]} · wal "
             f"{gen_now['wal_bytes']}B · events "
             f"{gen_now['logical']['cognitive_events']}"
             + (f" · LỆCH: {drift}" if drift else " · không lệch"))

        # ---- HBF3-8. MỘT install_generation. Đây là lần chạm duy nhất.
        report["install_attempted"] = True      # đặt TRƯỚC, không phải sau
        _flush()
        manifest = install_generation(REAL, CANDIDATE, QUARANTINE)
        report["install"] = {"installed_sha256": manifest["installed_sha256"],
                             "quarantine_bundle": str(manifest["quarantine_bundle"]),
                             "transitions": 1}
        report["real_store_migration"] = "PERFORMED — ONE generation transition"
        step("ONE install_generation → INSTALL_CONFIRMED",
             manifest["installed_sha256"] == cand_sha,
             manifest["installed_sha256"][:16])

        post_install_phase(cand_sha, snap_sha, t0)
    except BaseException as exc:                                # noqa: BLE001
        report["aborted_with"] = f"{type(exc).__name__}: {exc}"
        # "chưa ghi report['install']" KHÔNG có nghĩa canonical còn nguyên.
        # install_generation có thể ném SAU move-aside: lúc đó canonical đã
        # bị dời sang quarantine bundle và chưa có gì thay thế. Bản đầu của
        # handler này in "canonical NGUYÊN VẸN" rồi BẬT LẠI HOOK vào đúng cái
        # store đang không tồn tại — review đối kháng bắt, xếp catastrophic.
        # QUYẾT BẰNG QUAN SÁT, KHÔNG BẰNG SỔ SÁCH. "chưa ghi report['install']"
        # chỉ nói về bookkeeping của chính script; câu hỏi thật là canonical
        # bây giờ đang ở trạng thái nào.
        attempted = report.get("install_attempted", False)
        present = REAL.exists()
        sha_now = _sha(REAL) if present else None
        known = {report.get("source_generation_at_snapshot", {}).get(
            "main_sha256"), report.get("candidate_sha256")}
        untouched = present and sha_now in known
        report["canonical_present"] = present
        report["canonical_sha_at_abort"] = sha_now
        report["canonical_state"] = ("nguyên vẹn/đã cài xong" if untouched
                                     else "KHÔNG XÁC ĐỊNH")
        report["rollback_needed"] = attempted or not untouched
        _flush()
        if go and not attempted and untouched:
            unquiesce()
            print(f"\nDỪNG TRƯỚC INSTALL — canonical NGUYÊN VẸN, candidate bỏ. "
                  f"hook đã khôi phục. ({exc})")
        elif attempted:
            print(f"\nDỪNG TRONG/SAU INSTALL — hook GIỮ TẮT (không bật lại "
                  f"vào một store chưa xác định).\n"
                  f"  canonical tồn tại: {REAL.exists()}\n"
                  f"  quarantine       : {QUARANTINE}\n"
                  f"  khôi phục        : python activation/"
                  f"HBF3_naturalization.py --rollback   ({exc})")
        raise
    finally:
        _flush()


def resume_after_install() -> None:
    """Chạy tiếp pha hậu-install trên generation ĐÃ CÀI — không cài lại.

    Chỉ hợp lệ khi canonical đang byte-identical với candidate đã certify:
    nghĩa là hiện vật đang phục vụ ĐÚNG là hiện vật đã qua S5/S20/S50 và
    closure toàn population. Nếu lệch, dừng — lúc đó mới là chuyện rollback."""
    prev = json.loads((WORK / "hbf3_report.json").read_text(encoding="utf-8"))
    report.update({k: v for k, v in prev.items()
                   if k not in ("verdict", "steps", "aborted_with")})
    report["resumed_after_install"] = True
    report["install_attempted"] = True
    cand_sha = prev["candidate_sha256"]
    snap_sha = prev["source_snapshot_sha256"]
    t0 = time.time()
    print("HBF-3 RESUME — tiếp pha hậu-install, KHÔNG thay generation\n")
    # Câu hỏi đúng KHÔNG phải "file có y hệt candidate không" — canary hợp lệ
    # đã ghi thêm event nên byte chắc chắn khác. Câu hỏi đúng là: HIỆN VẬT
    # MIGRATION còn nguyên và LỊCH SỬ chưa bị chạm.
    step("canonical tồn tại", REAL.exists(),
         _sha(REAL)[:16] if REAL.exists() else "THIẾU FILE")
    rc, rp = _open(REAL, ro=True), _open(SNAP, ro=True)
    try:
        pre_ids = {str(r[0]) for r in rp.execute(
            "SELECT event_id FROM cognitive_events")}
        post_ids = {str(r[0]) for r in rc.execute(
            "SELECT event_id FROM cognitive_events")}
        new_since_install = post_ids - pre_ids
        closure0 = verify_closure_from_audit(rc)
        inv0 = adoption_invariants(rc)
        hd0 = historical_delta(rp, rc, exclude_events=new_since_install)
    finally:
        rc.close()
        rp.close()
    report["resume_precondition"] = {
        "canonical_sha": _sha(REAL), "candidate_sha": cand_sha,
        "identical_to_candidate": _sha(REAL) == cand_sha,
        "events_written_since_install": len(new_since_install),
        "closure": f"{closure0['closed']}/{closure0['checked']}",
        "historical_delta": hd0}
    step("hiện vật migration còn nguyên · lịch sử chưa bị chạm",
         not closure0["not_closed"]
         and closure0["checked"] == report["migration_transaction"][
             "audit_inserted"]
         and all(v == 0 for k, v in inv0.items()
                 if k != "migration_rows_total")
         and hd0["historical_rebuilds"] == 0
         and hd0["historical_rows_mutated"] == 0
         and hd0["historical_rows_vanished"] == 0,
         f"closure {closure0['closed']}/{closure0['checked']} · "
         f"{len(new_since_install)} event mới sau install · "
         f"rebuild={hd0['historical_rebuilds']} "
         f"mutated={hd0['historical_rows_mutated']}")
    step("writers vẫn đang quiesced",
         "hook_cli" not in SETTINGS.read_text(encoding="utf-8")
         and SETTINGS_BACKUP.exists(), "settings không còn hook_cli")
    try:
        post_install_phase(cand_sha, snap_sha, t0)
    except BaseException as exc:                                # noqa: BLE001
        report["aborted_with"] = f"{type(exc).__name__}: {exc}"
        report["rollback_needed"] = True
        _flush()
        print(f"\nDỪNG SAU INSTALL — hook GIỮ TẮT. --rollback nếu cần. ({exc})")
        raise
    finally:
        _flush()


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--verify-installed":
        verify_installed(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "--rollback":
        rollback()
    elif len(sys.argv) >= 2 and sys.argv[1] == "--resume-after-install":
        resume_after_install()
    else:
        main(go="--go" in sys.argv)
