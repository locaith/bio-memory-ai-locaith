"""Processes, not threads. Instrumentation by subclass, not by hook.

Two decisions worth stating.

**Real processes.** Threads in one interpreter share a GIL and, worse, would
let several "workers" share connection state that separate processes never
could. The lock contention this benchmark exists to find only appears when
independent processes hold independent connections to the same file. Windows
uses `spawn`, which re-imports the module in every child and so also catches
import-time assumptions that `fork` would hide.

**Measurement by subclass.** `InstrumentedWorker` extends the production
worker instead of adding timing hooks to it. The thing being measured stays
exactly as it ships; nothing in `bio_agent_os/` knows this file exists.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402
from bio_agent_os.cognitive.outbox import JobStatus                    # noqa: E402
from bio_agent_os.cognitive.reconciliation_worker import (             # noqa: E402
    ProjectionBuilder,
    ReconciliationWorker,
    build_default_builders,
)
from bio_agent_os.cognitive.shadow import COGNITIVE_MEMORY, ShadowMemoryBuilder  # noqa: E402

from benchmarks.reliability import corpus, environment                 # noqa: E402
from benchmarks.reliability.metrics import Histogram, JobSample, write_samples  # noqa: E402


def is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


# -- instrumentation ---------------------------------------------------------

class _TimedBuilder:
    """Wraps a builder to record when the projection itself started and ended.

    Everything else `process()` does — ledger lookup, dependency check, event
    load, outbox completion — is measured around this, so build time is build
    time and not "everything the worker did".
    """

    def __init__(self, inner: ProjectionBuilder, sink: dict[str, tuple[float, float]]) -> None:
        self.inner = inner
        self.sink = sink

    def build(self, event: Any, job: Any, conn: sqlite3.Connection) -> Any:
        started = time.time()
        try:
            return self.inner.build(event, job, conn)
        finally:
            self.sink[job.job_id] = (started, time.time())


class InstrumentedWorker(ReconciliationWorker):
    """A production worker that also says how long each stage took."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._build_times: dict[str, tuple[float, float]] = {}
        self.builders = {k: _TimedBuilder(v, self._build_times) for k, v in self.builders.items()}
        self.samples: list[JobSample] = []
        self.lock_errors = 0

    def process(self, job: Any) -> str:
        entered = time.time()
        try:
            status = super().process(job)
        except Exception as exc:
            if is_lock_error(exc):
                self.lock_errors += 1
            raise
        done = time.time()
        started, finished = self._build_times.pop(job.job_id, (entered, done))
        self.samples.append(
            JobSample(
                job_id=job.job_id,
                event_id=job.event_id,
                tenant_id=job.tenant_id,
                # Set when the outbox row was constructed, microseconds before
                # the event commit. Starting the clock there slightly
                # *overstates* visibility latency, which is the safe direction.
                outbox_created_at=job.created_at,
                claimed_at=float(job.locked_at) if job.locked_at else entered,
                build_started_at=started,
                build_finished_at=finished,
                # After `outbox.complete()` committed: the moment the pipeline
                # considers this projection visible and settled.
                completed_at=done,
                status=status,
                attempts=job.attempts,
                worker_id=self.worker_id,
            )
        )
        return status


def make_worker(
    runtime: MemoryOS,
    *,
    worker_id: str,
    shadow: bool = False,
    lease_seconds: float = 300.0,
    max_attempts: int = 5,
    builders: dict[str, ProjectionBuilder] | None = None,
) -> InstrumentedWorker:
    if builders is None:
        builders = (
            {COGNITIVE_MEMORY: ShadowMemoryBuilder(runtime.shadow_memories)}
            if shadow
            else build_default_builders(runtime.memories)
        )
    return InstrumentedWorker(
        runtime.events.conn,
        projection_conn=runtime.memories.conn,
        outbox=runtime.events.outbox,
        builders=builders,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )


# -- queue observation -------------------------------------------------------

