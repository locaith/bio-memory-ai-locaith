"""Reconciliation worker — the thing that actually drains the queue.

The outbox records what is owed and the replay engine finds it. Neither
applies anything. This module does, and it is separate from both for the same
reason they are separate from each other: an append path must not wait on
projection work, and a diagnosis tool must be safe to run without side effects.

The hard case this is built around
----------------------------------
A worker can die after the projection is committed but before the outbox row
is marked completed. On restart the job looks unfinished, so it is retried —
and a naive retry writes the projection twice.

The fix is a **projection ledger** written *in the same transaction as the
projection itself*. `SQLiteMemoryStore.put()` commits on its own connection,
so the ledger row is inserted on that same connection first, without a commit;
`put()`'s commit then makes both durable together. A retry sees the ledger row,
concludes the work is already done, and only marks the outbox completed.

That gives exactly-once *effect* on top of at-least-once delivery, which is the
only honest guarantee a queue can offer.
"""

from __future__ import annotations

import json
import logging
import signal
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from . import fault_points as _fault
from .models import BeliefState, CognitiveMemory, EventRecord, MemoryType
from .outbox import JobStatus, ProjectionJob, ProjectionOutbox
from .projection_registry import (
    DependencyState,
    ProjectionType,
    dependencies_of,
    spec,
)

