"""Crash, kill, lock, stale lease, duplicate, restart — against a loaded queue.

The fault matrix already proves each of these in isolation with a quiet
database. What it cannot show is whether recovery still works while other
workers are competing for the same write lock, which is the only condition
under which any of it will actually happen.

Nothing here simulates a crash. `TerminateProcess` and `os._exit` skip every
finaliser, flush and atexit hook, which is what a power loss does; a clean
`sys.exit` would prove only that shutdown works.
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive import fault_points as _fault              # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob     # noqa: E402
from bio_agent_os.cognitive.shadow import COGNITIVE_MEMORY             # noqa: E402

from benchmarks.reliability import corpus, environment, harness        # noqa: E402
from benchmarks.reliability.workloads import (                         # noqa: E402
    DEFAULT_SEED,
    doctor_digest,
    run_doctor,
)


# -- workload E: recovery ----------------------------------------------------

def crash_worker_main(spec: dict[str, Any]) -> None:
    """A worker that dies at a named point, in a real process.

    `os._exit` skips every finaliser, flush and atexit hook, which is what a
    power loss does. A clean `sys.exit` would prove only that shutdown works.
    """
    sys.path.insert(0, str(_REPO))
    from benchmarks.reliability import harness as _h

    target = spec["fault_point"]
    after = int(spec.get("after_firings", 1))
    seen = {"n": 0}

    def hook(point: str) -> None:
        if point != target:
            return
        seen["n"] += 1
        if seen["n"] >= after:
            Path(spec["marker"]).write_text(str(os.getpid()), encoding="utf-8")
            os._exit(70)

    runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
    worker = _h.make_worker(
        runtime, worker_id=spec["worker_id"], shadow=bool(spec.get("shadow")),
        lease_seconds=float(spec.get("lease_seconds", 300.0)),
    )
    _fault.install(hook)
    deadline = time.time() + float(spec.get("max_seconds", 120))
    while time.time() < deadline:
        before = worker.metrics.claimed
        worker.run_once(batch_size=int(spec.get("batch_size", 20)))
        if worker.metrics.claimed == before:
            break
    _fault.clear()
    runtime.close()


def lock_holder_main(spec: dict[str, Any]) -> None:
    """Hold an exclusive write transaction, so everyone else meets a busy lock.

    `isolation_level=None` matters: with Python's default the driver issues its
    own BEGIN around DML, and the explicit `BEGIN IMMEDIATE` below either
    errors or is quietly bypassed — in which case the "held lock" is never
    held and the scenario measures nothing.
    """
    conn = sqlite3.connect(spec["db"], timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bench_lock_probe(id INTEGER PRIMARY KEY, at REAL)"
        )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO bench_lock_probe(at) VALUES(?)", (time.time(),))
        Path(spec["ready_marker"]).write_text("held", encoding="utf-8")
        time.sleep(float(spec.get("hold_seconds", 3.0)))
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def workload_e_recovery(
    *,
    run_dir: Path,
    events: int = 4000,
    workers: int = 4,
    mode: str = "shadow",
    shadow: bool = False,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Seven disruptions against a loaded queue, each verified after recovery."""
    work = run_dir / f"E_recovery_{events}"
    work.mkdir(parents=True, exist_ok=True)
    db = str(work / "recovery.db")

    runtime = MemoryOS(db, projection_mode=mode)
    event_ids = []
    for obs in corpus.generate(events, seed=seed):
        event = runtime.observe(
            tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
            content=obs.content, workspace_id=obs.workspace_id,
        )
        event_ids.append(event.event_id)
    runtime.close()

    baseline = harness.queue_counts(db)
    scenarios: list[dict[str, Any]] = []
    ctx = __import__("multiprocessing").get_context("spawn")

    # 1. Kill a worker mid-drain while its peers keep working.
    gate = ctx.Event()
    stop = ctx.Event()
    specs = [
        {
            "db": db, "mode": mode, "worker_id": f"k{i}", "shadow": shadow,
            "out": str(work / f"kill_worker_{i}.json"), "start_event": gate,
            "stop_event": stop, "batch_size": 20, "idle_polls": 10**9,
            "stay_alive": True, "max_seconds": 300, "poll_seconds": 0.02,
            "lease_seconds": 5.0,
        }
        for i in range(workers)
    ]
    procs = harness.spawn(harness.worker_main, specs)
    time.sleep(1.5)
    gate.set()
    killed_at = None
    rng = random.Random(seed)
    deadline = time.time() + 30
    while time.time() < deadline:
        counts = harness.queue_counts(db)
        if counts["completed"] >= events * 0.2:
            victim = procs[rng.randrange(len(procs))]
            victim.terminate()          # TerminateProcess / SIGKILL: no cleanup
            killed_at = counts
            break
        time.sleep(0.05)
    time.sleep(1.0)
    after_kill = harness.queue_counts(db)
    stop.set()
    harness.join_all(procs, timeout=120)
    scenarios.append({
        "scenario": "worker_killed_mid_drain",
        "killed_after_completed": (killed_at or {}).get("completed"),
        "queue_after_kill": after_kill,
        "orphaned_in_progress": after_kill["in_progress"],
    })

    # 2. Restart the workers and let them finish. Nothing may be lost.
    restart_start = time.time()
    gate2 = ctx.Event()
    specs2 = [
        {
            "db": db, "mode": mode, "worker_id": f"r{i}", "shadow": shadow,
            "out": str(work / f"restart_worker_{i}.json"), "start_event": gate2,
            "batch_size": 50, "max_seconds": 600, "lease_seconds": 5.0,
            # Must idle for longer than the lease. A restarted worker that
            # gives up sooner exits while the dead worker's claims are still
            # inside their lease window, and reclaims nothing — measured, not
            # assumed: the first run of this scenario recovered 0 of 9 jobs
            # for exactly that reason.
            "idle_polls": 400, "poll_seconds": 0.05,
        }
        for i in range(workers)
    ]
    procs2 = harness.spawn(harness.worker_main, specs2)
    time.sleep(1.0)
    gate2.set()
    harness.join_all(procs2, timeout=900)
    recovery_seconds = time.time() - restart_start
    after_restart = harness.queue_counts(db)
    restart_results = harness.read_results([Path(s["out"]) for s in specs2])
    scenarios.append({
        "scenario": "worker_restarted",
        "recovery_seconds": round(recovery_seconds, 3),
        "queue": after_restart,
        "already_built": sum(r["metrics"]["already_built"] for r in restart_results),
        "jobs_recovered": after_restart["completed"] - after_kill["completed"],
    })

    # 3. A held write lock. Probed twice: once with a timeout shorter than the
    #    hold, to prove the error surfaces rather than corrupting anything;
    #    once with the runtime's own 30s timeout, to show it simply waits.
    ready = work / "lock_ready.txt"
    if ready.exists():
        ready.unlink()
    lock_proc = ctx.Process(
        target=lock_holder_main,
        args=({"db": db, "hold_seconds": 3.0, "ready_marker": str(ready)},),
    )
    lock_start = time.time()
    lock_proc.start()
    waited = 0.0
    while not ready.exists() and waited < 10:
        time.sleep(0.05)
        waited += 0.05

    def _probe(busy_timeout_ms: int) -> tuple[float, int]:
        errors = 0
        started_at = time.time()
        probe = sqlite3.connect(db, timeout=busy_timeout_ms / 1000 + 5, isolation_level=None)
        try:
            probe.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            probe.execute("BEGIN IMMEDIATE")
            probe.execute("CREATE TABLE IF NOT EXISTS bench_probe2(id INTEGER PRIMARY KEY)")
            probe.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            errors += 1 if harness.is_lock_error(exc) else 0
        finally:
            probe.close()
        return time.time() - started_at, errors

    short_s, short_errors = _probe(800)
    long_s, long_errors = _probe(30_000)
    lock_proc.join(timeout=30)
    scenarios.append({
        "scenario": "write_lock_held",
        "hold_seconds": 3.0,
        "lock_acquired_by_holder": ready.exists(),
        "short_timeout_ms": 800,
        "short_timeout_blocked_for_s": round(short_s, 3),
        "short_timeout_lock_errors": short_errors,
        "runtime_timeout_ms": 30_000,
        "runtime_timeout_blocked_for_s": round(long_s, 3),
        "runtime_timeout_lock_errors": long_errors,
        "total_seconds": round(time.time() - lock_start, 3),
        "note": (
            "the 800ms probe is expected to fail and does; the 30s probe is the "
            "runtime's own busy_timeout and is expected to wait and succeed"
        ),
    })

    # 4. A stale lease: a worker that claimed and never came back.
    conn = sqlite3.connect(db, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO cognitive_events(event_id, tenant_id, workspace_id, actor, source,"
        " payload_json, trust_tier, security_label, valid_from, valid_to, observed_at,"
        " checksum, metadata_json, modality, epistemic_status)"
        " VALUES('stale-evt','tenant_00','tenant_00_ws_00','ghost','bench',"
        " '{\"content\": \"stale lease probe\"}', 2, 'internal', NULL, NULL,"
        " '2026-08-05T00:00:00Z', 'stale-checksum-probe', '{}', 'text', 'observed')"
    )
    conn.commit()
    stale_job = ProjectionJob(
        event_id="stale-evt", projection_type=COGNITIVE_MEMORY, tenant_id="tenant_00",
    )
    conn.execute(
        "INSERT INTO projection_outbox(job_id, event_id, projection_type,"
        " projection_version, projection_key, tenant_id, status, attempts, available_at,"
        " locked_by, locked_at, last_error, payload_json, created_at, completed_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stale_job.job_id, "stale-evt", COGNITIVE_MEMORY, 1, stale_job.key, "tenant_00",
         JobStatus.IN_PROGRESS.value, 1, time.time() - 600, "dead-worker",
         time.time() - 600, None, "{}", time.time() - 600, None),
    )
    conn.commit()
    conn.close()

    stale_before = harness.queue_counts(db)
    gate3 = ctx.Event()
    specs3 = [{
        "db": db, "mode": mode, "worker_id": "stale-reclaimer", "shadow": shadow,
        "out": str(work / "stale_worker.json"), "start_event": gate3,
        "batch_size": 10, "idle_polls": 4, "max_seconds": 120, "lease_seconds": 30.0,
    }]
    procs3 = harness.spawn(harness.worker_main, specs3)
    time.sleep(1.0)
    stale_start = time.time()
    gate3.set()
    harness.join_all(procs3, timeout=300)
    stale_after = harness.queue_counts(db)
    scenarios.append({
        "scenario": "stale_lease_reclaimed",
        "reclaim_seconds": round(time.time() - stale_start, 3),
        "in_progress_before": stale_before["in_progress"],
        "in_progress_after": stale_after["in_progress"],
        "reclaimed": stale_before["in_progress"] - stale_after["in_progress"],
    })

    # 5. The same event enqueued twice.
    runtime = MemoryOS(db, projection_mode=mode)
    target_event = event_ids[0]
    tenant = target_event and runtime.events.conn.execute(
        "SELECT tenant_id FROM cognitive_events WHERE event_id=?", (target_event,)
    ).fetchone()["tenant_id"]
    before_jobs = len(runtime.events.outbox.by_event(target_event))
    for _ in range(3):
        runtime.events.outbox.enqueue(
            ProjectionJob(event_id=target_event, projection_type=COGNITIVE_MEMORY,
                          tenant_id=tenant),
            commit=True,
        )
    after_jobs = len(runtime.events.outbox.by_event(target_event))
    projections = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger WHERE event_id=?", (target_event,)
    ).fetchone()[0]
    runtime.close()
    scenarios.append({
        "scenario": "duplicate_enqueue",
        "enqueue_attempts": 3,
        "jobs_before": before_jobs,
        "jobs_after": after_jobs,
        "duplicate_jobs_created": after_jobs - before_jobs,
        "ledger_rows_for_event": int(projections),
    })

    # 6. Crash after the projection commits, before the outbox completes —
    #    the exact window the ledger exists to close, now under a loaded queue.
    runtime = MemoryOS(db, projection_mode=mode)
    crash_ids = []
    for obs in corpus.generate(200, seed=seed + 99, start=900_000):
        crash_ids.append(runtime.observe(
            tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
            content=obs.content, workspace_id=obs.workspace_id,
        ).event_id)
    runtime.close()
    marker = work / "crash_marker.txt"
    crash_proc = ctx.Process(target=crash_worker_main, args=({
        "db": db, "mode": mode, "worker_id": "crasher", "shadow": shadow,
        "fault_point": _fault.ProjectionFaultPoint.BEFORE_OUTBOX_COMPLETE.value,
        "after_firings": 3, "marker": str(marker), "lease_seconds": 2.0,
        "batch_size": 5, "max_seconds": 120,
    },))
    crash_proc.start()
    crash_proc.join(timeout=180)
    crashed = marker.exists() and crash_proc.exitcode == 70
    mid_crash = harness.queue_counts(db)

    gate4 = ctx.Event()
    specs4 = [{
        "db": db, "mode": mode, "worker_id": "post-crash", "shadow": shadow,
        "out": str(work / "post_crash_worker.json"), "start_event": gate4,
        "batch_size": 50, "max_seconds": 300, "lease_seconds": 2.0,
        "idle_polls": 200, "poll_seconds": 0.05,   # longer than the lease
    }]
    procs4 = harness.spawn(harness.worker_main, specs4)
    time.sleep(1.0)
    gate4.set()
    harness.join_all(procs4, timeout=600)
    post_crash = harness.queue_counts(db)
    post_results = harness.read_results([Path(s["out"]) for s in specs4])
    scenarios.append({
        "scenario": "crash_after_projection_commit_before_outbox_complete",
        "crashed_as_designed": crashed,
        "exit_code": crash_proc.exitcode,
        "queue_mid_crash": mid_crash,
        "queue_after_recovery": post_crash,
        "already_built_on_retry": sum(r["metrics"]["already_built"] for r in post_results),
    })

    # 7. Full runtime restart, then integrity.
    restart_runtime = MemoryOS(db, projection_mode=mode)
    events_after = restart_runtime.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0]
    restart_runtime.close()

    conn = sqlite3.connect(db, timeout=60.0)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    duplicate_ledgers = conn.execute(
        "SELECT COUNT(*) FROM (SELECT projection_key FROM projection_ledger"
        " GROUP BY projection_key HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    duplicate_projections = conn.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT json_extract(metadata_json,'$.projection_key') AS k, COUNT(*) AS n"
        "  FROM cognitive_memories"
        "  WHERE json_extract(metadata_json,'$.projection_key') IS NOT NULL"
        "  GROUP BY k HAVING n > 1)"
    ).fetchone()[0] if _has_table(conn, "cognitive_memories") else 0
    conn.close()

    final_counts = harness.queue_counts(db)
    scenarios.append({
        "scenario": "full_runtime_restart",
        "events_readable_after_restart": int(events_after),
        "sqlite_integrity_check": integrity,
        "duplicate_ledger_rows": int(duplicate_ledgers),
        "duplicate_projections": int(duplicate_projections),
    })

    return {
        "workload": "E_recovery",
        "events": events,
        "workers": workers,
        "baseline_queue": baseline,
        "final_queue": final_counts,
        "events_appended": events + 200 + 1,
        "events_present": int(events_after),
        "lost_events": (events + 200 + 1) - int(events_after),
        "scenarios": scenarios,
        "doctor_after": doctor_digest(run_doctor(db, deep=True)),
        "database": environment.database_footprint(db),
        "db_path": db,
    }


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


__all__ = [
    "crash_worker_main",
    "lock_holder_main",
    "workload_e_recovery",
]