def queue_counts(db_path: str) -> dict[str, int]:
    """Status histogram read on a fresh connection, so nothing is cached."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM projection_outbox GROUP BY status"
        ).fetchall()
        out = {s.value: 0 for s in JobStatus}
        for row in rows:
            out[row["status"]] = int(row["n"])
        out["events"] = int(
            conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0]
        )
        try:
            out["ledger"] = int(
                conn.execute("SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
            )
        except sqlite3.OperationalError:
            out["ledger"] = 0
        try:
            out["memories"] = int(
                conn.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
            )
        except sqlite3.OperationalError:
            out["memories"] = 0
        try:
            out["shadow"] = int(
                conn.execute("SELECT COUNT(*) FROM shadow_memories").fetchone()[0]
            )
        except sqlite3.OperationalError:
            out["shadow"] = 0
        return out
    finally:
        conn.close()


# -- child process entry points ---------------------------------------------
#
# Module level and picklable-argument only: `spawn` re-imports this module in
# every child, and a closure would not survive the trip.

def producer_main(spec: dict[str, Any]) -> None:
    """Append `count` events, timing each one."""
    started_event = spec.get("start_event")
    out_path = Path(spec["out"])
    latency = Histogram(f"append.{spec['index']}")
    lock_errors = 0
    failures = 0
    event_ids: list[str] = []

    runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
    observations = list(corpus.generate(spec["count"], seed=spec["seed"], start=spec["start"]))

    if started_event is not None:
        started_event.wait(timeout=120)

    wall_start = time.time()
    for obs in observations:
        t0 = time.perf_counter()
        try:
            event = runtime.observe(
                tenant_id=obs.tenant_id,
                actor=obs.actor,
                source=obs.source,
                content=obs.content,
                workspace_id=obs.workspace_id,
            )
            event_ids.append(event.event_id)
        except Exception as exc:  # a producer must report, not vanish
            failures += 1
            if is_lock_error(exc):
                lock_errors += 1
            continue
        finally:
            latency.add((time.perf_counter() - t0) * 1000)
    wall_end = time.time()

    peak_rss = environment.peak_rss_bytes()
    runtime.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "role": "producer",
                "index": spec["index"],
                "pid": os.getpid(),
                "appended": len(event_ids),
                "failures": failures,
                "lock_errors": lock_errors,
                "wall_start": wall_start,
                "wall_end": wall_end,
                "elapsed_s": wall_end - wall_start,
                "throughput_per_s": len(event_ids) / max(1e-9, wall_end - wall_start),
                "latency": latency.as_dict(),
                "peak_rss_bytes": peak_rss,
                "event_ids": event_ids if spec.get("keep_event_ids") else [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def worker_main(spec: dict[str, Any]) -> None:
    """Drain the queue until it stays empty, a deadline passes, or asked to stop."""
    start_event = spec.get("start_event")
    stop_event = spec.get("stop_event")
    out_path = Path(spec["out"])

    runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
    worker = make_worker(
        runtime,
        worker_id=spec["worker_id"],
        shadow=bool(spec.get("shadow")),
        lease_seconds=float(spec.get("lease_seconds", 300.0)),
        max_attempts=int(spec.get("max_attempts", 5)),
    )

    if start_event is not None:
        start_event.wait(timeout=120)

    idle_limit = int(spec.get("idle_polls", 5))
    poll_seconds = float(spec.get("poll_seconds", 0.05))
    deadline = time.time() + float(spec.get("max_seconds", 900))
    batch = int(spec.get("batch_size", 50))
    # Long runs need samples on disk while they are still running. Flushing in
    # batches every few seconds keeps that off the per-job path.
    flush_seconds = float(spec.get("flush_samples_every", 0) or 0)
    samples_path = out_path.with_suffix(".samples.jsonl")
    flushed = 0
    next_flush = time.time() + flush_seconds if flush_seconds else None
    lock_errors = 0
    idle = 0
    wall_start = time.time()

    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        before = worker.metrics.claimed
        try:
            worker.run_once(batch_size=batch)
        except Exception as exc:
            if is_lock_error(exc):
                lock_errors += 1
                time.sleep(0.01)
                continue
            traceback.print_exc()
            break
        if next_flush is not None and time.time() >= next_flush:
            if len(worker.samples) > flushed:
                write_samples(worker.samples[flushed:], samples_path, append=True)
                flushed = len(worker.samples)
            next_flush = time.time() + flush_seconds
        if worker.metrics.claimed == before:
            idle += 1
            if idle >= idle_limit and not spec.get("stay_alive"):
                break
            time.sleep(poll_seconds)
        else:
            idle = 0
    wall_end = time.time()

    peak_rss = environment.peak_rss_bytes()
    if len(worker.samples) > flushed:
        write_samples(worker.samples[flushed:], samples_path, append=flushed > 0)
    runtime.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "role": "worker",
                "worker_id": spec["worker_id"],
                "pid": os.getpid(),
                "wall_start": wall_start,
                "wall_end": wall_end,
                "elapsed_s": wall_end - wall_start,
                "metrics": worker.metrics.as_dict(),
                "throughput_per_s": worker.metrics.completed / max(1e-9, wall_end - wall_start),
                "lock_errors": lock_errors + worker.lock_errors,
                "samples": len(worker.samples),
                "peak_rss_bytes": peak_rss,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def spawn(target: Any, specs: list[dict[str, Any]]) -> list[mp.Process]:
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=target, args=(spec,), daemon=False) for spec in specs]
    for proc in procs:
        proc.start()
    return procs


def join_all(procs: list[mp.Process], timeout: float = 1800) -> list[int]:
    deadline = time.time() + timeout
    for proc in procs:
        proc.join(timeout=max(1.0, deadline - time.time()))
    codes = []
    for proc in procs:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=10)
        codes.append(proc.exitcode if proc.exitcode is not None else -999)
    return codes


def read_results(paths: list[Path]) -> list[dict[str, Any]]:
    out = []
    for path in paths:
        if path.exists():
            out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


__all__ = [
    "InstrumentedWorker",
    "is_lock_error",
    "join_all",
    "make_worker",
    "producer_main",
    "queue_counts",
    "read_results",
    "spawn",
    "worker_main",
]
