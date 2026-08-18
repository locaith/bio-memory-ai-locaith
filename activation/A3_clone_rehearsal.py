"""A3 — diễn tập toàn bộ kế hoạch activation trên một BẢN SAO của store thật.

Store thật không bị chạm. Mọi bước dưới đây chạy trên clone dựng từ snapshot
A1; cuối cùng rollback được chứng minh bằng cách dựng lại từ snapshot và so
hash, còn store thật được kiểm bằng bất biến subsystem (không phải hash toàn
file — hook phiên vẫn ghi vào nó một cách hợp lệ).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive import projection_control as pc
from bio_agent_os.cognitive import forgetting

A1 = Path(__file__).parent / "A1"
SNAPSHOT = A1 / "preflight_snapshot.db"
REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
WORK = Path(__file__).parent / "A3"
CLONE = WORK / "rehearsal_clone.db"
LEASE = 300.0

report: dict = {"steps": []}


def step(name: str, ok: bool, detail: str = ""):
    report["steps"].append({"step": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"A3 dừng tại: {name}")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def containment_of_real() -> dict:
    conn = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            "projection_ledger": "CO" if "projection_ledger" in tables else "ABSENT",
            "memory_tombstones": "CO" if "memory_tombstones" in tables else "ABSENT",
            "projection_outbox_rows": (
                conn.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0]
                if "projection_outbox" in tables else "ABSENT"),
        }
    finally:
        conn.close()


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if CLONE.exists():
        CLONE.unlink()

    snap_hash = sha(SNAPSHOT)
    before_real = containment_of_real()
    step("bất biến containment store thật TRƯỚC diễn tập",
         before_real["projection_ledger"] == "ABSENT"
         and before_real["memory_tombstones"] == "ABSENT"
         and before_real["projection_outbox_rows"] == 0, str(before_real))

    # ---- dựng clone từ snapshot
    shutil.copy2(SNAPSHOT, CLONE)
    step("clone dựng từ snapshot, hash khớp", sha(CLONE) == snap_hash,
         snap_hash[:24])

    # ---- schema initialization: mở runtime outbox trên CLONE
    runtime = MemoryOS(CLONE, projection_mode="outbox")
    tables = {r[0] for r in runtime.memories.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    step("schema init trên clone: projection runtime tables tồn tại",
         "projection_outbox" in tables, f"{len(tables)} bảng")

    # ---- one controlled job
    marker = "REHEARSAL-A3: Kiểm hoạt hoá có kiểm soát 0919000111."
    event = runtime.observe(tenant_id="t1", actor="activation", source="rehearsal",
                            content=marker, workspace_id="w1")
    queued = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_outbox WHERE status='pending'"
    ).fetchone()[0]
    step("một job có kiểm soát vào hàng đợi", queued == 1, f"pending={queued}")

    worker = worker_for(runtime, worker_id="rehearsal-1", lease_seconds=LEASE)
    metrics = worker.run_once(batch_size=1)
    built = sum(1 for r in runtime.memories.conn.execute(
        "SELECT content FROM cognitive_memories") if "0919000111" in str(r[0]))
    ledger = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
    job_status = runtime.memories.conn.execute(
        "SELECT status FROM projection_outbox").fetchone()[0]
    step("persistent readback: materialized + ledger + completed",
         built == 1 and ledger >= 1 and job_status == "completed"
         and metrics.completed == 1,
         f"built={built} ledger={ledger} status={job_status}")

    # ---- bounded batch
    for i in range(3):
        runtime.observe(tenant_id="t1", actor="activation", source="rehearsal",
                        content=f"REHEARSAL-A3 batch {i} 092200022{i}.",
                        workspace_id="w1")
    drain_report = pc.drain(runtime, timeout_seconds=30.0, batch_size=10)
    step("bounded batch: drain kết luận COMPLETE",
         drain_report["outcome"] == pc.DRAIN_COMPLETE
         and drain_report["remaining"] == 0, str(drain_report["outcome"]))

    # ---- truth/forgetting invariant trên clone sau khi runtime chạy
    forgetting.forget_derived(runtime, subject="Kiểm hoạt hoá",
                              needle="0919000111")
    still = sum(1 for r in runtime.memories.conn.execute(
        "SELECT content FROM cognitive_memories") if "0919000111" in str(r[0]))
    worker.run_once(batch_size=10)     # worker chạy lại — không được hồi sinh
    resurrected = sum(1 for r in runtime.memories.conn.execute(
        "SELECT content FROM cognitive_memories") if "0919000111" in str(r[0]))
    step("forget giữ nguyên hiệu lực sau activation (0 resurrection)",
         still == 0 and resurrected == 0, f"still={still} sau-rerun={resurrected}")

    legacy_count = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    runtime.close()

    # ---- rollback: dựng lại từ snapshot, hash phải về đúng điểm
    CLONE.unlink()
    shutil.copy2(SNAPSHOT, CLONE)
    step("rollback: clone dựng lại từ snapshot, hash khớp điểm rollback",
         sha(CLONE) == snap_hash, snap_hash[:24])

    # ---- store thật không bị chạm (bất biến subsystem)
    after_real = containment_of_real()
    step("bất biến containment store thật SAU diễn tập",
         after_real == before_real, str(after_real))

    report["conclusion"] = "A3 CLONE REHEARSAL: PASS"
    report["rollback_point"] = {"snapshot": str(SNAPSHOT),
                                 "sha256": snap_hash}
    report["memories_after_rehearsal_run"] = legacy_count
    (WORK / "rehearsal_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nA3 CLONE REHEARSAL: PASS")


if __name__ == "__main__":
    main()
