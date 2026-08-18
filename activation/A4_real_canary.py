"""A4 — REAL CANARY. Mutation đầu tiên của store thật, phạm vi MỘT job.

Được duyệt tường minh, một lần, có điều kiện. Hai guard bổ sung của chủ:
  1. snapshot mới phải RESTORE-VERIFY trước mutation — một backup chưa từng
     restore thành công vẫn chỉ là lời hứa.
  2. unrelated persistent delta = 0 — đo trên TỪNG HÀNG đã tồn tại trước A4
     (digest theo hàng), không phải đếm tổng.

Bất kỳ lệch nào ở bất kỳ bước nào: DỪNG MỌI HOẠT ĐỘNG WORKER, restore từ
snapshot pre-A4, verify lại theo manifest — không vá tại chỗ trước rollback.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive import forgetting

REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
WORK = Path(__file__).parent / "A4"
SNAP = WORK / "pre_a4_snapshot.db"
RESTORE_PROBE = WORK / "pre_a4_restore_probe.db"
MARKER = "CANARY-A4: kiểm hoạt hoá thật, một job duy nhất, 0913777555."
LEASE = 300.0

report: dict = {"phase": "A4 REAL CANARY", "steps": [],
                "ownership_telemetry": [], "schema_mutations": []}


def step(name: str, ok: bool, detail: str = ""):
    report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        report["conclusion"] = f"A4 FAIL tại: {name} — ROLLBACK"
        rollback()
        raise SystemExit(1)


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def row_digests(db: Path) -> dict:
    """Digest theo TỪNG HÀNG của các bảng nội dung — nền cho unrelated-delta."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    out: dict = {}
    try:
        for table, key in (("cognitive_events", "event_id"),
                           ("cognitive_memories", "memory_id")):
            rows = {}
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            for r in conn.execute(f"SELECT * FROM {table}"):
                d = dict(zip(cols, r))
                rows[str(d[key])] = hashlib.sha256(
                    repr(sorted(d.items())).encode()).hexdigest()
            out[table] = rows
        out["tables"] = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        conn.close()
    return out


def telemetry(conn, label: str, job_id=None):
    q = "SELECT job_id, status, locked_by, locked_at, attempts FROM projection_outbox"
    rows = [dict(zip(("job_id", "status", "locked_by", "locked_at", "attempts"), r))
            for r in conn.execute(q + (" WHERE job_id=?" if job_id else ""),
                                  ((job_id,) if job_id else ()))]
    report["ownership_telemetry"].append({"at": label, "rows": rows})
    return rows


