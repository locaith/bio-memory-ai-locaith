"""The transactional outbox: atomicity, idempotency, and safe claiming.

These tests exist because of the gap documented in
docs/v082/WRITE_PATH_ANALYSIS.md: an event could commit while the projection
it owed was never recorded anywhere.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from bio_agent_os.cognitive.event_store import SQLiteEventStore
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.outbox import (
    PROJECTION_VERSION,
    JobStatus,
    ProjectionJob,
    ProjectionOutbox,
    projection_key,
)


def _event(event_id: str = "evt-1", tenant: str = "t1") -> EventRecord:
    return EventRecord(
        tenant_id=tenant,
        actor="tester",
        source="unit",
        payload={"content": "hello"},
        event_id=event_id,
    )


@pytest.fixture()
def store(tmp_path) -> SQLiteEventStore:
    return SQLiteEventStore(tmp_path / "events.db")


# -- idempotency key --------------------------------------------------------


def test_projection_key_is_deterministic():
    a = projection_key("evt-1", "memory", 1)
    b = projection_key("evt-1", "memory", 1)
    assert a == b and len(a) == 64


def test_projection_key_separates_type_and_version():
    base = projection_key("evt-1", "memory", 1)
    assert projection_key("evt-1", "summary", 1) != base
    assert projection_key("evt-1", "memory", 2) != base, (
        "a version bump must be able to rebuild without colliding"
    )
    assert projection_key("evt-2", "memory", 1) != base


def test_key_is_not_confusable_across_field_boundaries():
    """Concatenating without a separator would make these collide."""
    assert projection_key("a", "bc", 1) != projection_key("ab", "c", 1)


# -- atomicity: the whole point ---------------------------------------------


def test_event_and_outbox_commit_together(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    events = store.conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0]
    jobs = store.conn.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0]
    assert events == 1 and jobs == 1


def test_a_crash_between_the_two_inserts_loses_both(tmp_path):
    """The window is closed: a failure after the event insert but before the
    commit rolls the event back too, so there is never a durable event with no
    record of what it owed."""
    path = tmp_path / "events.db"
    store = SQLiteEventStore(path)

    record = _event()
    store.conn.execute(
        "INSERT INTO cognitive_events(event_id, tenant_id, workspace_id, actor, source,"
        " payload_json, trust_tier, security_label, valid_from, valid_to, observed_at,"
        " checksum, metadata_json, modality, epistemic_status)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record.event_id, record.tenant_id, None, record.actor, record.source,
            '{"content":"hello"}', 2, "internal", None, None, record.observed_at,
            "deadbeef", "{}", "text", "observed",
        ),
    )
    # process dies here — no commit
    store.conn.rollback()

    reopened = SQLiteEventStore(path)
    assert reopened.conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 0
    assert reopened.conn.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == 0


def test_no_projection_types_means_nothing_is_owed(store: SQLiteEventStore):
    """observe() without remember() is legal and must not create a phantom job."""
    store.append(_event())
    assert store.conn.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == 0


def test_multiple_projection_types_enqueue_separately(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory", "summary"))
    assert len(store.outbox.by_event("evt-1")) == 2


def test_appending_the_same_event_twice_does_not_duplicate_jobs(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    try:
        store.append(_event(), projection_types=("memory",))
    except sqlite3.IntegrityError:
        pass  # the event PK rejects it; the outbox must not have grown either
    assert len(store.outbox.by_event("evt-1")) == 1


def test_enqueue_is_idempotent_on_the_key(store: SQLiteEventStore):
    job = ProjectionJob(event_id="evt-9", projection_type="memory", tenant_id="t1")
    store.outbox.enqueue(job, commit=True)
    store.outbox.enqueue(
        ProjectionJob(event_id="evt-9", projection_type="memory", tenant_id="t1"), commit=True
    )
    assert len(store.outbox.by_event("evt-9")) == 1


def test_enqueue_does_not_commit_by_default(store: SQLiteEventStore):
    """Committing inside enqueue would reopen the window it exists to close."""
    store.outbox.enqueue(ProjectionJob(event_id="e", projection_type="memory", tenant_id="t"))
    store.conn.rollback()
    assert store.outbox.by_event("e") == []


# -- claiming ---------------------------------------------------------------


def test_claim_marks_in_progress_and_counts_the_attempt(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    claimed = store.outbox.claim("worker-1")
    assert len(claimed) == 1
    assert claimed[0].status == JobStatus.IN_PROGRESS.value
    assert claimed[0].locked_by == "worker-1"
    assert claimed[0].attempts == 1


def test_two_workers_cannot_claim_the_same_job(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    first = store.outbox.claim("worker-1")
    second = store.outbox.claim("worker-2")
    assert len(first) == 1 and second == []


def test_an_expired_lease_is_reclaimable(store: SQLiteEventStore):
    """A worker that died holding a job must not block it forever.

    The lock is taken now and time is advanced afterwards. Claiming with a
    past `now` would fail the availability filter instead, so the first worker
    would never have held the job at all.
    """
    store.append(_event(), projection_types=("memory",))
    held = store.outbox.claim("dead-worker")
    assert len(held) == 1 and held[0].attempts == 1

    later = time.time() + 10_000
    reclaimed = store.outbox.claim("live-worker", now=later, lease_seconds=300)
    assert len(reclaimed) == 1
    assert reclaimed[0].locked_by == "live-worker"
    assert reclaimed[0].attempts == 2, "the reclaim counts as another attempt"


def test_a_fresh_lease_is_not_stolen(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    store.outbox.claim("worker-1")
    assert store.outbox.claim("worker-2", lease_seconds=300) == []


def test_claim_respects_the_limit(store: SQLiteEventStore):
    for i in range(5):
        store.append(_event(f"evt-{i}"), projection_types=("memory",))
    assert len(store.outbox.claim("w", limit=3)) == 3


# -- completion and failure -------------------------------------------------


def test_complete_clears_the_lock(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    job = store.outbox.claim("w")[0]
    assert store.outbox.complete(job.job_id)
    done = store.outbox.get(job.job_id)
    assert done.status == JobStatus.COMPLETED.value
    assert done.locked_by is None and done.completed_at is not None


def test_failure_retries_with_backoff_then_dead_letters(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    job_id = store.outbox.claim("w")[0].job_id

    status = store.outbox.fail(job_id, "boom", max_attempts=3)
    assert status == JobStatus.PENDING.value
    assert store.outbox.get(job_id).available_at > time.time(), "backoff must delay the retry"

    for _ in range(3):
        claimed = store.outbox.claim("w", now=time.time() + 10_000)
        if claimed:
            status = store.outbox.fail(claimed[0].job_id, "boom again", max_attempts=3)
    assert status == JobStatus.DEAD_LETTER.value
    assert len(store.outbox.dead_letters()) == 1


def test_a_dead_letter_is_not_reclaimed(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    job_id = store.outbox.claim("w")[0].job_id
    store.outbox.fail(job_id, "fatal", max_attempts=1)
    assert store.outbox.claim("w", now=time.time() + 10_000) == []


def test_backoff_grows_and_is_capped():
    job = ProjectionJob(event_id="e", projection_type="memory", tenant_id="t")
    job.attempts = 1
    first = job.backoff_seconds()
    job.attempts = 4
    assert job.backoff_seconds() > first
    job.attempts = 50
    assert job.backoff_seconds() == 300.0


def test_skip_records_that_nothing_is_owed(store: SQLiteEventStore):
    """A declined projection is a decision, not an absence."""
    store.append(_event(), projection_types=("memory",))
    assert store.outbox.skip("evt-1", "memory", reason="quarantined")
    job = store.outbox.by_event("evt-1")[0]
    assert job.status == JobStatus.SKIPPED.value
    assert job.last_error == "quarantined"
    assert store.outbox.claim("w") == [], "a skipped job must not be picked up"


# -- observability ----------------------------------------------------------


def test_counts_cover_every_status(store: SQLiteEventStore):
    store.append(_event("a"), projection_types=("memory",))
    store.append(_event("b"), projection_types=("memory",))
    store.outbox.complete(store.outbox.claim("w")[0].job_id)
    counts = store.outbox.counts()
    assert counts[JobStatus.COMPLETED.value] == 1
    assert counts[JobStatus.PENDING.value] == 1
    assert set(counts) == {s.value for s in JobStatus}


def test_lag_reports_the_oldest_pending_job(store: SQLiteEventStore):
    assert store.outbox.lag_seconds() == 0.0
    store.append(_event(), projection_types=("memory",))
    assert store.outbox.lag_seconds(now=time.time() + 60) >= 60


def test_stale_lists_abandoned_jobs(store: SQLiteEventStore):
    store.append(_event(), projection_types=("memory",))
    store.outbox.claim("dead")
    assert store.outbox.stale(lease_seconds=300) == [], "a fresh lock is not stale"
    later = time.time() + 10_000
    assert len(store.outbox.stale(lease_seconds=300, now=later)) == 1


def test_pending_can_be_filtered_by_tenant(store: SQLiteEventStore):
    store.append(_event("a", "t1"), projection_types=("memory",))
    store.append(_event("b", "t2"), projection_types=("memory",))
    assert len(store.outbox.pending(tenant_id="t1")) == 1
    assert len(store.outbox.pending()) == 2


# -- the existing contract is untouched -------------------------------------


def test_events_remain_append_only(store: SQLiteEventStore):
    store.append(_event())
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE cognitive_events SET actor='x' WHERE event_id='evt-1'")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM cognitive_events WHERE event_id='evt-1'")


def test_checksum_chain_still_verifies(store: SQLiteEventStore):
    store.append(_event("a"), projection_types=("memory",))
    store.append(_event("b"), projection_types=("memory",))
    assert store.verify_chain("t1")