logger = logging.getLogger("bio_agent_os.reconciliation")

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_ledger (
    projection_key TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    projection_type TEXT NOT NULL,
    projection_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    target_id TEXT,
    worker_id TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_event ON projection_ledger(event_id);
CREATE INDEX IF NOT EXISTS idx_ledger_tenant ON projection_ledger(tenant_id, projection_type);
"""


class BuildOutcome(str, Enum):
    """Result of one build attempt."""

    BUILT = "built"
    ALREADY_BUILT = "already_built"   # ledger says a previous attempt finished
    SKIPPED = "skipped"               # the builder declined, legitimately
    FAILED = "failed"


@dataclass(slots=True)
class BuildResult:
    outcome: str
    target_id: str | None = None
    reason: str = ""


class ProjectionBuilder(Protocol):
    """Turns an immutable event into a projection.

    A builder must be deterministic: the same event and version must produce
    the same projection, or replay cannot reproduce what it is meant to
    reproduce.

    **A builder must not commit.** It writes into the transaction the worker
    already has open on `conn` — which already holds the ledger row — and the
    worker commits both together. A builder that commits on its own splits one
    transaction into two, and the window between them is a state where the
    projection exists but the ledger does not yet describe it.
    """

    def build(self, event: EventRecord, job: ProjectionJob, conn: sqlite3.Connection) -> BuildResult:
        ...


class CognitiveMemoryBuilder:
    """Builds the retrievable memory a `cognitive_memory` job owes.

    Writes into the worker's open transaction and does not commit: the ledger
    row is already pending on the same connection, and the worker commits the
    pair.
    """

    projection_type = ProjectionType.COGNITIVE_MEMORY.value

    def __init__(self, memory_store: Any) -> None:
        self.memories = memory_store

    def build(self, event: EventRecord, job: ProjectionJob, conn: sqlite3.Connection) -> BuildResult:
        content = (event.payload or {}).get("content", "")
        if not str(content).strip():
            # Nothing to remember. A recorded decision, not a silent gap.
            return BuildResult(BuildOutcome.SKIPPED.value, reason="event carries no content")

        memory = CognitiveMemory(
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            memory_type=MemoryType.EPISODIC,
            content=str(content),
            source_event_ids=[event.event_id],
            trust_tier=event.trust_tier,
            security_label=event.security_label,
            valid_from=event.valid_from,
            valid_to=event.valid_to,
            lifecycle_state=BeliefState.PROPOSED,
            metadata={
                **(event.metadata or {}),
                "projection_key": job.key,
                "projection_type": job.projection_type,
                "projection_version": job.projection_version,
                "source_event_id": event.event_id,
            },
        )
        stored = self.memories.put(memory, commit=False)
        return BuildResult(BuildOutcome.BUILT.value, target_id=getattr(stored, "memory_id", None))


@dataclass(slots=True)
class WorkerMetrics:
    claimed: int = 0
    completed: int = 0
    already_built: int = 0
    skipped: int = 0
    failed: int = 0
    dead_lettered: int = 0
    dependency_held: int = 0
    blocked: int = 0
    build_seconds: float = 0.0
    cycles: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "claimed": self.claimed,
            "completed": self.completed,
            "already_built": self.already_built,
            "skipped": self.skipped,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
            "dependency_held": self.dependency_held,
            "blocked": self.blocked,
            "cycles": self.cycles,
            "build_seconds": round(self.build_seconds, 4),
            "mean_build_ms": (
                round(self.build_seconds * 1000 / self.completed, 3) if self.completed else 0.0
            ),
        }


class ReconciliationWorker:
    """Claims owed projections and applies them, safely and repeatedly."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        projection_conn: sqlite3.Connection | None = None,
        outbox: ProjectionOutbox | None = None,
        builders: dict[str, ProjectionBuilder] | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 300.0,
        max_attempts: int = 5,
        tenant_id: str | None = None,
        wal_manager: Any | None = None,
    ) -> None:
        #: Queue connection — reads events, claims and completes outbox rows.
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        #: Connection the projection is written on. The ledger row MUST live
        #: here rather than on `conn`: atomicity in SQLite is per connection,
        #: and writing the ledger on the queue connection while the builder
        #: writes the projection on another means two write transactions on one
        #: file. The second then blocks on the first's lock until busy_timeout
        #: expires — 30 s per job, and no atomicity gained.
        self.projection_conn = projection_conn if projection_conn is not None else conn
        self.projection_conn.row_factory = sqlite3.Row
        self.outbox = outbox or ProjectionOutbox(conn)
        self.builders = builders or {}
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.tenant_id = tenant_id
        #: Optional. Checkpointing happens between cycles in `run_forever`,
        #: never inside a batch and never on the append path — see `wal.py`.
        self.wal_manager = wal_manager
        self.metrics = WorkerMetrics()
        self._stop = threading.Event()
        self._migrate()

    def _migrate(self) -> None:
        self.projection_conn.executescript(LEDGER_SCHEMA)
        self.projection_conn.commit()

    # -- shutdown ----------------------------------------------------------

    def request_stop(self, *_: Any) -> None:
        """Ask the loop to finish the job in hand and then stop.

        Never interrupts a job mid-build: an abrupt stop is exactly the crash
        this design already tolerates, but a clean stop should not need
        recovery afterwards.
        """
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is not None:
                try:
                    signal.signal(sig, self.request_stop)
                except (ValueError, OSError):
                    pass  # not on the main thread; caller handles shutdown

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- ledger ------------------------------------------------------------

    def ledger_entry(self, projection_key: str) -> sqlite3.Row | None:
        return self.projection_conn.execute(
            "SELECT * FROM projection_ledger WHERE projection_key=?", (projection_key,)
        ).fetchone()

    def _record_ledger(self, job: ProjectionJob, target_id: str | None) -> None:
        """Insert the ledger row WITHOUT committing.

        The builder's `put()` commits on this same connection immediately
        after, so the projection and the record that it exists become durable
        in one transaction. Committing here instead would recreate the very
        window this class exists to close.
        """
        self.projection_conn.execute(
            "INSERT OR IGNORE INTO projection_ledger("
            " projection_key, event_id, projection_type, projection_version,"
            " tenant_id, target_id, worker_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                job.key, job.event_id, job.projection_type, job.projection_version,
                job.tenant_id, target_id, self.worker_id, time.time(),
            ),
        )

    # -- dependencies ------------------------------------------------------

    def dependency_state(self, job: ProjectionJob) -> str:
        parents = dependencies_of(job.projection_type)
        if not parents:
            return DependencyState.READY.value
        for parent in parents:
            row = self.conn.execute(
                "SELECT status FROM projection_outbox WHERE event_id=? AND projection_type=? "
                "ORDER BY created_at DESC LIMIT 1",
                (job.event_id, parent.value),
            ).fetchone()
            if row is None:
                return DependencyState.PENDING_DEPENDENCY.value
            if row["status"] == JobStatus.DEAD_LETTER.value:
                return DependencyState.BLOCKED.value
            if row["status"] not in (JobStatus.COMPLETED.value, JobStatus.SKIPPED.value):
                return DependencyState.PENDING_DEPENDENCY.value
        return DependencyState.READY.value

    # -- event loading -----------------------------------------------------

    def _load_event(self, job: ProjectionJob) -> EventRecord | None:
        from .event_store import SQLiteEventStore

        row = self.conn.execute(
            "SELECT * FROM cognitive_events WHERE event_id=? AND tenant_id=?",
            (job.event_id, job.tenant_id),
        ).fetchone()
        return SQLiteEventStore._row(row) if row else None

    # -- one job -----------------------------------------------------------

    def process(self, job: ProjectionJob) -> str:
        """Apply one claimed job. Returns the resulting outbox status."""
        # 1. Already built? The crash window between projection commit and
        #    outbox completion lands here, and this is what makes the retry
        #    harmless.
        if self.ledger_entry(job.key) is not None:
            self.outbox.complete(job.job_id)
            self.metrics.already_built += 1
            self.metrics.completed += 1
            logger.info(
                "projection already built; completing job",
                extra={"job_id": job.job_id, "projection_key": job.key},
            )
            return JobStatus.COMPLETED.value

        # 2. Dependencies.
        state = self.dependency_state(job)
        if state == DependencyState.BLOCKED.value:
            self.outbox.fail(job.job_id, "parent projection is dead-lettered",
                             max_attempts=0)
            self.metrics.blocked += 1
            return JobStatus.DEAD_LETTER.value
        if state == DependencyState.PENDING_DEPENDENCY.value:
            # Not a failure. Release the lease and let it come round again.
            self.outbox.fail(job.job_id, "waiting on parent projection",
                             max_attempts=self.max_attempts * 100)
            self.metrics.dependency_held += 1
            return DependencyState.PENDING_DEPENDENCY.value

        # 3. Replayability.
        if not spec(job.projection_type).replayable and job.attempts > 1:
            self.outbox.fail(
                job.job_id,
                "projection pins a specific version and cannot be rebuilt automatically",
                max_attempts=0,
            )
            self.metrics.dead_lettered += 1
            return JobStatus.DEAD_LETTER.value

        # 4. Event.
        event = self._load_event(job)
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_EVENT_LOAD)
        if event is None:
            self.outbox.fail(job.job_id, "event not found for this job", max_attempts=0)
            self.metrics.dead_lettered += 1
            return JobStatus.DEAD_LETTER.value

        builder = self.builders.get(job.projection_type)
        if builder is None:
            self.outbox.fail(
                job.job_id, f"no builder registered for {job.projection_type}", max_attempts=0
            )
            self.metrics.dead_lettered += 1
            return JobStatus.DEAD_LETTER.value

        # 5. Build. One transaction on the target connection carries the
        #    ledger row, the projection and the ledger's target_id, and the
        #    worker is what commits it. The builder used to commit for itself,
        #    which split this into two transactions: the second existed only
        #    to write target_id, and a crash between them left a ledger row
        #    that could never say what it had produced.
        started = time.perf_counter()
        try:
            _fault.fire(_fault.ProjectionFaultPoint.BEFORE_LEDGER_INSERT)
            self._record_ledger(job, target_id=None)
            _fault.fire(_fault.ProjectionFaultPoint.AFTER_LEDGER_INSERT)
            _fault.fire(_fault.ProjectionFaultPoint.BEFORE_PROJECTION_WRITE)
            result = builder.build(event, job, self.projection_conn)
            _fault.fire(
                _fault.ProjectionFaultPoint.AFTER_PROJECTION_WRITE_BEFORE_COMMIT
            )
        except Exception as exc:  # a bad builder must not take the worker down
            self.projection_conn.rollback()
            status = self.outbox.fail(
                job.job_id, f"{type(exc).__name__}: {exc}", max_attempts=self.max_attempts
            )
            self.metrics.failed += 1
            if status == JobStatus.DEAD_LETTER.value:
                self.metrics.dead_lettered += 1
            logger.warning(
                "projection build failed",
                extra={"job_id": job.job_id, "error": str(exc), "status": status},
            )
            return status
        finally:
            self.metrics.build_seconds += time.perf_counter() - started

        if result.outcome == BuildOutcome.SKIPPED.value:
            # Roll the ledger insert back: nothing was projected, so nothing
            # should claim to have been.
            self.projection_conn.rollback()
            self.outbox.skip(job.event_id, job.projection_type, reason=result.reason)
            self.metrics.skipped += 1
            return JobStatus.SKIPPED.value

        if result.target_id:
            self.projection_conn.execute(
                "UPDATE projection_ledger SET target_id=? WHERE projection_key=?",
                (result.target_id, job.key),
            )
        # The one commit: ledger row, projection and target_id together.
        self.projection_conn.commit()
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_PROJECTION_COMMIT)

        # 6. Verify before claiming success.
        if self.ledger_entry(job.key) is None:
            status = self.outbox.fail(
                job.job_id, "projection verification failed: no ledger row after build",
                max_attempts=self.max_attempts,
            )
            self.metrics.failed += 1
            return status

        _fault.fire(_fault.ProjectionFaultPoint.BEFORE_OUTBOX_COMPLETE)
        self.outbox.complete(job.job_id)
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_OUTBOX_COMPLETE)
        self.metrics.completed += 1
        return JobStatus.COMPLETED.value

    # -- loops -------------------------------------------------------------

    def run_once(self, *, batch_size: int = 10) -> WorkerMetrics:
        """Claim and process up to `batch_size` jobs, then return."""
        self.metrics.cycles += 1
        _fault.fire(_fault.ProjectionFaultPoint.BEFORE_CLAIM)
        jobs = self.outbox.claim(
            self.worker_id, limit=batch_size, lease_seconds=self.lease_seconds
        )
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_CLAIM)
        if self.tenant_id:
            jobs = [j for j in jobs if j.tenant_id == self.tenant_id]
        self.metrics.claimed += len(jobs)
        for job in jobs:
            if self.stopping:
                break
            self.process(job)
        return self.metrics

    def run_forever(
        self,
        *,
        batch_size: int = 10,
        poll_seconds: float = 1.0,
        max_cycles: int | None = None,
        max_seconds: float | None = None,
    ) -> WorkerMetrics:
        """Poll until asked to stop, or until a bound is reached.

        `max_cycles` and `max_seconds` exist so a "forever" loop is always
        bounded in a test and in a supervised run. An unbounded loop with no
        stop condition is the failure mode the directive names first.
        """
        started = time.time()
        while not self.stopping:
            if max_cycles is not None and self.metrics.cycles >= max_cycles:
                break
            if max_seconds is not None and (time.time() - started) >= max_seconds:
                break
            before = self.metrics.claimed
            self.run_once(batch_size=batch_size)
            # Between cycles, outside any transaction, and a no-op unless the
            # interval has elapsed. A checkpoint inside a batch would hold the
            # write lock the batch is trying to use.
            if self.wal_manager is not None:
                try:
                    self.wal_manager.maybe_checkpoint()
                except Exception:  # a checkpoint must never take the worker down
                    logger.exception("wal checkpoint failed")
            if self.metrics.claimed == before and not self.stopping:
                # Queue is empty; sleep in slices so a stop is prompt.
                slept = 0.0
                while slept < poll_seconds and not self.stopping:
                    time.sleep(min(0.05, poll_seconds - slept))
                    slept += 0.05
        return self.metrics