def rollback():
    print("\n  >>> ROLLBACK: restore tu snapshot pre-A4")
    shutil.copy2(SNAP, REAL)
    ok = sha(REAL) == report["pre_a4"]["snapshot_sha256"]
    report["rollback"] = {"executed": True, "hash_match": ok}
    print(f"  >>> restore {'KHOP' if ok else 'LECH — CAN CAN THIEP TAY'} manifest")


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    # ---- GUARD 1: snapshot MỚI + restore-verify TRƯỚC mutation
    source = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(SNAP))
    source.backup(dest)
    dest.close()
    probe = sqlite3.connect(str(RESTORE_PROBE))
    sqlite3.connect(f"file:{SNAP.as_posix()}?mode=ro", uri=True).backup(probe)
    probe_counts = {t: probe.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                    for (t,) in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
    src_counts = {t: source.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                  for (t,) in source.execute(
                      "SELECT name FROM sqlite_master WHERE type='table'")}
    probe.close()
    source.close()
    report["pre_a4"] = {"snapshot": str(SNAP), "snapshot_sha256": sha(SNAP),
                        "row_counts": src_counts}
    step("GUARD 1: snapshot mới restore-verify TRƯỚC mutation",
         probe_counts == src_counts, f"{len(src_counts)} bảng khớp")

    before = row_digests(REAL)
    report["pre_a4"]["events"] = len(before["cognitive_events"])
    report["pre_a4"]["memories"] = len(before["cognitive_memories"])

    # ---- mutation 1: mở outbox mode
    runtime = MemoryOS(REAL, projection_mode="outbox")
    worker = worker_for(runtime, worker_id="canary-a4", lease_seconds=LEASE)
    after_open = sorted(r[0] for r in runtime.memories.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    report["schema_mutations"] = sorted(set(after_open) - set(before["tables"]))
    step("schema mutation ghi nhận tường minh", True,
         f"bảng mới: {report['schema_mutations'] or 'không'}")

    # ---- MỘT event canary
    conn = runtime.memories.conn
    telemetry(conn, "trước observe")
    event = runtime.observe(tenant_id="personal", actor="activation",
                            source="canary", content=MARKER, workspace_id="personal")
    report["canary_event_id"] = event.event_id
    jobs = [dict(zip(("job_id", "status"), r)) for r in conn.execute(
        "SELECT job_id, status FROM projection_outbox")]
    step("đúng MỘT job canary vào hàng đợi",
         len(jobs) == 1 and jobs[0]["status"] == "pending", jobs)
    report["canary_job_id"] = jobs[0]["job_id"]

    telemetry(conn, "trước claim", report["canary_job_id"])
    metrics = worker.run_once(batch_size=1)
    telemetry(conn, "sau claim + sau terminal", report["canary_job_id"])

    built = [str(r[0]) for r in conn.execute(
        "SELECT memory_id FROM cognitive_memories WHERE content LIKE ?",
        (f"%0913777555%",))]
    ledger = conn.execute("SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
    job_status = conn.execute(
        "SELECT status FROM projection_outbox WHERE job_id=?",
        (report["canary_job_id"],)).fetchone()[0]
    step("persistent readback: 1 materialized + 1 ledger + COMPLETED",
         len(built) == 1 and ledger == 1 and job_status == "completed"
         and metrics.completed == 1,
         f"built={len(built)} ledger={ledger} status={job_status}")
    report["projection_readback"] = {"memory_id": built[0], "ledger_rows": ledger,
                                     "job_status": job_status}

    # ---- forget đúng canary
    telemetry(conn, "trước forget", report["canary_job_id"])
    forgetting.forget_derived(runtime, subject="kiểm hoạt hoá",
                              needle="0913777555")
    gone = conn.execute("SELECT COUNT(*) FROM cognitive_memories "
                        "WHERE content LIKE '%0913777555%'").fetchone()[0]
    step("forget hiệu lực trong persistent state", gone == 0, f"còn={gone}")

    # ---- worker chạy thêm MỘT lần bounded — không hồi sinh
    worker.run_once(batch_size=10)
    telemetry(conn, "sau worker rerun", report["canary_job_id"])
    resurrected = conn.execute("SELECT COUNT(*) FROM cognitive_memories "
                               "WHERE content LIKE '%0913777555%'").fetchone()[0]
    step("0 resurrection sau worker rerun", resurrected == 0, f"={resurrected}")
    report["forget_readback"] = {"after_forget": gone, "after_rerun": resurrected}
    runtime.close()

    # ---- GUARD 2: unrelated persistent delta = 0, đo theo TỪNG HÀNG cũ
    after = row_digests(REAL)
    changed_events = [k for k, v in before["cognitive_events"].items()
                      if after["cognitive_events"].get(k) != v]
    changed_memories = [k for k, v in before["cognitive_memories"].items()
                        if after["cognitive_memories"].get(k) != v]
    missing_memories = [k for k in before["cognitive_memories"]
                        if k not in after["cognitive_memories"]]
    step("GUARD 2: mọi event có sẵn nguyên vẹn", not changed_events,
         f"đổi={len(changed_events)}")
    step("GUARD 2: mọi memory có sẵn nguyên vẹn, không mất hàng nào",
         not changed_memories and not missing_memories,
         f"đổi={len(changed_memories)} mất={len(missing_memories)}")
    new_events = [k for k in after["cognitive_events"]
                  if k not in before["cognitive_events"]]
    report["unrelated_delta"] = {
        "pre_existing_events_changed": changed_events,
        "pre_existing_memories_changed": changed_memories,
        "pre_existing_memories_missing": missing_memories,
        "new_events": new_events,
        "new_tables": report["schema_mutations"],
    }

    report["conclusion"] = "A4 REAL CANARY: PASS"
    report["rollback"] = {"executed": False,
                          "point": str(SNAP),
                          "sha256": report["pre_a4"]["snapshot_sha256"]}
    (WORK / "a4_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nA4 REAL CANARY: PASS — store thật đã nhận đúng một job, "
          "mọi hàng cũ nguyên vẹn")


if __name__ == "__main__":
    main()
