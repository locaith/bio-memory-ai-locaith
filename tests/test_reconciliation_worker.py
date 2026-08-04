"""The worker that drains the queue — including the crash it exists to survive."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from bio_agent_os.cognitive import MemoryOS
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob
from bio_agent_os.cognitive.projection_registry import DependencyState, ProjectionType
from bio_agent_os.cognitive.reconciliation_worker import (
    BuildOutcome,
    BuildResult,
    CognitiveMemoryBuilder,
    ReconciliationWorker,
    build_default_builders,
    worker_for,
)

MEMORY = ProjectionType.COGNITIVE_MEMORY.value
CONTEXT = ProjectionType.CONTEXT_BLOCK.value


@pytest.fixture()
def os_(tmp_path) -> MemoryOS:
    return MemoryOS(tmp_path / "runtime.db")


@pytest.fixture()
def worker(os_: MemoryOS) -> ReconciliationWorker:
    return worker_for(os_, worker_id="w1")


def _append(os_: MemoryOS, event_id: str = "evt-1", tenant: str = "t1",
            content: str = "remember me", types=(MEMORY,)) -> EventRecord:
    return os_.events.append(
        EventRecord(
            tenant_id=tenant, actor="tester", source="unit",
            payload={"content": content}, event_id=event_id,
        ),
        projection_types=types,
    )


def _memory_count(os_: MemoryOS, event_id: str) -> int:
    return os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE source_event_ids_json LIKE ?",
        (f"%{event_id}%",),
    ).fetchone()[0]


# -- the happy path ---------------------------------------------------------


def test_worker_drains_a_pending_job(os_, worker):
    _append(os_)
    worker.run_once()
    assert worker.metrics.completed == 1
    assert _memory_count(os_, "evt-1") == 1
    assert os_.events.outbox.by_event("evt-1")[0].status == JobStatus.COMPLETED.value


def test_projection_carries_its_full_provenance(os_, worker):
    _append(os_)
    worker.run_once()
    row = os_.memories.conn.execute(
        "SELECT metadata_json, tenant_id FROM cognitive_memories"
    ).fetchone()
    import json

    meta = json.loads(row["metadata_json"])
    assert meta["source_event_id"] == "evt-1"
    assert meta["projection_type"] == MEMORY
    assert meta["projection_version"] == 1
    assert len(meta["projection_key"]) == 64
    assert row["tenant_id"] == "t1"


def test_batch_processing(os_, worker):
    for i in range(5):
        _append(os_, f"evt-{i}")
    worker.run_once(batch_size=10)
    assert worker.metrics.completed == 5


def test_an_empty_queue_is_a_no_op(os_, worker):
    worker.run_once()
    assert worker.metrics.claimed == 0 and worker.metrics.completed == 0


# -- THE crash window -------------------------------------------------------


def test_retry_after_a_crash_between_projection_and_completion(os_, worker):
    """The failure this whole design exists for.

    The projection commits, the process dies before the outbox row is marked
    completed, and the job is retried. Exactly one projection must exist.
    """
    _append(os_)
    job = os_.events.outbox.claim("crashing-worker")[0]

    # Build the projection and its ledger row, then die: no complete() call.
    worker._record_ledger(job, target_id=None)
    CognitiveMemoryBuilder(os_.memories).build(
        worker._load_event(job), job, os_.memories.conn
    )
    assert _memory_count(os_, "evt-1") == 1
    assert os_.events.outbox.get(job.job_id).status == JobStatus.IN_PROGRESS.value

    # A new worker reclaims the stale lease and retries.
    survivor = worker_for(os_, worker_id="w2", lease_seconds=0)
    survivor.run_once()

    assert _memory_count(os_, "evt-1") == 1, "the retry must not duplicate the projection"
    assert os_.events.outbox.get(job.job_id).status == JobStatus.COMPLETED.value
    assert survivor.metrics.already_built == 1


def test_the_ledger_is_what_makes_the_retry_safe(os_, worker):
    _append(os_)
    worker.run_once()
    key = os_.events.outbox.by_event("evt-1")[0].key
    assert worker.ledger_entry(key) is not None

    # Force the job back to pending and run again.
    os_.events.conn.execute(
        "UPDATE projection_outbox SET status=?, locked_at=NULL WHERE event_id=?",
        (JobStatus.PENDING.value, "evt-1"),
    )
    os_.events.conn.commit()
    worker.run_once()
    assert _memory_count(os_, "evt-1") == 1


def test_a_skipped_build_leaves_no_ledger_row(os_, worker):
    """A declined projection must not claim to have produced one."""
    _append(os_, content="   ")
    worker.run_once()
    job = os_.events.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.SKIPPED.value
    assert worker.ledger_entry(job.key) is None
    assert _memory_count(os_, "evt-1") == 0
    assert worker.metrics.skipped == 1


# -- concurrency ------------------------------------------------------------


def test_two_workers_cannot_process_the_same_job(os_):
    _append(os_)
    builders = build_default_builders(os_.memories)
    a = worker_for(os_, worker_id="a")
    b = worker_for(os_, worker_id="b")
    a.run_once()
    b.run_once()
    assert a.metrics.completed == 1
    assert b.metrics.claimed == 0
    assert _memory_count(os_, "evt-1") == 1


def test_a_stale_lease_is_reclaimed(os_):
    _append(os_)
    os_.events.outbox.claim("dead")
    survivor = worker_for(os_, worker_id="alive", lease_seconds=0)
    survivor.run_once()
    assert survivor.metrics.completed == 1


# -- dependencies -----------------------------------------------------------


def test_a_dependent_job_is_held_not_failed(os_, worker):
    _append(os_, types=(MEMORY, CONTEXT))
    context_job = [j for j in os_.events.outbox.by_event("evt-1")
                   if j.projection_type == CONTEXT][0]
    result = worker.process(context_job)
    assert result == DependencyState.PENDING_DEPENDENCY.value
    assert worker.metrics.dependency_held == 1
    assert os_.events.outbox.get(context_job.job_id).status == JobStatus.PENDING.value, (
        "waiting on a parent is not a failure"
    )


def test_a_dead_parent_blocks_the_child(os_, worker):
    _append(os_, types=(MEMORY, CONTEXT))
    jobs = {j.projection_type: j for j in os_.events.outbox.by_event("evt-1")}
    os_.events.outbox.fail(jobs[MEMORY].job_id, "fatal", max_attempts=0)

    result = worker.process(jobs[CONTEXT])
    assert result == JobStatus.DEAD_LETTER.value
    assert worker.metrics.blocked == 1


def test_an_unreplayable_projection_is_not_rebuilt_on_retry(os_, worker):
    """checkpoint_reference pins a memory version; a rebuild would repoint it."""
    _append(os_, types=(ProjectionType.CHECKPOINT_REFERENCE.value,))
    job = os_.events.outbox.by_event("evt-1")[0]
    # Complete the (absent) parent so dependency does not mask the check.
    os_.events.outbox.enqueue(
        ProjectionJob(event_id="evt-1", projection_type=MEMORY, tenant_id="t1"), commit=True
    )
    parent = [j for j in os_.events.outbox.by_event("evt-1") if j.projection_type == MEMORY][0]
    os_.events.outbox.complete(parent.job_id)

    job.attempts = 2  # a retry, not the first attempt
    assert worker.process(job) == JobStatus.DEAD_LETTER.value


# -- failure handling -------------------------------------------------------


class _Exploding:
    def build(self, event, job, conn):
        raise RuntimeError("builder exploded")


def test_a_failing_builder_does_not_take_the_worker_down(os_):
    _append(os_)
    w = ReconciliationWorker(os_.events.conn, projection_conn=os_.memories.conn,
        outbox=os_.events.outbox, builders={MEMORY: _Exploding()}, worker_id="w", max_attempts=3)
    w.run_once()
    assert w.metrics.failed == 1
    job = os_.events.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.PENDING.value
    assert "builder exploded" in job.last_error


def test_a_failed_build_leaves_no_ledger_row(os_):
    _append(os_)
    w = ReconciliationWorker(os_.events.conn, projection_conn=os_.memories.conn,
        outbox=os_.events.outbox, builders={MEMORY: _Exploding()}, worker_id="w")
    w.run_once()
    key = os_.events.outbox.by_event("evt-1")[0].key
    assert w.ledger_entry(key) is None, "a failed build must not look completed"


def test_backoff_delays_the_next_attempt(os_):
    """A failing job must not be retried immediately in a tight loop."""
    _append(os_)
    w = ReconciliationWorker(os_.events.conn, projection_conn=os_.memories.conn,
        outbox=os_.events.outbox, builders={MEMORY: _Exploding()}, worker_id="w",
        max_attempts=5, lease_seconds=0)
    w.run_once()
    job = os_.events.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.PENDING.value
    assert job.available_at > time.time(), "the retry must be pushed into the future"

    w.run_once()
    assert w.metrics.claimed == 1, "backoff must prevent an immediate re-claim"


def test_repeated_failure_reaches_dead_letter(os_):
    _append(os_)
    w = ReconciliationWorker(os_.events.conn, projection_conn=os_.memories.conn,
        outbox=os_.events.outbox, builders={MEMORY: _Exploding()}, worker_id="w",
        max_attempts=2, lease_seconds=0)
    for _ in range(4):
        # Simulate the backoff window elapsing rather than sleeping through it.
        os_.events.conn.execute(
            "UPDATE projection_outbox SET available_at=? WHERE event_id=?",
            (time.time() - 1, "evt-1"),
        )
        os_.events.conn.commit()
        w.run_once()

    job = os_.events.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.DEAD_LETTER.value
    assert job.attempts >= 2
    assert w.metrics.dead_lettered >= 1


def test_a_missing_builder_dead_letters_with_a_reason(os_):
    _append(os_)
    w = ReconciliationWorker(os_.events.conn, projection_conn=os_.memories.conn,
                             outbox=os_.events.outbox, builders={}, worker_id="w")
    w.run_once()
    job = os_.events.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.DEAD_LETTER.value
    assert "no builder registered" in job.last_error


def test_a_job_for_a_missing_event_dead_letters(os_, worker):
    os_.events.outbox.enqueue(
        ProjectionJob(event_id="ghost", projection_type=MEMORY, tenant_id="t1"), commit=True
    )
    worker.run_once()
    assert os_.events.outbox.by_event("ghost")[0].status == JobStatus.DEAD_LETTER.value


# -- tenant isolation -------------------------------------------------------


def test_a_tenant_scoped_worker_ignores_other_tenants(os_):
    _append(os_, "a", tenant="t1")
    _append(os_, "b", tenant="t2")
    w = worker_for(os_, worker_id="w", tenant_id="t1")
    w.run_once(batch_size=10)
    assert _memory_count(os_, "a") == 1
    assert _memory_count(os_, "b") == 0


def test_projections_do_not_cross_tenants(os_, worker):
    _append(os_, "a", tenant="t1")
    _append(os_, "b", tenant="t2")
    worker.run_once(batch_size=10)
    rows = os_.memories.conn.execute(
        "SELECT tenant_id, source_event_ids_json FROM cognitive_memories"
    ).fetchall()
    for row in rows:
        expected = "a" if row["tenant_id"] == "t1" else "b"
        assert expected in row["source_event_ids_json"]


# -- loops and shutdown -----------------------------------------------------


def test_run_forever_is_bounded_by_max_cycles(os_, worker):
    worker.run_forever(max_cycles=3, poll_seconds=0.01)
    assert worker.metrics.cycles == 3


def test_run_forever_is_bounded_by_time(os_, worker):
    started = time.time()
    worker.run_forever(max_seconds=0.2, poll_seconds=0.01)
    assert time.time() - started < 3.0


def test_request_stop_ends_the_loop(os_, worker):
    for i in range(20):
        _append(os_, f"evt-{i}")

    def stopper():
        time.sleep(0.05)
        worker.request_stop()

    t = threading.Thread(target=stopper)
    t.start()
    worker.run_forever(batch_size=1, poll_seconds=0.01, max_seconds=5)
    t.join()
    assert worker.stopping


def test_metrics_are_structured(os_, worker):
    _append(os_)
    worker.run_once()
    data = worker.metrics.as_dict()
    for key in ("claimed", "completed", "skipped", "failed", "dead_lettered",
                "dependency_held", "blocked", "cycles", "mean_build_ms"):
        assert key in data
    assert data["completed"] == 1


# -- the existing contract --------------------------------------------------


def test_the_worker_never_deletes_an_event(os_, worker):
    _append(os_)
    worker.run_once()
    assert os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0] == 1


def test_observe_is_still_legacy(os_):
    """Nothing in this commit wires observe() to the outbox."""
    event = os_.observe(tenant_id="t1", actor="a", source="unit", content="legacy")
    assert os_.events.outbox.by_event(event.event_id) == []
