"""24-hour staging shadow canary for the cognitive_memory projection.

Runs legacy and shadow from one canonical input for a full day, on an isolated
SQLite database, and writes down what happened once a minute.

Three things this deliberately is not.

**Not a speed test.** The input rate is held well below the measured
projection floor. A canary that saturates the queue measures the backlog, not
the pipeline, and the pipeline is what has to survive 24 hours.

**Not a production process.** It refuses to start against a database outside
its own staging directory, and it sets `BIO_AGENT_PROJECTION_MODE` on itself
rather than inheriting it — a shadow flag exported at machine scope would turn
shadow mode on for everything else on the box.

**Not self-healing.** If an SLO breaks it stops and says so. A canary that
works around its own failures has stopped being evidence.

Recovery events are scheduled, not improvised: a worker restart, a killed
lease-holder, a held write lock, a controlled pause, a full runtime restart, a
duplicate enqueue, and a WAL checkpoint. Each writes its own before/after
record.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import signal
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

STAGING = _REPO / ".staging" / "v082-canary"
DB_PATH = STAGING / "canary.db"

#: Set before importing the runtime, and only in this process tree. The
#: instruction is explicit that a machine-scoped shadow flag is a hazard.
os.environ["BIO_AGENT_PROJECTION_MODE"] = "shadow"

from benchmarks.reliability import corpus, environment, harness            # noqa: E402
from benchmarks.reliability.metrics import Histogram, read_samples         # noqa: E402
from bio_agent_os.cognitive.diagnostics import DeepDoctor                  # noqa: E402
from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor    # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                         # noqa: E402
from bio_agent_os.cognitive.models import AccessContext, MemoryType        # noqa: E402
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob         # noqa: E402
from bio_agent_os.cognitive.projection_control import ProjectionControl, drain  # noqa: E402
from bio_agent_os.cognitive.shadow import (                                # noqa: E402
    COGNITIVE_MEMORY,
    ComparisonStatus,
    ProjectionMode,
    current_mode,
)
from bio_agent_os.cognitive.shadow_runner import compare_event, shadow_worker  # noqa: E402
from bio_agent_os.cognitive.wal import CheckpointMode, manager_for as wal_manager_for  # noqa: E402


# ==========================================================================
# SLOs, declared here and evaluated against every sample. Written before the
# run, and not edited afterwards to make a run pass.
# ==========================================================================

SLO = {
    "correctness": {
        "lost_committed_events": 0,
        "lost_outbox_debts": 0,
        "duplicate_projections": 0,
        "duplicate_ledgers": 0,
        "tenant_leakage": 0,
        "workspace_leakage": 0,
        "shadow_visible_in_production_recall": 0,
        "unexplained_shadow_mismatch": 0,
        "sqlite_integrity": "ok",
    },
    "latency": {
        "legacy_observe_p95_ms": 1.0,
        "shadow_observe_p95_ms": 1.0,
        "shadow_overhead_absolute_p95_ms": 0.5,
        "projection_visibility_p95_ms": 100.0,
        "projection_visibility_p99_ms": 500.0,
    },
    "queue": {"warn_depth": 700, "critical_depth": 1100},
    "wal": {"warn_bytes": 256 * 1048576, "critical_bytes": 512 * 1048576},
}

#: How long a shadow projection is given to find its legacy counterpart before
#: a comparison is treated as final. The producer writes the event, then the
#: memory; the worker can land in between.
COMPARE_SETTLE_SECONDS = 5.0

#: After this long, a still-missing legacy memory stops being a race and
#: starts being a finding. It is then recorded as MISSING_LEGACY for real.
COMPARE_GIVE_UP_SECONDS = 120.0

#: Any of these stops the canary immediately. Section 11 of the runbook.
ROLLBACK_TRIGGERS = (
    "lost_committed_events", "lost_outbox_debts", "duplicate_projections",
    "duplicate_ledgers", "tenant_leakage", "workspace_leakage",
    "shadow_visible_in_production_recall", "sqlite_integrity_not_ok",
    "doctor_critical", "queue_not_draining", "wal_above_hard_limit",
    "legacy_observe_failed",
)


# ==========================================================================
# producers
# ==========================================================================

def producer_main(spec: dict[str, Any]) -> None:
    """Legacy observe() + remember(), at a paced rate, forever until stopped.

    Both paths run from the same canonical input: `observe()` writes the event
    and (in shadow mode) the outbox debt; `remember()` produces the production
    memory that legacy recall serves. The comparator later checks that the
    shadow projection agrees with that memory.
    """
    os.environ["BIO_AGENT_PROJECTION_MODE"] = "shadow"
    stop = spec["stop_event"]
    out = Path(spec["out"])
    runtime = MemoryOS(spec["db"], projection_mode="shadow")

    observe_ms = Histogram("observe")
    remember_ms = Histogram("remember")
    appended = failures = lock_errors = legacy_failures = 0
    cycle = 0
    offset = int(spec["index"]) * 50_000_000
    interval = 1.0 / max(1e-9, float(spec["target_rate"]))
    started = time.time()
    # Written periodically, not only at exit. A producer that reports its
    # latency only when it stops gives a 24-hour run no observe() percentiles
    # at all — and the shadow-overhead SLO is computed from exactly those.
    next_report = started + 10.0

    def _report(final: bool = False) -> None:
        payload = {
            "role": "producer", "index": spec["index"], "appended": appended,
            "failures": failures, "legacy_failures": legacy_failures,
            "lock_errors": lock_errors,
            "elapsed_s": round(time.time() - started, 1),
            "observe": observe_ms.as_dict(), "remember": remember_ms.as_dict(),
            "final": final,
        }
        if final:
            payload["peak_rss_bytes"] = environment.peak_rss_bytes()
        Path(spec["out"]).write_text(json.dumps(payload, ensure_ascii=False),
                                     encoding="utf-8")

    try:
        while not stop.is_set():
            batch = list(corpus.generate(400, seed=spec["seed"] + cycle,
                                         start=offset + cycle * 400))
            cycle += 1
            for obs in batch:
                if stop.is_set():
                    break
                tick = time.perf_counter()
                try:
                    t0 = time.perf_counter()
                    event = runtime.observe(
                        tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                        content=obs.content, workspace_id=obs.workspace_id,
                    )
                    observe_ms.add((time.perf_counter() - t0) * 1000)

                    t1 = time.perf_counter()
                    runtime.remember(event=event, memory_type=MemoryType.EPISODIC,
                                     content=obs.content)
                    remember_ms.add((time.perf_counter() - t1) * 1000)
                    appended += 1
                except Exception as exc:
                    failures += 1
                    legacy_failures += 1
                    if harness.is_lock_error(exc):
                        lock_errors += 1
                    _log(spec["log"], f"producer {spec['index']} observe failed: "
                                      f"{type(exc).__name__}: {exc}")
                if time.time() >= next_report:
                    _report()
                    next_report = time.time() + 10.0
                elapsed = time.perf_counter() - tick
                if elapsed < interval:
                    time.sleep(interval - elapsed)
    finally:
        runtime.close()
        _report(final=True)


#: Tenant prefix for the legacy baseline. Its events carry no outbox debt by
#: design, so the correctness probe has to be able to exclude them from the
#: "every event owes a projection" invariant.
BASELINE_TENANT_PREFIX = "baseline_"


def legacy_baseline_main(spec: dict[str, Any]) -> None:
    """The same corpus with the shadow queue off, on the same database.

    Absolute shadow overhead is shadow observe() minus legacy observe(), and
    that subtraction is only meaningful if both sides face the same
    conditions. A first version ran this on a *separate* database at a tenth
    of the rate, which made the difference a measure of SQLite write
    contention rather than of shadow mode: it reported ~11 ms of "overhead"
    against a benchmark figure of 0.30 ms.

    So it runs here: same file, same competing workers, same rate. The only
    difference between the two producers is whether observe() enqueues an
    outbox row, which is exactly the thing being priced.
    """
    os.environ["BIO_AGENT_PROJECTION_MODE"] = "legacy"
    stop = spec["stop_event"]
    runtime = MemoryOS(spec["db"], projection_mode="legacy")
    observe_ms = Histogram("legacy_observe")
    appended = 0
    cycle = 0
    interval = 1.0 / max(1e-9, float(spec["target_rate"]))
    # Reported periodically for the same reason the shadow producers are: the
    # absolute shadow overhead is shadow_p95 minus this, and a baseline that
    # only appears at exit makes that number uncomputable for the whole run.
    next_report = time.time() + 10.0

    def _report() -> None:
        Path(spec["out"]).write_text(json.dumps({
            "role": "legacy_baseline", "appended": appended,
            "observe": observe_ms.as_dict(),
        }, ensure_ascii=False), encoding="utf-8")

    try:
        while not stop.is_set():
            batch = list(corpus.generate(400, seed=spec["seed"] + cycle,
                                         start=90_000_000 + cycle * 400))
            cycle += 1
            for obs in batch:
                if stop.is_set():
                    break
                tick = time.perf_counter()
                try:
                    t0 = time.perf_counter()
                    event = runtime.observe(
                        tenant_id=BASELINE_TENANT_PREFIX + obs.tenant_id,
                        actor=obs.actor, source=obs.source,
                        content=obs.content, workspace_id=obs.workspace_id,
                    )
                    observe_ms.add((time.perf_counter() - t0) * 1000)
                    runtime.remember(event=event, memory_type=MemoryType.EPISODIC,
                                     content=obs.content)
                    appended += 1
                except Exception:
                    pass
                if time.time() >= next_report:
                    _report()
                    next_report = time.time() + 10.0
                elapsed = time.perf_counter() - tick
                if elapsed < interval:
                    time.sleep(interval - elapsed)
    finally:
        runtime.close()
        _report()


def shadow_worker_main(spec: dict[str, Any]) -> None:
    """Drains the shadow queue and compares each projection against legacy."""
    os.environ["BIO_AGENT_PROJECTION_MODE"] = "shadow"
    stop = spec["stop_event"]
    runtime = MemoryOS(spec["db"], projection_mode="shadow")
    # `shadow_runner.shadow_worker()` returns a plain ReconciliationWorker,
    # which records no per-stage timings. The canary needs projection-lag
    # percentiles, so it uses the instrumented subclass with the same shadow
    # builder — measurement by subclass, the production worker untouched.
    worker = harness.make_worker(
        runtime, worker_id=spec["worker_id"], shadow=True, lease_seconds=60.0,
    )
    worker.wal_manager = wal_manager_for(runtime) if spec.get("manage_wal") else None
    worker.control = ProjectionControl(runtime.events.conn)

    samples_path = Path(spec["out"]).with_suffix(".samples.jsonl")
    compared: set[str] = set()
    statuses: dict[str, int] = {}
    deferred: list[tuple[str, float]] = []
    flushed = 0
    next_compare = 0      # index into worker.samples, not len(compared)
    next_flush = time.time() + 5.0
    started = time.time()

    try:
        while not stop.is_set():
            before = worker.metrics.claimed
            try:
                worker.run_once(batch_size=50)
            except Exception as exc:
                _log(spec["log"], f"worker {spec['worker_id']}: "
                                  f"{type(exc).__name__}: {exc}")
                time.sleep(0.2)
                continue

            # Compare only after a projection has landed — and be careful
            # about *which* projection.
            #
            # The producer calls observe() then remember(). The worker can
            # claim and build the shadow projection in between, so a
            # comparison run immediately after the shadow write can find no
            # legacy memory yet and report MISSING_LEGACY. That is the
            # harness racing itself, not a disagreement between the two
            # paths: a smoke run produced 145 of them and every one became
            # MATCH when re-compared after things settled.
            #
            # So MISSING_LEGACY is never final here. It goes back in the
            # queue and is retried once the row has had time to appear.
            for sample in worker.samples[next_compare:]:
                next_compare += 1
                if sample.event_id in compared:
                    continue
                if sample.status != JobStatus.COMPLETED.value:
                    continue
                deferred.append((sample.event_id, time.time()))

            still_waiting: list[tuple[str, float]] = []
            now = time.time()
            for event_id, first_seen in deferred:
                if now - first_seen < COMPARE_SETTLE_SECONDS:
                    still_waiting.append((event_id, first_seen))
                    continue
                try:
                    result = compare_event(runtime, event_id)
                except Exception as exc:
                    _log(spec["log"], f"compare failed for {event_id}: {exc}")
                    continue
                if (result.status == ComparisonStatus.MISSING_LEGACY.value
                        and now - first_seen < COMPARE_GIVE_UP_SECONDS):
                    still_waiting.append((event_id, first_seen))
                    continue
                statuses[result.status] = statuses.get(result.status, 0) + 1
                compared.add(event_id)
            deferred = still_waiting

            if time.time() >= next_flush:
                if len(worker.samples) > flushed:
                    from benchmarks.reliability.metrics import write_samples
                    write_samples(worker.samples[flushed:], samples_path, append=flushed > 0)
                    flushed = len(worker.samples)
                Path(spec["status_out"]).write_text(json.dumps({
                    "worker_id": spec["worker_id"],
                    "metrics": worker.metrics.as_dict(),
                    "comparisons": statuses,
                    "compared": len(compared),
                }, ensure_ascii=False), encoding="utf-8")
                next_flush = time.time() + 5.0

            if worker.metrics.claimed == before:
                time.sleep(0.05)
    finally:
        if len(worker.samples) > flushed:
            from benchmarks.reliability.metrics import write_samples
            write_samples(worker.samples[flushed:], samples_path, append=flushed > 0)
        peak = environment.peak_rss_bytes()
        runtime.close()
        Path(spec["out"]).write_text(json.dumps({
            "role": "shadow_worker", "worker_id": spec["worker_id"],
            "metrics": worker.metrics.as_dict(), "comparisons": statuses,
            "compared": len(compared), "elapsed_s": round(time.time() - started, 1),
            "peak_rss_bytes": peak,
        }, ensure_ascii=False), encoding="utf-8")


def _log(path: str | Path, message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


__all__ = [
    "DB_PATH",
    "ROLLBACK_TRIGGERS",
    "SLO",
    "STAGING",
    "legacy_baseline_main",
    "producer_main",
    "shadow_worker_main",
]