def worker_for(memory_os: Any, *, manage_wal: bool = True, **kwargs: Any) -> ReconciliationWorker:
    """Wire a worker to a MemoryOS with the connections in the right places.

    The queue lives with the event store; the ledger and the projection live
    with the memory store. Getting that backwards costs one busy_timeout per
    job and buys no atomicity — see `ReconciliationWorker.projection_conn`.

    `manage_wal` attaches a checkpoint manager on the queue connection. A
    `run_forever` worker is the one long-lived process the runtime is sure to
    have, which makes it the right place to do periodic maintenance — but the
    checkpointing itself only ever happens between cycles.
    """
    if manage_wal and "wal_manager" not in kwargs:
        from .wal import manager_for as _wal_manager_for

        kwargs["wal_manager"] = _wal_manager_for(memory_os)
    return ReconciliationWorker(
        memory_os.events.conn,
        projection_conn=memory_os.memories.conn,
        outbox=memory_os.events.outbox,
        builders=build_default_builders(memory_os.memories),
        **kwargs,
    )


def build_default_builders(memory_store: Any) -> dict[str, ProjectionBuilder]:
    """The builders shipped with the kernel.

    Only `cognitive_memory` today. The other four registry types have no
    builder yet, and a job for one of them dead-letters with that stated
    reason rather than silently doing nothing.
    """
    return {CognitiveMemoryBuilder.projection_type: CognitiveMemoryBuilder(memory_store)}


__all__ = [
    "LEDGER_SCHEMA",
    "BuildOutcome",
    "BuildResult",
    "CognitiveMemoryBuilder",
    "ProjectionBuilder",
    "ReconciliationWorker",
    "WorkerMetrics",
    "build_default_builders",
    "worker_for",
]
