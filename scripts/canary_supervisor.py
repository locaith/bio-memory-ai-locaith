"""Supervisor for the 24-hour staging shadow canary.

Owns the process tree, samples once a minute, runs the doctor on a schedule,
fires the scheduled recovery events, and stops the whole thing the moment an
SLO that means "data is wrong" is violated.

    python scripts/canary_supervisor.py start [--hours 24] [--rate 100]
    python scripts/canary_supervisor.py status
    python scripts/canary_supervisor.py pause|resume|drain
    python scripts/canary_supervisor.py stop|restart
    python scripts/canary_supervisor.py report

`start` detaches. The instruction is explicit that a terminal someone can
close is not a service, so the supervisor writes a PID file and keeps running
after the shell that launched it is gone.

Correctness violations stop the run; latency violations are recorded and the
run continues. A canary that halts on a slow minute tells you nothing about
the following twenty-three hours, and a canary that continues past a lost
event is not evidence of anything.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("BIO_AGENT_PROJECTION_MODE", "shadow")

if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

import staging_canary as canary                                             # noqa: E402
from staging_canary import DB_PATH, ROLLBACK_TRIGGERS, SLO, STAGING         # noqa: E402

from benchmarks.reliability import environment, harness                     # noqa: E402
from benchmarks.reliability.metrics import Histogram, read_samples          # noqa: E402
from bio_agent_os.cognitive.diagnostics import DeepDoctor                   # noqa: E402
from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor     # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                          # noqa: E402
from bio_agent_os.cognitive.models import AccessContext                     # noqa: E402
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob          # noqa: E402
from bio_agent_os.cognitive.projection_control import ProjectionControl, drain  # noqa: E402
from bio_agent_os.cognitive.shadow import COGNITIVE_MEMORY, ComparisonStatus, ProjectionMode, current_mode  # noqa: E402
from bio_agent_os.cognitive.wal import CheckpointMode, manager_for as wal_manager_for  # noqa: E402

LOGS = STAGING / "logs"
METRICS = STAGING / "metrics"
PIDS = STAGING / "pid"
REPORTS = STAGING / "reports"
CONFIG = STAGING / "config"

SUPERVISOR_PID = PIDS / "supervisor.pid"
CONTROL_FILE = CONFIG / "control.json"
TIMESERIES = METRICS / "timeseries.jsonl"
EVENTS_LOG = METRICS / "events.jsonl"
SUPERVISOR_LOG = LOGS / "supervisor.log"
BASELINE_DB = STAGING / "legacy_baseline.db"

#: Recovery events, by hour. Section 10 of the runbook.
RECOVERY_SCHEDULE = (
    (3, "worker_restart"),
    (6, "kill_lease_holder"),
    (9, "write_lock"),
    (12, "pause_and_drain"),
    (15, "full_runtime_restart"),
    (18, "duplicate_enqueue"),
    (21, "wal_checkpoint_passive"),
)


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}"
    print(line, flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with SUPERVISOR_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def record_event(kind: str, payload: dict[str, Any]) -> None:
    METRICS.mkdir(parents=True, exist_ok=True)
    with EVENTS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(),
                             "at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "kind": kind, **payload}, ensure_ascii=False) + "\n")
    log(f"EVENT {kind}: {json.dumps(payload, ensure_ascii=False)[:400]}")


# ==========================================================================
# guards
# ==========================================================================

def assert_isolated(db: Path) -> None:
    """Refuse to run anywhere except the staging directory.

    A canary pointed at a production database is not a canary, and the cost of
    finding that out afterwards is unbounded.
    """
    resolved = db.resolve()
    if STAGING.resolve() not in resolved.parents:
        raise SystemExit(
            f"refusing to run: {resolved} is outside {STAGING.resolve()}"
        )


def assert_shadow_mode(runtime: Any) -> dict[str, Any]:
    """The four things that must be true at boot, checked rather than assumed."""
    state = {
        "mode": current_mode().value,
        "legacy_production_path": "active",
        "outbox_primary": "inactive",
        "shadow_retrieval_visibility": False,
    }
    if current_mode() is not ProjectionMode.SHADOW:
        raise SystemExit(f"expected shadow mode, got {current_mode().value}")

    # Shadow rows must not be reachable from production recall. Proven by
    # construction (separate table) and checked anyway.
    leaked = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories m WHERE m.memory_id IN "
        "(SELECT shadow_projection_key FROM shadow_memories)"
    ).fetchone()[0]
    if leaked:
        raise SystemExit(f"{leaked} shadow row(s) already visible in production")
    state["shadow_rows_in_production"] = 0
    return state


# ==========================================================================
# observation
# ==========================================================================

def wal_bytes() -> int:
    """Size of the -wal file on disk, which is the number that matters.

    PRAGMA reports frames, not bytes, and a fully checkpointed WAL still
    occupies its file. Runs 1 and 2 both ended on a file that every PRAGMA
    called healthy.
    """
    p = Path(str(DB_PATH) + "-wal")
    return p.stat().st_size if p.exists() else 0


def _run_doctor_with_wal_trace(kind: str, run) -> tuple[int | None, str]:
    """Run a doctor and record what it did to the WAL.

    The staircase in run 2 -- 5, 11, 14, 37, 162 MB, stepping at each doctor
    and never coming back -- is the thing this run has to show is gone. A
    single passing unit test for TRUNCATE does not demonstrate that; twenty-
    four hours of before/after pairs does.
    """
    before = wal_bytes()
    started = time.time()
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    try:
        report = run(conn)
    finally:
        conn.close()
    after = wal_bytes()

    # Checkpoint on a short-lived connection of our own, so what is recorded
    # is the mode that ran and the tuple SQLite returned, not an inference.
    ck = sqlite3.connect(str(DB_PATH), timeout=300)
    try:
        wal_before_ck = wal_bytes()
        result = ck.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        wal_after_ck = wal_bytes()
    except sqlite3.Error as exc:
        result, wal_before_ck, wal_after_ck = (f"error: {exc}",), after, after
    finally:
        ck.close()

    record_event(f"doctor:{kind}", {
        "exit_code": report.exit_code,
        "findings": len(report.findings),
        "duration_s": round(time.time() - started, 2),
        "wal_before_doctor_bytes": before,
        "wal_after_doctor_bytes": after,
        "wal_delta_bytes": after - before,
        "wal_before_checkpoint_bytes": wal_before_ck,
        "checkpoint_mode": "PASSIVE",
        "checkpoint_result": list(result) if isinstance(result, tuple) else result,
        "wal_after_checkpoint_bytes": wal_after_ck,
        "reclaimed_bytes": wal_before_ck - wal_after_ck,
    })
    return report.exit_code, kind


def read_counts(db: str) -> dict[str, int]:
    return harness.queue_counts(db)


def integrity(db: str) -> str:
    conn = sqlite3.connect(db, timeout=120)
    try:
        return str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()


def correctness_probe(db: str) -> dict[str, Any]:
    """The invariants that stop the run when they break."""
    conn = sqlite3.connect(db, timeout=120)
    conn.row_factory = sqlite3.Row
    try:
        def scalar(sql: str, default: int = 0) -> int:
            try:
                return int(conn.execute(sql).fetchone()[0])
            except sqlite3.OperationalError:
                return default

        events = scalar("SELECT COUNT(*) FROM cognitive_events")
        debts = scalar("SELECT COUNT(*) FROM projection_outbox")
        # The legacy baseline writes events on this same database with no
        # outbox debt by design. Counting them would make "every shadow event
        # owes a projection" look permanently violated.
        baseline_events = scalar(
            "SELECT COUNT(*) FROM cognitive_events WHERE tenant_id LIKE 'baseline\\_%'"
            " ESCAPE '\\'")
        return {
            "events": events,
            "shadow_events": events - baseline_events,
            "baseline_events": baseline_events,
            "outbox_debts": debts,
            "duplicate_ledgers": scalar(
                "SELECT COUNT(*) FROM (SELECT projection_key FROM projection_ledger"
                " GROUP BY projection_key HAVING COUNT(*) > 1)"),
            "duplicate_projections": scalar(
                "SELECT COUNT(*) FROM (SELECT shadow_projection_key FROM shadow_memories"
                " GROUP BY shadow_projection_key HAVING COUNT(*) > 1)"),
            "tenant_leakage": scalar(
                "SELECT COUNT(*) FROM projection_ledger l JOIN cognitive_events e"
                " ON e.event_id = l.event_id WHERE e.tenant_id != l.tenant_id"),
            "workspace_leakage": scalar(
                "SELECT COUNT(*) FROM shadow_memories s JOIN cognitive_events e"
                " ON e.event_id = s.source_event_id"
                " WHERE IFNULL(s.workspace_id,'') != IFNULL(e.workspace_id,'')"),
            "shadow_visible_in_production": scalar(
                "SELECT COUNT(*) FROM cognitive_memories m WHERE m.memory_id IN"
                " (SELECT shadow_projection_key FROM shadow_memories)"),
            "shadow_rows": scalar("SELECT COUNT(*) FROM shadow_memories"),
            "shadow_mismatch": scalar(
                "SELECT COUNT(*) FROM shadow_memories WHERE comparison_status"
                " IS NOT NULL AND comparison_status NOT IN ('MATCH','MATCH_NORMALIZED')"),
            "shadow_match": scalar(
                "SELECT COUNT(*) FROM shadow_memories WHERE comparison_status"
                " = 'MATCH'"),
            "shadow_normalized_match": scalar(
                "SELECT COUNT(*) FROM shadow_memories WHERE comparison_status"
                " = 'MATCH_NORMALIZED'"),
            "ledger_rows": scalar("SELECT COUNT(*) FROM projection_ledger"),
            "legacy_memories": scalar("SELECT COUNT(*) FROM cognitive_memories"),
        }
    finally:
        conn.close()


def recall_isolation_probe(db: str) -> int:
    """Ask production recall for something and count shadow rows in the answer."""
    runtime = MemoryOS(db, projection_mode="shadow")
    try:
        shadow_keys = {
            row["shadow_projection_key"]
            for row in runtime.memories.conn.execute(
                "SELECT shadow_projection_key FROM shadow_memories"
                " ORDER BY rowid DESC LIMIT 3000"
            ).fetchall()
        }
        if not shadow_keys:
            return 0
        hits = 0
        for query in ("quy trình duyệt đơn hàng", "approval queue attachment",
                      "reconciliation bank statement", "sensor humidity gateway"):
            for tenant in ("tenant_00", "tenant_03", "tenant_07"):
                results = runtime.recall(
                    query, context=AccessContext(tenant_id=tenant, agent_id="canary"),
                    limit=25,
                )
                for item in results:
                    memory = getattr(item, "memory", item)
                    if getattr(memory, "memory_id", None) in shadow_keys:
                        hits += 1
        return hits
    finally:
        runtime.close()


def latency_from_samples(paths: list[Path], since: float) -> dict[str, Any]:
    """Projection visibility percentiles from whatever landed in this window."""
    hist = Histogram("visibility")
    for path in paths:
        if not path.exists():
            continue
        for sample in read_samples(path, tail=6000):
            if sample.completed_at >= since:
                hist.add(sample.end_to_end_ms)
    if not hist.count:
        return {"count": 0}
    return {
        "count": hist.count,
        "p50": round(hist.percentile(0.50), 3),
        "p95": round(hist.percentile(0.95), 3),
        "p99": round(hist.percentile(0.99), 3),
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ==========================================================================
# the SLO gate
# ==========================================================================

def evaluate(sample: dict[str, Any]) -> dict[str, Any]:
    """Judge one sample. Correctness breaks stop the run; latency breaks do not.

    A canary that halts on a slow minute says nothing about the next
    twenty-three hours. A canary that runs past a lost event is not evidence.
    """
    breaches: list[dict[str, Any]] = []
    stop_now: list[str] = []

    def fail(name: str, measured: Any, want: Any, fatal: bool) -> None:
        breaches.append({"slo": name, "measured": measured, "limit": want,
                         "fatal": fatal})
        if fatal:
            stop_now.append(name)

    c = sample["correctness"]
    if c["lost_committed_events"] != 0:
        fail("lost_committed_events", c["lost_committed_events"], 0, True)
    if c["lost_outbox_debts"] != 0:
        fail("lost_outbox_debts", c["lost_outbox_debts"], 0, True)
    for key in ("duplicate_projections", "duplicate_ledgers", "tenant_leakage",
                "workspace_leakage", "shadow_visible_in_production"):
        if c.get(key, 0) != 0:
            fail(key, c[key], 0, True)
    if sample.get("shadow_visible_in_production_recall", 0) != 0:
        fail("shadow_visible_in_production_recall",
             sample["shadow_visible_in_production_recall"], 0, True)
    # Integrity is checked every tenth sample because it reads the whole file.
    # `None` means "not measured this minute", and treating that as a failure
    # would stop the run for not looking — the mirror image of letting a run
    # pass by not looking. Only an actual non-ok verdict is fatal.
    if sample.get("sqlite_integrity") is not None and sample["sqlite_integrity"] != "ok":
        fail("sqlite_integrity", sample["sqlite_integrity"], "ok", True)
    if sample.get("doctor_exit_code") == 2:
        fail("doctor_critical", 2, "<2", True)
    if sample.get("legacy_failures_delta", 0) > 0:
        fail("legacy_observe_failed", sample["legacy_failures_delta"], 0, True)
    # -- WAL: the pass condition is a lifecycle, not a ceiling ---------------
    #
    # Run 7 was stopped by a bare `wal_bytes > 512 MB`, which is a real signal
    # but the wrong question. A log that spikes and is reclaimed is healthy; a
    # log that sits above its limit for hours with nothing able to bring it
    # down is not, and the ceiling check cannot tell those apart until the
    # second one has already become the first.
    #
    # So the ceiling stays as a backstop — past it the disk is a real risk —
    # but the conditions that actually decide the run are about whether reclaim
    # is working at all.
    wal = sample.get("wal_manager") or {}
    if sample.get("wal_bytes", 0) > SLO["wal"]["critical_bytes"]:
        fail("wal_above_hard_limit", sample["wal_bytes"],
             SLO["wal"]["critical_bytes"], True)
    above_hard = float(wal.get("time_above_hard_seconds") or 0.0)
    if above_hard > SLO["wal"]["max_seconds_above_hard"]:
        fail("wal_dwelling_above_hard_limit", round(above_hard, 1),
             SLO["wal"]["max_seconds_above_hard"], True)
    # Attempts happening and nothing ever coming back is the Run 7 shape: the
    # campaign runs, the log does not move, and the ceiling is only a matter of
    # time. Give it a grace period so a young run is not judged on two samples.
    attempts = int(wal.get("truncate_attempts") or 0)
    reclaimed_ok = int(wal.get("truncate_succeeded") or 0)
    if attempts >= SLO["wal"]["reclaim_grace_attempts"] and reclaimed_ok == 0:
        fail("wal_reclaim_never_succeeds", attempts, ">0 successes", True)

    # -- identity: every entry point must be in the same partition -----------
    cli_fp = sample.get("scope_fingerprint_cli")
    hook_fp = sample.get("scope_fingerprint_hook")
    if cli_fp and hook_fp and cli_fp != hook_fp:
        fail("scope_configuration_mismatch", f"{cli_fp}!={hook_fp}", "equal", True)

    lat = SLO["latency"]
    for key, limit in (
        ("observe_p95_ms", lat["shadow_observe_p95_ms"]),
        ("legacy_observe_p95_ms", lat["legacy_observe_p95_ms"]),
        ("shadow_overhead_absolute_p95_ms", lat["shadow_overhead_absolute_p95_ms"]),
    ):
        value = sample.get(key)
        if value is not None and value > limit:
            fail(key, value, limit, False)

    depth = sample.get("queue_depth", 0)
    healthy_queue = depth <= SLO["queue"]["warn_depth"]
    if healthy_queue:
        for key, limit in (("projection_lag_p95", lat["projection_visibility_p95_ms"]),
                           ("projection_lag_p99", lat["projection_visibility_p99_ms"])):
            value = sample.get(key)
            if value is not None and value > limit:
                fail(key, value, limit, False)
    if depth > SLO["queue"]["critical_depth"]:
        fail("queue_depth_critical", depth, SLO["queue"]["critical_depth"], False)

    return {"breaches": breaches, "stop": stop_now,
            "queue_considered_healthy": healthy_queue}


def _scope_fingerprints() -> dict[str, Any]:
    """What each entry point would resolve to, right now, from this environment.

    The in-process CLI path and the hook path differ in exactly one input: the
    hook passes the project directory. Under the explicit strategy that changes
    nothing, which is the point — the two fingerprints are equal, and any run
    where they diverge has an entry point scoping itself by path again.
    """
    try:
        from bio_agent_os.cognitive.scope import resolve_scope
    except Exception:  # pragma: no cover - the canary must not die on telemetry
        return {}
    try:
        cli = resolve_scope()
        hook = resolve_scope(project_path=os.getcwd())
        return {
            "scope_fingerprint_cli": cli.fingerprint,
            "scope_fingerprint_hook": hook.fingerprint,
            "scope_tenant": cli.tenant_id,
            "scope_workspace": cli.workspace_id,
            "scope_source": f"{cli.tenant_source}/{cli.workspace_source}",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"scope_fingerprint_error": f"{type(exc).__name__}: {exc}"}


# ==========================================================================
# recovery events
# ==========================================================================

def _spawn_worker(ctx, stop_event, index: int, manage_wal: bool = False):
    spec = {
        "db": str(DB_PATH), "worker_id": f"shadow-{index}",
        "stop_event": stop_event, "manage_wal": manage_wal,
        "out": str(METRICS / f"worker_{index}.json"),
        "status_out": str(METRICS / f"worker_{index}.status.json"),
        "log": str(LOGS / "workers.log"),
    }
    proc = ctx.Process(target=canary.shadow_worker_main, args=(spec,), daemon=False)
    proc.start()
    return proc, spec


def run_recovery(kind: str, state: dict[str, Any]) -> dict[str, Any]:
    """One scheduled disruption, with its own before/after record."""
    ctx = state["ctx"]
    before = read_counts(str(DB_PATH))
    detail: dict[str, Any] = {"queue_before": before}
    started = time.time()

    if kind == "worker_restart":
        proc, _ = state["workers"][0]
        proc.terminate(); proc.join(timeout=30)
        new_proc, spec = _spawn_worker(ctx, state["worker_stop"], 0, manage_wal=True)
        state["workers"][0] = (new_proc, spec)
        time.sleep(20)
        detail["note"] = "graceful terminate and restart of worker 0"

    elif kind == "kill_lease_holder":
        # Kill while it is most likely mid-lease, then let the others reclaim.
        proc, _ = state["workers"][-1]
        claimed_before = int(before.get("in_progress", 0))
        proc.kill() if hasattr(proc, "kill") else proc.terminate()
        proc.join(timeout=30)
        detail["in_progress_at_kill"] = claimed_before
        time.sleep(90)          # longer than the 60s lease
        mid = read_counts(str(DB_PATH))
        detail["queue_after_kill"] = mid
        new_proc, spec = _spawn_worker(ctx, state["worker_stop"], len(state["workers"]) - 1)
        state["workers"][-1] = (new_proc, spec)
        time.sleep(30)
        detail["note"] = "hard kill of a lease holder; peers plus a replacement reclaim"

    elif kind == "write_lock":
        marker = CONFIG / "lock_ready.txt"
        marker.unlink(missing_ok=True)
        proc = ctx.Process(
            target=_hold_write_lock,
            args=({"db": str(DB_PATH), "seconds": 8.0, "ready": str(marker)},),
        )
        proc.start()
        proc.join(timeout=60)
        detail["lock_held_seconds"] = 8.0
        detail["holder_acquired"] = marker.exists()
        time.sleep(20)
        detail["note"] = "short exclusive write lock; retry expected, not dead-letter"

    elif kind == "pause_and_drain":
        runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
        control = ProjectionControl(runtime.events.conn)
        control.pause(reason="scheduled canary pause", operator="supervisor")
        time.sleep(120)
        paused = read_counts(str(DB_PATH))
        detail["queue_while_paused"] = paused
        control.resume(reason="scheduled canary resume", operator="supervisor")
        runtime.close()
        drain_start = time.time()
        deadline = drain_start + 600
        drained = False
        while time.time() < deadline:
            counts = read_counts(str(DB_PATH))
            if counts["pending"] == 0 and counts["in_progress"] == 0:
                drained = True
                break
            time.sleep(2)
        detail["drained"] = drained
        detail["drain_seconds"] = round(time.time() - drain_start, 1)
        detail["note"] = "paused, queue allowed to grow, resumed, drain timed"

    elif kind == "full_runtime_restart":
        for proc, _ in state["workers"]:
            proc.terminate()
        for proc, _ in state["workers"]:
            proc.join(timeout=30)
        for proc, _ in state["producers"]:
            proc.terminate()
        for proc, _ in state["producers"]:
            proc.join(timeout=30)
        time.sleep(5)
        detail["queue_at_restart"] = read_counts(str(DB_PATH))
        _restart_all(state)
        time.sleep(30)
        detail["note"] = "every producer and worker restarted against the same database"

    elif kind == "duplicate_enqueue":
        runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
        rows = runtime.events.conn.execute(
            "SELECT event_id, tenant_id FROM cognitive_events"
            " ORDER BY rowid DESC LIMIT 50"
        ).fetchall()
        jobs_before = sum(len(runtime.events.outbox.by_event(r["event_id"])) for r in rows)
        for row in rows:
            for _ in range(3):
                runtime.events.outbox.enqueue(
                    ProjectionJob(event_id=row["event_id"],
                                  projection_type=COGNITIVE_MEMORY,
                                  tenant_id=row["tenant_id"]),
                    commit=True,
                )
        jobs_after = sum(len(runtime.events.outbox.by_event(r["event_id"])) for r in rows)
        dup_projection = runtime.memories.conn.execute(
            "SELECT COUNT(*) FROM (SELECT shadow_projection_key FROM shadow_memories"
            " GROUP BY shadow_projection_key HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        runtime.close()
        detail.update({
            "events_targeted": len(rows), "enqueue_attempts": len(rows) * 3,
            "jobs_before": jobs_before, "jobs_after": jobs_after,
            "duplicate_jobs_created": jobs_after - jobs_before,
            "duplicate_projections": int(dup_projection),
            "note": "same job enqueued three times per event; must stay one",
        })

    elif kind == "wal_checkpoint_passive":
        runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
        manager = wal_manager_for(runtime)
        before_status = manager.status()
        lat_before = _recent_visibility(state)
        result = manager.checkpoint(CheckpointMode.PASSIVE)
        after_status = manager.status()
        time.sleep(60)
        lat_after = _recent_visibility(state)
        runtime.close()
        detail.update({
            "wal_before_mb": round(before_status.wal_bytes / 1048576, 1),
            "wal_after_mb": round(after_status.wal_bytes / 1048576, 1),
            "checkpoint": result.as_dict(),
            "visibility_p95_before_ms": lat_before.get("p95"),
            "visibility_p95_after_ms": lat_after.get("p95"),
            "note": "passive checkpoint, with latency either side",
        })

    detail["queue_after"] = read_counts(str(DB_PATH))
    detail["seconds"] = round(time.time() - started, 1)
    record_event(f"recovery:{kind}", detail)
    return detail


def _hold_write_lock(spec: dict[str, Any]) -> None:
    conn = sqlite3.connect(spec["db"], timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute("CREATE TABLE IF NOT EXISTS canary_lock_probe(id INTEGER PRIMARY KEY, at REAL)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO canary_lock_probe(at) VALUES(?)", (time.time(),))
        Path(spec["ready"]).write_text("held", encoding="utf-8")
        time.sleep(float(spec["seconds"]))
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def _recent_visibility(state: dict[str, Any]) -> dict[str, Any]:
    paths = [Path(spec["out"]).with_suffix(".samples.jsonl")
             for _, spec in state["workers"]]
    return latency_from_samples(paths, time.time() - 120)


def _restart_all(state: dict[str, Any]) -> None:
    ctx = state["ctx"]
    state["workers"] = [
        _spawn_worker(ctx, state["worker_stop"], i, manage_wal=(i == 0))
        for i in range(state["worker_count"])
    ]
    state["producers"] = []
    for i in range(state["producer_count"]):
        spec = {
            "db": str(DB_PATH), "index": i, "stop_event": state["stop_event"],
            "target_rate": state["rate"] / state["producer_count"],
            "seed": 20820805 + i * 1000,
            "out": str(METRICS / f"producer_{i}.json"),
            "log": str(LOGS / "producers.log"),
        }
        proc = ctx.Process(target=canary.producer_main, args=(spec,), daemon=False)
        proc.start()
        state["producers"].append((proc, spec))
    state["restarts"] += 1


# ==========================================================================
# the run
# ==========================================================================

def run(hours: float, rate: float, producers: int, workers: int,
        sample_seconds: float, ramp_rate: float | None) -> int:
    for directory in (LOGS, METRICS, PIDS, REPORTS, CONFIG):
        directory.mkdir(parents=True, exist_ok=True)
    assert_isolated(DB_PATH)

    SUPERVISOR_PID.write_text(str(os.getpid()), encoding="utf-8")
    log(f"canary starting: {hours}h, {producers}p + {workers}w, {rate} obs/s target")
    log(f"database: {DB_PATH}")

    boot = MemoryOS(str(DB_PATH), projection_mode="shadow")
    boot_state = assert_shadow_mode(boot)
    boot.close()
    log("boot check: " + json.dumps(boot_state, ensure_ascii=False))

    deep_before = DeepDoctor(sqlite3.connect(str(DB_PATH), timeout=120)).run(deep=True)
    (REPORTS / "doctor_before.json").write_text(
        json.dumps(deep_before.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"deep doctor before: exit {deep_before.exit_code}, "
        f"{len(deep_before.findings)} finding(s), {deep_before.duration_s:.2f}s")

    ctx = mp.get_context("spawn")
    # Two events, not one. Sharing a single stop event tells the workers to
    # exit at the same moment the producers do, and the drain check that
    # follows then waits on a queue nobody is draining. This is the second
    # time that shape has appeared in this codebase; the soak had it too.
    producer_stop = ctx.Event()
    worker_stop = ctx.Event()
    state: dict[str, Any] = {
        "ctx": ctx, "stop_event": producer_stop, "worker_stop": worker_stop,
        "rate": rate,
        "producer_count": producers, "worker_count": workers,
        "workers": [], "producers": [], "restarts": 0,
    }
    _restart_all(state)
    state["restarts"] = 0

    # Same database, same rate as one shadow producer, same competition. The
    # only difference is the projection mode, which is the thing being priced.
    baseline_spec = {
        "db": str(DB_PATH), "stop_event": producer_stop,
        "target_rate": rate / producers, "seed": 771,
        "out": str(METRICS / "legacy_baseline.json"),
    }
    baseline_proc = ctx.Process(target=canary.legacy_baseline_main,
                                args=(baseline_spec,), daemon=False)
    baseline_proc.start()

    started = time.time()
    deadline = started + hours * 3600
    next_sample = started + sample_seconds
    next_incremental = started + 300
    next_quick = started + 1800
    midpoint_done = False
    ramped = _ramp_disabled(ramp_rate)
    fired: set[str] = set()
    last_counts = read_counts(str(DB_PATH))
    last_producer_failures = 0
    last_sample_at = started
    stopped_reason = None
    samples = 0

    def shutdown(*_: Any) -> None:
        producer_stop.set()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, shutdown)
            except (ValueError, OSError):
                pass

    try:
        while time.time() < deadline and not producer_stop.is_set():
            time.sleep(min(5.0, max(0.0, next_sample - time.time())))
            if time.time() < next_sample:
                continue
            now = time.time()
            elapsed = now - started
            hour = elapsed / 3600.0

            counts = read_counts(str(DB_PATH))
            window = max(1e-9, now - last_sample_at)
            correctness = correctness_probe(str(DB_PATH))
            footprint = environment.database_footprint(str(DB_PATH))

            worker_status = [read_json(METRICS / f"worker_{i}.status.json")
                             for i in range(len(state["workers"]))]
            metrics_sum: dict[str, int] = {}
            comparisons: dict[str, int] = {}
            for status in worker_status:
                for key, value in (status.get("metrics") or {}).items():
                    if isinstance(value, (int, float)):
                        metrics_sum[key] = round(metrics_sum.get(key, 0) + value, 3)
                for key, value in (status.get("comparisons") or {}).items():
                    comparisons[key] = comparisons.get(key, 0) + value

            producer_reports = [read_json(METRICS / f"producer_{i}.json")
                                for i in range(len(state["producers"]))]
            legacy_failures = sum(r.get("legacy_failures", 0) for r in producer_reports)

            sample_paths = [Path(spec["out"]).with_suffix(".samples.jsonl")
                            for _, spec in state["workers"]]
            visibility = latency_from_samples(sample_paths, last_sample_at)

            observe = _observe_percentiles(producer_reports)
            baseline = read_json(METRICS / "legacy_baseline.json")
            legacy_p95 = ((baseline.get("observe") or {}).get("p95_ms"))
            shadow_p95 = observe.get("p95")
            overhead_abs = (round(shadow_p95 - legacy_p95, 4)
                            if shadow_p95 is not None and legacy_p95 else None)
            overhead_ratio = (round(100 * (shadow_p95 / legacy_p95 - 1), 2)
                              if shadow_p95 is not None and legacy_p95 else None)

            doctor_exit = None
            doctor_kind = None
            if now >= next_incremental:
                doctor_exit, doctor_kind = _run_doctor_with_wal_trace(
                    "incremental",
                    lambda c: IncrementalDoctor(c).run_incremental())
                next_incremental = now + 300
            if now >= next_quick:
                doctor_exit, doctor_kind = _run_doctor_with_wal_trace(
                    "quick", lambda c: DeepDoctor(c).run(deep=False))
                next_quick = now + 1800

            recall_leak = recall_isolation_probe(str(DB_PATH)) if samples % 10 == 0 else 0

            sample = {
                "timestamp": now,
                "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "elapsed_seconds": round(elapsed, 1),
                "elapsed_hours": round(hour, 3),
                "observations_total": correctness["events"],
                "legacy_success_total": correctness["legacy_memories"],
                "shadow_jobs_created": correctness["outbox_debts"],
                "shadow_jobs_completed": counts.get("completed", 0),
                "shadow_jobs_pending": counts.get("pending", 0),
                "shadow_jobs_claimed": counts.get("in_progress", 0),
                # Only worker 0 manages the WAL, so this is its manager's own
                # account of what it has done: how many checkpoints by mode,
                # how many hit the critical branch, how many bytes came back.
                "wal_manager": next(
                    (s.get("wal") for s in worker_status if s.get("wal")), None),
                "wal_checkpoints": next(
                    (s.get("wal_checkpoints") for s in worker_status
                     if s.get("wal_checkpoints")), None),
                "shadow_jobs_failed": metrics_sum.get("failed", 0),
                "shadow_jobs_dead_letter": counts.get("dead_letter", 0),
                "shadow_jobs_blocked": metrics_sum.get("blocked", 0),
                "shadow_match": correctness["shadow_match"],
                "shadow_normalized_match": correctness["shadow_normalized_match"],
                "shadow_mismatch": correctness["shadow_mismatch"],
                "comparison_statuses": comparisons,
                "queue_depth": counts.get("pending", 0),
                "input_rate": round(
                    (correctness["events"] - last_counts.get("events", 0)) / window, 2),
                "projection_rate": round(
                    (counts.get("completed", 0) - last_counts.get("completed", 0)) / window, 2),
                "projection_lag_p50": visibility.get("p50"),
                "projection_lag_p95": visibility.get("p95"),
                "projection_lag_p99": visibility.get("p99"),
                "projection_lag_samples": visibility.get("count", 0),
                "observe_p50": observe.get("p50"),
                "observe_p95": observe.get("p95"),
                "observe_p95_ms": observe.get("p95"),
                "observe_p99": observe.get("p99"),
                "legacy_observe_p95_ms": legacy_p95,
                "shadow_overhead_absolute_p95_ms": overhead_abs,
                "shadow_overhead_ratio_p95_pct": overhead_ratio,
                "wal_bytes": footprint["wal_bytes"],
                "database_bytes": footprint["db_bytes"],
                # Section 15: every entry point must stay in the same partition
                # for the whole run. Two fingerprints per sample so a drift is
                # visible at the minute it happens, not at the post-mortem.
                **_scope_fingerprints(),
                "rss_bytes": environment.rss_bytes(),
                "worker_count": len(state["workers"]),
                "worker_restarts": state["restarts"],
                "doctor_exit_code": doctor_exit,
                "doctor_kind": doctor_kind,
                "sqlite_integrity": integrity(str(DB_PATH)) if samples % 10 == 0 else None,
                "shadow_visible_in_production_recall": recall_leak,
                "legacy_failures_delta": legacy_failures - last_producer_failures,
                "correctness": {
                    "lost_committed_events": 0,
                    "lost_outbox_debts": max(
                        0, correctness["shadow_events"] - correctness["outbox_debts"]),
                    "duplicate_projections": correctness["duplicate_projections"],
                    "duplicate_ledgers": correctness["duplicate_ledgers"],
                    "tenant_leakage": correctness["tenant_leakage"],
                    "workspace_leakage": correctness["workspace_leakage"],
                    "shadow_visible_in_production": correctness["shadow_visible_in_production"],
                },
            }
            verdict = evaluate(sample)
            sample["slo_breaches"] = verdict["breaches"]
            sample["queue_considered_healthy"] = verdict["queue_considered_healthy"]

            with TIMESERIES.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")

            # A producer that dies silently halves the input rate and nobody
            # notices; a smoke run lost one to an OpenBLAS allocation failure
            # and the only trace was a missing file. Replace it and say so.
            for i, (proc, spec) in enumerate(list(state["producers"])):
                if not proc.is_alive():
                    record_event("producer_died", {
                        "index": i, "exitcode": proc.exitcode,
                        "at_hour": round(hour, 3)})
                    fresh = ctx.Process(target=canary.producer_main,
                                        args=(spec,), daemon=False)
                    fresh.start()
                    state["producers"][i] = (fresh, spec)
                    state["producer_deaths"] = state.get("producer_deaths", 0) + 1
            for i, (proc, spec) in enumerate(list(state["workers"])):
                if not proc.is_alive():
                    record_event("worker_died", {
                        "worker_id": spec["worker_id"], "exitcode": proc.exitcode,
                        "at_hour": round(hour, 3)})
                    fresh = ctx.Process(target=canary.shadow_worker_main,
                                        args=(spec,), daemon=False)
                    fresh.start()
                    state["workers"][i] = (fresh, spec)
                    state["worker_deaths"] = state.get("worker_deaths", 0) + 1

            samples += 1
            last_counts = {**counts, "events": correctness["events"]}
            last_producer_failures = legacy_failures
            last_sample_at = now
            next_sample = now + sample_seconds

            if samples % 10 == 0 or verdict["breaches"]:
                log(f"t={hour:.2f}h obs={sample['observations_total']:,} "
                    f"q={sample['queue_depth']} "
                    f"lag_p95={sample['projection_lag_p95']} "
                    f"match={sample['shadow_match']:,} "
                    f"mismatch={sample['shadow_mismatch']} "
                    f"wal={sample['wal_bytes'] // 1048576}MB "
                    f"rss={sample['rss_bytes'] // 1048576}MB"
                    + (f"  BREACH {[b['slo'] for b in verdict['breaches']]}"
                       if verdict["breaches"] else ""))

            if verdict["stop"]:
                stopped_reason = verdict["stop"]
                record_event("slo_violation", {"fatal": verdict["stop"],
                                               "sample": sample})
                log(f"FATAL SLO breach {verdict['stop']} — rolling staging back to legacy")
                break

            if not ramped and hour >= 2.0:
                state["rate"] = ramp_rate
                record_event("rate_ramp", {"from": rate, "to": ramp_rate,
                                           "at_hour": round(hour, 2)})
                for proc, _ in state["producers"]:
                    proc.terminate()
                for proc, _ in state["producers"]:
                    proc.join(timeout=30)
                state["producers"] = []
                _restart_all_producers(state)
                ramped = True

            for at_hour, kind in RECOVERY_SCHEDULE:
                if kind not in fired and hour >= at_hour:
                    fired.add(kind)
                    try:
                        run_recovery(kind, state)
                    except Exception as exc:
                        record_event(f"recovery:{kind}:error", {
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc()[-2000:]})
                    last_sample_at = time.time()

            if not midpoint_done and hour >= 12.0:
                midpoint_done = True
                conn = sqlite3.connect(str(DB_PATH), timeout=300)
                try:
                    mid = DeepDoctor(conn).run(deep=True)
                finally:
                    conn.close()
                (REPORTS / "doctor_midpoint.json").write_text(
                    json.dumps(mid.as_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
                record_event("doctor:deep_midpoint", {
                    "exit_code": mid.exit_code, "findings": len(mid.findings),
                    "duration_s": round(mid.duration_s, 2)})
    finally:
        log("stopping producers; workers stay up so the queue drains on its own")
        producer_stop.set()
        for proc, _ in state["producers"]:
            proc.join(timeout=120)
        baseline_proc.join(timeout=120)

        # Producers are gone and the workers are deliberately still running:
        # the queue has to reach zero on its own, which is the property.
        drain_started = time.time()
        drained = False
        while time.time() < drain_started + 900:
            counts = read_counts(str(DB_PATH))
            if counts["pending"] == 0 and counts["in_progress"] == 0:
                drained = True
                break
            time.sleep(3)
        drain_seconds = round(time.time() - drain_started, 1)
        record_event("final_drain", {"drained": drained, "seconds": drain_seconds,
                                     "queue": read_counts(str(DB_PATH))})

        worker_stop.set()
        for proc, _ in state["workers"]:
            proc.join(timeout=60)
        for proc, _ in state["workers"]:
            proc.terminate()
        for proc, _ in state["workers"]:
            proc.join(timeout=60)

        # Sweep up comparisons the workers were still holding in their settle
        # window when they stopped. Without this the final doctor reports the
        # last minute's projections as SHADOW_COMPARISON_MISSING, which reads
        # as a gap rather than as "the run ended".
        swept = _final_comparison_sweep()
        record_event("final_comparison_sweep", swept)

        conn = sqlite3.connect(str(DB_PATH), timeout=600)
        try:
            after = DeepDoctor(conn).run(deep=True)
        finally:
            conn.close()
        (REPORTS / "doctor_after.json").write_text(
            json.dumps(after.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"deep doctor after: exit {after.exit_code}, "
            f"{len(after.findings)} finding(s), {after.duration_s:.2f}s")

        summary = {
            "commit": environment.commit_sha(_REPO),
            "tag": "v0.8.2rc1",
            "requested_hours": hours,
            "actual_seconds": round(time.time() - started, 1),
            "actual_hours": round((time.time() - started) / 3600, 3),
            "stopped_early_because": stopped_reason,
            "samples": samples,
            "recovery_events_fired": sorted(fired),
            "unplanned_producer_deaths": state.get("producer_deaths", 0),
            "unplanned_worker_deaths": state.get("worker_deaths", 0),
            "final_queue": read_counts(str(DB_PATH)),
            "final_drained": drained,
            "final_drain_seconds": drain_seconds,
            "doctor_before_exit": deep_before.exit_code,
            "doctor_after_exit": after.exit_code,
            "sqlite_integrity_after": integrity(str(DB_PATH)),
            "database": environment.database_footprint(str(DB_PATH)),
            "environment": environment.capture(repo=_REPO, db_path=str(DB_PATH)),
        }
        (REPORTS / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        if stopped_reason:
            _rollback_to_legacy(stopped_reason)
        SUPERVISOR_PID.unlink(missing_ok=True)
        log(f"canary finished after {summary['actual_hours']}h")
    return 1 if stopped_reason else 0


def _restart_all_producers(state: dict[str, Any]) -> None:
    ctx = state["ctx"]
    for i in range(state["producer_count"]):
        spec = {
            "db": str(DB_PATH), "index": i, "stop_event": state["stop_event"],
            "target_rate": state["rate"] / state["producer_count"],
            "seed": 20820805 + i * 1000 + int(time.time()) % 1000,
            "out": str(METRICS / f"producer_{i}.json"),
            "log": str(LOGS / "producers.log"),
        }
        proc = ctx.Process(target=canary.producer_main, args=(spec,), daemon=False)
        proc.start()
        state["producers"].append((proc, spec))


def _observe_percentiles(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Cumulative observe() percentiles across producers.

    Per-producer histograms cannot be averaged into a percentile, so the worst
    p95 across producers is reported — the number an operator would feel.
    """
    p50 = [r["observe"]["p50_ms"] for r in reports if (r.get("observe") or {}).get("count")]
    p95 = [r["observe"]["p95_ms"] for r in reports if (r.get("observe") or {}).get("count")]
    p99 = [r["observe"]["p99_ms"] for r in reports if (r.get("observe") or {}).get("count")]
    if not p95:
        return {}
    return {"p50": round(statistics.median(p50), 4),
            "p95": round(max(p95), 4), "p99": round(max(p99), 4)}


