"""Child-process entry points for the fault matrix.

Module level and picklable, because these must run under the `spawn` start
method — the only one available on Windows and the one that catches import-time
assumptions `fork` would hide.

Every child dies with `os._exit()`. That skips atexit handlers, flushes and
destructors, which is the point: a real crash does not get to tidy up, and a
child that closed its connection politely would prove nothing.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from bio_agent_os.cognitive import fault_points as fault
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.reconciliation_worker import (
    ReconciliationWorker,
    build_default_builders,
    worker_for,
)

#: Exit code used when a child kills itself at a fault point, so a parent can
#: tell a deliberate crash from an ordinary failure.
CRASH_EXIT = 137


def _arm(point: str, reached: Any) -> None:
    """Kill this process the instant the named point is reached."""

    def hook(fired: str) -> None:
        if fired == point:
            if reached is not None:
                reached.set()
            os._exit(CRASH_EXIT)

    fault.install(hook)


def crash_worker_at(db_path: str, point: str, reached: Any, *, lease_seconds: float = 300.0) -> None:
    """Run one worker cycle, dying at `point`."""
    _arm(point, reached)
    os_ = MemoryOS(db_path)
    worker = worker_for(os_, worker_id=f"crash-{os.getpid()}", lease_seconds=lease_seconds)
    worker.run_once()
    # Only reached when the point never fired.
    if reached is not None:
        reached.set()


class UncommittedProjectionBuilder:
    """Writes the projection row but never commits, then fires the fault point.

    Exists because the production builder calls `SQLiteMemoryStore.put()`,
    which does the INSERT and the COMMIT in one call — there is no instruction
    between them for a fault point to sit on. This reproduces exactly that
    intermediate state on the same connection, so the question "does an
    uncommitted projection survive a crash?" can actually be asked.
    """

    def build(self, event: EventRecord, job: Any, conn: Any) -> Any:
        from bio_agent_os.cognitive.reconciliation_worker import BuildOutcome, BuildResult

        conn.execute(
            "INSERT INTO cognitive_memories("
            " memory_id, version, tenant_id, workspace_id, memory_type, content,"
            " structured_json, source_event_ids_json, confidence, importance, salience,"
            " utility, trust_tier, security_label, valid_from, valid_to, observed_at,"
            " superseded_at, reinforcement_count, contradiction_count, retrieval_count,"
            " last_accessed_at, lifecycle_state, governed_exception_for, approved_by,"
            " approval_expires_at, allowed_agents_json, allowed_roles_json,"
            " purpose_allowlist_json, created_at, metadata_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"mem-{job.event_id}", 1, event.tenant_id, event.workspace_id, "episodic",
                str((event.payload or {}).get("content", "")), "{}",
                f'["{event.event_id}"]', 0.6, 0.5, 0.5, 0.5, int(event.trust_tier),
                event.security_label.value, None, None, event.observed_at, None,
                0, 0, 0, None, "proposed", None, None, None, "[]", "[]", "[]",
                event.observed_at, "{}",
            ),
        )
        # Inserted, not committed. Dying here must leave nothing behind.
        fault.fire(fault.ProjectionFaultPoint.AFTER_PROJECTION_WRITE_BEFORE_COMMIT)
        return BuildResult(BuildOutcome.BUILT.value, target_id=f"mem-{job.event_id}")


def crash_uncommitted_projection(db_path: str, reached: Any) -> None:
    """Insert a projection without committing, then die."""
    point = fault.ProjectionFaultPoint.AFTER_PROJECTION_WRITE_BEFORE_COMMIT.value
    _arm(point, reached)
    os_ = MemoryOS(db_path)
    worker = ReconciliationWorker(
        os_.events.conn,
        projection_conn=os_.memories.conn,
        outbox=os_.events.outbox,
        builders={"cognitive_memory": UncommittedProjectionBuilder()},
        worker_id=f"uncommitted-{os.getpid()}",
        # Job đang PENDING — độ dài lease không tham gia vào việc giành nó.
        # Zero cũ chỉ là thói quen của lane, nay bị `validate_lease_seconds`
        # từ chối ngay tại claim().
        lease_seconds=300.0,
    )
    worker.run_once()
    if reached is not None:
        reached.set()


def crash_append_at(db_path: str, point: str, reached: Any, event_id: str, tenant: str) -> None:
    """Append an event, dying at `point` inside the event+outbox transaction."""
    _arm(point, reached)
    os_ = MemoryOS(db_path)
    os_.events.append(
        EventRecord(
            tenant_id=tenant, actor="child", source="fault",
            payload={"content": "crash me"}, event_id=event_id,
        ),
        projection_types=("cognitive_memory",),
    )
    if reached is not None:
        reached.set()


def run_worker(db_path: str, result: Any = None, *, lease_seconds: float = 300.0,
               batch_size: int = 10, worker_id: str | None = None,
               tenant_id: str | None = None, start_gate: Any = None) -> None:
    """A normal worker cycle in its own process. No faults armed."""
    if start_gate is not None:
        start_gate.wait(10)
    os_ = MemoryOS(db_path)
    worker = worker_for(
        os_, worker_id=worker_id or f"w-{os.getpid()}",
        lease_seconds=lease_seconds, tenant_id=tenant_id,
    )
    worker.run_once(batch_size=batch_size)
    if result is not None:
        result.put({
            "pid": os.getpid(),
            "worker_id": worker.worker_id,
            **worker.metrics.as_dict(),
        })


def hold_write_lock(db_path: str, holding: Any, release: Any) -> None:
    """Take a real SQLite write lock and hold it until told to let go."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _lock_probe(id INTEGER PRIMARY KEY, at REAL)"
    )
    conn.execute("INSERT INTO _lock_probe(at) VALUES(?)", (time.time(),))
    holding.set()
    release.wait(30)
    conn.rollback()
    conn.close()


__all__ = [
    "CRASH_EXIT",
    "UncommittedProjectionBuilder",
    "crash_append_at",
    "crash_uncommitted_projection",
    "crash_worker_at",
    "hold_write_lock",
    "run_worker",
]
