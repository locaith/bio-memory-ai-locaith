"""A5.0–A5.3 — staged NEW-WRITE activation trên store thật.

Các con số 5/20/50 là blast-radius caps, không phải ngưỡng thống kê.

INTERLOCK (mọi stage): cấm replay/rebuild/backfill — script này không import
replay engine; và sau mỗi stage, mọi job trong outbox phải thuộc đúng tập
event_id script này vừa ghi. Một job lạ = historical enqueue = STOP.
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
WORK = Path(__file__).parent / "A5"
SNAP = WORK / "pre_a5_snapshot.db"
LEASE = 300.0
report: dict = {"phase": "A5.0-A5.3", "stages": {}}


def fail(stage: str, why: str):
    report["stages"][stage] = {"result": "FAIL", "why": why}
    print(f"  [STOP] {stage}: {why}")
    print("  >>> ROLLBACK tu snapshot pre-A5")
    shutil.copy2(SNAP, REAL)
    ok = hashlib.sha256(REAL.read_bytes()).hexdigest() == report["pre_a5_sha256"]
    print(f"  >>> restore {'KHOP' if ok else 'LECH'}")
    (WORK / "a5_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    raise SystemExit(1)


def row_digests(db) -> dict:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for table, key in (("cognitive_events", "event_id"),
                           ("cognitive_memories", "memory_id")):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            out[table] = {
                str(dict(zip(cols, r))[key]): hashlib.sha256(
                    repr(sorted(dict(zip(cols, r)).items())).encode()).hexdigest()
                for r in conn.execute(f"SELECT * FROM {table}")}
        return out
    finally:
        conn.close()


def stage(runtime, name: str, n_writes: int, baseline: dict) -> dict:
    conn = runtime.memories.conn
    marker_base = f"A5-{name}"
    requested, event_ids = [], set()
    for i in range(n_writes):
        content = f"{marker_base} ghi mới có kiểm soát #{i} mã 08{name[-1]}55000{i:02d}."
        event = runtime.observe(tenant_id="personal", actor="activation",
                                source=f"a5-{name}", content=content,
                                workspace_id="personal")
        requested.append(content)
        event_ids.add(str(event.event_id))

    # INTERLOCK: outbox chỉ được chứa job của đúng các event vừa ghi
    queue = [str(r[0]) for r in conn.execute(
        "SELECT event_id FROM projection_outbox WHERE status='pending'")]
    alien = [e for e in queue if e not in event_ids]
    if alien:
        fail(name, f"HISTORICAL/ALIEN ENQUEUE: {alien[:3]}")

    worker = worker_for(runtime, worker_id=f"a5-{name}", lease_seconds=LEASE)
    attempted = 0
    while True:
        metrics_before = worker.metrics.claimed
        worker.run_once(batch_size=10)
        attempted = worker.metrics.claimed
        if worker.metrics.claimed == metrics_before:
            break

    confirmed = sum(1 for r in conn.execute(
        "SELECT content FROM cognitive_memories") if marker_base in str(r[0]))
    terminal = dict(conn.execute(
        "SELECT status, COUNT(*) FROM projection_outbox GROUP BY status"))
    unresolved = terminal.get("pending", 0) + terminal.get("in_progress", 0)
    dupes = [c for c in requested if sum(
        1 for r in conn.execute(
            "SELECT content FROM cognitive_memories WHERE content=?", (c,))) > 1]
    lost = n_writes - confirmed          # tính TRƯỚC forget, không âm giả

    # forget MỘT canary của stage, giữ ForgetReport — không suy từ đếm.
    # Đích danh bằng memory_id: lần chạy đầu dùng subject chung của cả stage
    # và forget xoá đúng-hợp-lệ CẢ 5 — product đúng, guard của harness sai.
    target = conn.execute(
        "SELECT memory_id FROM cognitive_memories WHERE content=?",
        (requested[0],)).fetchone()[0]
    needle = f"08{name[-1]}55000000"[:12]
    fr = forgetting.forget_derived(runtime, memory_id=str(target),
                                   needle=requested[0][-14:])
    tomb = conn.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0] \
        if conn.execute("SELECT name FROM sqlite_master WHERE name='memory_tombstones'").fetchone() else 0

    after = row_digests(REAL)
    changed = [k for k, v in baseline["cognitive_events"].items()
               if after["cognitive_events"].get(k) != v]
    changed += [k for k, v in baseline["cognitive_memories"].items()
                if after["cognitive_memories"].get(k) != v]

    result = {
        "requested": n_writes, "attempted_deliveries": attempted,
        "confirmed_materialized": confirmed,
        "lost": lost,
        "duplicates": len(dupes), "unresolved_jobs": unresolved,
        "forget": {"memories_deleted": fr.memories_deleted,
                    "tombstoned": fr.tombstoned, "note": fr.note},
        "tombstone_rows_total": tomb,
        "pre_existing_rows_changed": len(changed),
    }
    report["stages"][name] = result
    print(f"  [{name}] {result}")

    if confirmed != n_writes:
        fail(name, f"confirmed {confirmed} != requested {n_writes}")
    if dupes or unresolved or changed:
        fail(name, f"dupes={len(dupes)} unresolved={unresolved} "
                   f"pre-existing-changed={len(changed)}")
    if fr.memories_deleted != 1 or fr.tombstoned < 1:
        fail(name, f"forget khong du tu cach: deleted={fr.memories_deleted} "
                   f"tombstoned={fr.tombstoned}")
    result["result"] = "PASS"
    return result


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    # ---- A5.0: snapshot mới + restore-verify + interlock baseline
    src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(SNAP))
    src.backup(dst)
    dst.close()
    probe = sqlite3.connect(":memory:")
    sqlite3.connect(f"file:{SNAP.as_posix()}?mode=ro", uri=True).backup(probe)
    src_counts = {t: src.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                  for (t,) in src.execute(
                      "SELECT name FROM sqlite_master WHERE type='table'")}
    probe_counts = {t: probe.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
                    for (t,) in probe.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
    probe.close()
    src.close()
    report["pre_a5_sha256"] = hashlib.sha256(SNAP.read_bytes()).hexdigest()
    if src_counts != probe_counts:
        print("[STOP] A5.0 snapshot restore-verify LECH")
        raise SystemExit(1)
    print(f"  [A5.0] snapshot + restore-verify PASS ({len(src_counts)} bảng)")
    baseline = row_digests(REAL)
    report["stages"]["A5.0"] = {"result": "PASS",
                                 "baseline_events": len(baseline["cognitive_events"]),
                                 "baseline_memories": len(baseline["cognitive_memories"])}

    runtime = MemoryOS(REAL, projection_mode="outbox")
    try:
        stage(runtime, "A5.1", 5, baseline)
        baseline = row_digests(REAL)          # baseline mới sau mỗi stage PASS
        stage(runtime, "A5.2", 20, baseline)
        baseline = row_digests(REAL)
        stage(runtime, "A5.3", 50, baseline)
    finally:
        runtime.close()

    report["conclusion"] = "A5.0-A5.3 PASS — sẵn sàng A5.4"
    (WORK / "a5_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nA5.0-A5.3: PASS")


if __name__ == "__main__":
    main()