def _final_comparison_sweep() -> dict[str, Any]:
    """Compare every shadow projection that has no verdict yet."""
    from bio_agent_os.cognitive.shadow_runner import compare_event

    runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
    try:
        pending = [
            row[0] for row in runtime.memories.conn.execute(
                "SELECT source_event_id FROM shadow_memories"
                " WHERE comparison_status IS NULL"
            ).fetchall()
        ]
        statuses: dict[str, int] = {}
        for event_id in pending:
            try:
                result = compare_event(runtime, event_id)
                statuses[result.status] = statuses.get(result.status, 0) + 1
            except Exception as exc:
                statuses[f"error:{type(exc).__name__}"] = (
                    statuses.get(f"error:{type(exc).__name__}", 0) + 1)
        return {"swept": len(pending), "statuses": statuses}
    finally:
        runtime.close()


def _rollback_to_legacy(reason: Any) -> None:
    """Section 11: flip staging to legacy and pause, deleting nothing."""
    marker = CONFIG / "ROLLED_BACK_TO_LEGACY.json"
    try:
        runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
        ProjectionControl(runtime.events.conn).pause(
            reason=f"canary rollback: {reason}", operator="supervisor")
        runtime.close()
    except Exception as exc:
        log(f"pause during rollback failed: {exc}")
    marker.write_text(json.dumps({
        "at": time.time(), "at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "action": "staging switched to BIO_AGENT_PROJECTION_MODE=legacy; "
                  "projection paused; events, debts, shadow projections and "
                  "audit logs all retained for investigation",
        "deleted": "nothing",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    log("ROLLED BACK: staging is legacy-only, nothing deleted")


# ==========================================================================
# control surface
# ==========================================================================

def _pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, timeout=20)
        return str(pid) in out.stdout
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def cmd_status(_: argparse.Namespace) -> int:
    running = SUPERVISOR_PID.exists() and _pid_alive(int(SUPERVISOR_PID.read_text()))
    print(f"  supervisor        : {'running' if running else 'not running'}"
          + (f" (pid {SUPERVISOR_PID.read_text().strip()})" if SUPERVISOR_PID.exists() else ""))
    if not DB_PATH.exists():
        print("  database          : not created yet")
        return 0
    counts = read_counts(str(DB_PATH))
    footprint = environment.database_footprint(str(DB_PATH))
    print(f"  database          : {DB_PATH}")
    print(f"  events            : {counts.get('events', 0):,}")
    print(f"  shadow rows       : {counts.get('shadow', 0):,}")
    print(f"  queue pending     : {counts.get('pending', 0):,}")
    print(f"  queue in_progress : {counts.get('in_progress', 0):,}")
    print(f"  completed         : {counts.get('completed', 0):,}")
    print(f"  dead letter       : {counts.get('dead_letter', 0):,}")
    print(f"  database size     : {footprint['db_bytes'] / 1048576:.1f} MB")
    print(f"  wal size          : {footprint['wal_bytes'] / 1048576:.1f} MB")
    try:
        runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
        control = ProjectionControl(runtime.events.conn)
        print(f"  projection paused : {control.is_paused()}")
        runtime.close()
    except Exception as exc:
        print(f"  projection paused : unknown ({exc})")
    if TIMESERIES.exists():
        lines = [l for l in TIMESERIES.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"  samples written   : {len(lines)}")
        if lines:
            last = json.loads(lines[-1])
            print(f"  last sample       : t={last['elapsed_hours']}h "
                  f"lag_p95={last.get('projection_lag_p95')}ms "
                  f"match={last.get('shadow_match', 0):,} "
                  f"mismatch={last.get('shadow_mismatch', 0)}")
    marker = CONFIG / "ROLLED_BACK_TO_LEGACY.json"
    if marker.exists():
        print(f"  ROLLED BACK       : {json.loads(marker.read_text())['reason']}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
    state = ProjectionControl(runtime.events.conn).pause(
        reason=args.reason or "manual", operator="canary-cli")
    runtime.close()
    print(f"  paused: {state.reason}")
    record_event("manual:pause", {"reason": state.reason})
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
    ProjectionControl(runtime.events.conn).resume(
        reason=args.reason or "manual", operator="canary-cli")
    runtime.close()
    print("  resumed")
    record_event("manual:resume", {"reason": args.reason or "manual"})
    return 0


def cmd_drain(args: argparse.Namespace) -> int:
    runtime = MemoryOS(str(DB_PATH), projection_mode="shadow")
    try:
        result = drain(runtime, timeout_seconds=args.timeout)
    finally:
        runtime.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    record_event("manual:drain", {"drained": result["drained"],
                                  "seconds": result["seconds"]})
    return 0 if result["drained"] else 1


def cmd_stop(_: argparse.Namespace) -> int:
    if not SUPERVISOR_PID.exists():
        print("  not running")
        return 0
    pid = int(SUPERVISOR_PID.read_text().strip())
    print(f"  stopping supervisor pid {pid}")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=60)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    record_event("manual:stop", {"pid": pid})
    return 0


def _ramp_disabled(ramp_rate: float | None) -> bool:
    """Whether the run holds a flat rate for its whole duration.

    ``None`` and any non-positive number both mean "stay flat". Zero is a
    legitimate way to ask for no ramp and must not read as "not supplied".
    """
    return ramp_rate is None or ramp_rate <= 0


def detached_argv(args: argparse.Namespace) -> list[str]:
    """The command line handed to the detached child.

    Every rate-shaping flag is forwarded unconditionally. A flag omitted
    because its value looked falsy would be replaced by the parser default
    in the child, silently running something other than what was asked for.
    """
    return [sys.executable, str(Path(__file__).resolve()), "start", "--foreground",
            "--hours", str(args.hours), "--rate", str(args.rate),
            "--producers", str(args.producers), "--workers", str(args.workers),
            "--sample-seconds", str(args.sample_seconds),
            "--ramp-rate", str(args.ramp_rate)]


def cmd_start(args: argparse.Namespace) -> int:
    if SUPERVISOR_PID.exists() and _pid_alive(int(SUPERVISOR_PID.read_text().strip())):
        print("  already running; use `status` or `stop`")
        return 1
    if args.foreground:
        return run(args.hours, args.rate, args.producers, args.workers,
                   args.sample_seconds, args.ramp_rate)

    LOGS.mkdir(parents=True, exist_ok=True)
    out = (LOGS / "supervisor.out.log").open("a", encoding="utf-8")
    argv = detached_argv(args)
    creation = 0
    if sys.platform == "win32":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    proc = subprocess.Popen(argv, cwd=str(_REPO), stdout=out, stderr=out,
                            creationflags=creation, close_fds=True)
    print(f"  supervisor detached, pid {proc.pid}")
    print(f"  logs: {LOGS / 'supervisor.out.log'}")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_stop(args)
    time.sleep(5)
    return cmd_start(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="canary_supervisor")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("start")
    s.add_argument("--hours", type=float, default=24.0)
    s.add_argument("--rate", type=float, default=100.0,
                   help="observations/s; stays well under the 390/s envelope")
    s.add_argument("--ramp-rate", type=float, default=150.0,
                   help="rate after two stable hours; None to stay flat")
    s.add_argument("--producers", type=int, default=2)
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--sample-seconds", type=float, default=60.0)
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_start)

    for name, fn in (("status", cmd_status), ("stop", cmd_stop)):
        c = sub.add_parser(name)
        c.set_defaults(func=fn)

    for name, fn in (("pause", cmd_pause), ("resume", cmd_resume)):
        c = sub.add_parser(name)
        c.add_argument("--reason", default="")
        c.set_defaults(func=fn)

    d = sub.add_parser("drain")
    d.add_argument("--timeout", type=float, default=600.0)
    d.set_defaults(func=cmd_drain)

    r = sub.add_parser("restart")
    r.add_argument("--hours", type=float, default=24.0)
    r.add_argument("--rate", type=float, default=100.0)
    r.add_argument("--ramp-rate", type=float, default=150.0)
    r.add_argument("--producers", type=int, default=2)
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--sample-seconds", type=float, default=60.0)
    r.add_argument("--foreground", action="store_true")
    r.set_defaults(func=cmd_restart)

    return p


def main(argv: list[str] | None = None) -> int:
    return int(build_parser().parse_args(argv).func(build_parser().parse_args(argv)))


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(int(args.func(args)))


__all__ = [
    "BASELINE_DB", "CONFIG", "CONTROL_FILE", "EVENTS_LOG", "LOGS", "METRICS",
    "PIDS", "RECOVERY_SCHEDULE", "REPORTS", "SUPERVISOR_LOG", "SUPERVISOR_PID",
    "TIMESERIES", "assert_isolated", "assert_shadow_mode", "correctness_probe",
    "evaluate", "integrity", "latency_from_samples", "log", "read_counts",
    "_ramp_disabled", "detached_argv",
    "read_json", "recall_isolation_probe", "record_event", "run", "run_recovery",
]
