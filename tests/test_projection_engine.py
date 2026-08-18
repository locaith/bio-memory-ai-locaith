"""Registry and replay engine: what is owed, in what order, and never guessed."""

from __future__ import annotations

import time

import pytest

from bio_agent_os.cognitive.event_store import SQLiteEventStore
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob
from bio_agent_os.cognitive.projection_engine import (
    ProjectionReplayEngine,
    ReplayReason,
)
from bio_agent_os.cognitive.projection_registry import (
    REGISTRY,
    DependencyState,
    ProjectionType,
    current_version,
    dependencies_of,
    dependents_of,
    detect_cycles,
    spec,
    topological_order,
)

MEMORY = ProjectionType.COGNITIVE_MEMORY.value
CONTEXT = ProjectionType.CONTEXT_BLOCK.value
CHECKPOINT = ProjectionType.CHECKPOINT_REFERENCE.value


def _event(event_id: str = "evt-1", tenant: str = "t1") -> EventRecord:
    return EventRecord(
        tenant_id=tenant, actor="tester", source="unit",
        payload={"content": "hello"}, event_id=event_id,
    )


@pytest.fixture()
def store(tmp_path) -> SQLiteEventStore:
    return SQLiteEventStore(tmp_path / "events.db")


@pytest.fixture()
def engine(store: SQLiteEventStore) -> ProjectionReplayEngine:
    return ProjectionReplayEngine(store.conn, store.outbox)


# -- registry ---------------------------------------------------------------


def test_registry_covers_every_target_store():
    # Named for the property, not the count: the old name said "six stores"
    # while asserting five, and then a sixth arrived.
    stores = {s.target_store for s in REGISTRY.values()}
    assert stores == {"memories", "self_model", "prospective", "context_blocks",
                      "checkpoints", "hippocampus_labels"}
    assert len(stores) == len(REGISTRY), "two types share a target store"


def test_context_and_checkpoint_depend_on_memory():
    assert dependencies_of(CONTEXT) == (ProjectionType.COGNITIVE_MEMORY,)
    assert dependencies_of(CHECKPOINT) == (ProjectionType.COGNITIVE_MEMORY,)
    assert dependencies_of(MEMORY) == ()


def test_dependents_are_discoverable():
    assert set(dependents_of(MEMORY)) == {
        ProjectionType.CONTEXT_BLOCK, ProjectionType.CHECKPOINT_REFERENCE
    }


def test_topological_order_puts_parents_first():
    order = topological_order()
    assert order.index(ProjectionType.COGNITIVE_MEMORY) < order.index(ProjectionType.CONTEXT_BLOCK)
    assert order.index(ProjectionType.COGNITIVE_MEMORY) < order.index(
        ProjectionType.CHECKPOINT_REFERENCE
    )
    assert len(order) == len(REGISTRY)


def test_registry_has_no_cycles():
    assert detect_cycles() == []


def test_a_cycle_is_reported_not_swallowed(monkeypatch):
    """A cycle is a design error; returning a partial order would hide it."""
    from dataclasses import replace

    patched = dict(REGISTRY)
    patched[ProjectionType.COGNITIVE_MEMORY] = replace(
        REGISTRY[ProjectionType.COGNITIVE_MEMORY],
        depends_on=(ProjectionType.CONTEXT_BLOCK,),
    )
    monkeypatch.setattr(
        "bio_agent_os.cognitive.projection_registry.REGISTRY", patched
    )
    assert detect_cycles(), "a cycle must be reported"
    with pytest.raises(ValueError, match="cycle"):
        topological_order()


def test_checkpoint_is_marked_unreplayable():
    """It pins a memory version; rebuilding would silently repoint it."""
    assert not spec(CHECKPOINT).replayable
    assert spec(MEMORY).replayable


def test_versions_are_per_type():
    assert current_version(MEMORY) == 1
    assert current_version(CONTEXT) == 1


# -- discovery --------------------------------------------------------------


def test_an_event_with_no_outbox_row_is_reported_as_owed(store, engine):
    store.append(_event())  # legacy path: no projection recorded
    report = engine.scan()
    memory_candidates = [c for c in report.candidates if c.projection_type == MEMORY]
    assert len(memory_candidates) == 1
    assert memory_candidates[0].reason == ReplayReason.MISSING_OUTBOX.value


def test_an_optional_projection_is_not_treated_as_an_orphan(store, engine):
    """Only the memory projection is owed by default; the rest are opt-in."""
    store.append(_event())
    types = {c.projection_type for c in engine.scan().candidates}
    assert types == {MEMORY}


def test_a_pending_job_is_found(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    assert engine.scan().candidates[0].reason == ReplayReason.PENDING.value


def test_a_completed_job_is_not_reported(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.complete(job.job_id)
    assert engine.scan().candidates == []


def test_a_skipped_job_is_not_reported(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    store.outbox.skip("evt-1", MEMORY, reason="quarantined")
    assert engine.scan().candidates == []


def test_a_stale_lease_is_found(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    store.outbox.claim("dead-worker")
    fresh = engine.scan(lease_seconds=300)
    assert fresh.candidates == [], "a live lease is not owed work"
    # `scan(lease_seconds=0)` KHÔNG bị cấm và không được migrate đi: đây là máy
    # dò chỉ-đọc, tham số là NGƯỠNG của một câu hỏi ("dưới hạn L thì hàng nào
    # coi như bị bỏ rơi?"), không phải một lease được cấp. Cấm zero ở đây là
    # một correction áp lên nhầm tập hợp — validate_lease_seconds chỉ gác
    # claim(), nơi zero thật sự phá quyền sở hữu độc quyền.
    stale = engine.scan(lease_seconds=0)
    assert stale.candidates[0].reason == ReplayReason.STALE_LEASE.value


def test_a_dead_letter_is_found(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.fail(job.job_id, "fatal", max_attempts=1)
    assert engine.scan().candidates[0].reason == ReplayReason.DEAD_LETTER.value


def test_version_drift_is_found(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.complete(job.job_id)
    # Simulate a row written by an older projection version: both the version
    # and its derived key must move together, or the row is a state that could
    # never have been produced.
    from bio_agent_os.cognitive.outbox import projection_key as _key

    store.conn.execute(
        "UPDATE projection_outbox SET projection_version=0, projection_key=? WHERE job_id=?",
        (_key(job.event_id, job.projection_type, 0), job.job_id),
    )
    store.conn.commit()
    assert engine.scan().candidates[0].reason == ReplayReason.VERSION_DRIFT.value


# -- dependency states ------------------------------------------------------


def test_a_dependent_job_waits_for_its_parent(store, engine):
    store.append(_event(), projection_types=(MEMORY, CONTEXT))
    context = [c for c in engine.scan().candidates if c.projection_type == CONTEXT][0]
    assert context.dependency_state == DependencyState.PENDING_DEPENDENCY.value
    assert not context.actionable, "must not be attempted before its parent"


def test_a_dependent_job_becomes_ready_once_the_parent_completes(store, engine):
    store.append(_event(), projection_types=(MEMORY, CONTEXT))
    parent = [j for j in store.outbox.by_event("evt-1") if j.projection_type == MEMORY][0]
    store.outbox.claim("w")
    store.outbox.complete(parent.job_id)

    context = [c for c in engine.scan().candidates if c.projection_type == CONTEXT][0]
    assert context.dependency_state == DependencyState.READY.value
    assert context.actionable


def test_a_dead_parent_blocks_rather_than_pends(store, engine):
    """BLOCKED and PENDING_DEPENDENCY are different: one can still resolve."""
    store.append(_event(), projection_types=(MEMORY, CONTEXT))
    parent = [j for j in store.outbox.by_event("evt-1") if j.projection_type == MEMORY][0]
    store.outbox.fail(parent.job_id, "fatal", max_attempts=0)

    context = [c for c in engine.scan().candidates if c.projection_type == CONTEXT][0]
    assert context.dependency_state == DependencyState.BLOCKED.value


# -- replay -----------------------------------------------------------------


def test_replay_defaults_to_dry_run_and_changes_nothing(store, engine):
    store.append(_event())
    report = engine.replay()
    assert report.dry_run
    assert report.enqueued == 0
    assert store.outbox.by_event("evt-1") == []


def test_replay_enqueues_a_missing_projection(store, engine):
    store.append(_event())
    report = engine.replay(dry_run=False)
    assert report.enqueued == 1
    jobs = store.outbox.by_event("evt-1")
    assert len(jobs) == 1 and jobs[0].status == JobStatus.PENDING.value


def test_replay_twice_does_not_duplicate(store, engine):
    store.append(_event())
    engine.replay(dry_run=False)
    engine.replay(dry_run=False)
    assert len(store.outbox.by_event("evt-1")) == 1


def test_replay_resets_a_dead_letter_and_clears_its_attempts(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.fail(job.job_id, "fatal", max_attempts=1)

    engine.replay(dry_run=False)
    revived = store.outbox.get(job.job_id)
    assert revived.status == JobStatus.PENDING.value
    assert revived.attempts == 0, "a deliberate replay is not another retry"
    assert revived.last_error is None


def test_replay_scopes_to_one_event(store, engine):
    store.append(_event("a"))
    store.append(_event("b"))
    engine.replay(event_id="a", dry_run=False)
    assert len(store.outbox.by_event("a")) == 1
    assert store.outbox.by_event("b") == []


def test_replay_scopes_to_one_tenant(store, engine):
    store.append(_event("a", "t1"))
    store.append(_event("b", "t2"))
    engine.replay(tenant_id="t1", dry_run=False)
    assert len(store.outbox.by_event("a")) == 1
    assert store.outbox.by_event("b") == []


def test_replay_holds_back_an_unreplayable_projection(store, engine):
    store.append(_event(), projection_types=(MEMORY, CHECKPOINT))
    parent = [j for j in store.outbox.by_event("evt-1") if j.projection_type == MEMORY][0]
    store.outbox.claim("w")
    store.outbox.complete(parent.job_id)

    report = engine.replay(dry_run=False)
    assert report.skipped_unreplayable >= 1
    assert "NOT REBUILT AUTOMATICALLY" in report.render()


def test_replay_holds_back_a_job_whose_parent_is_unfinished(store, engine):
    store.append(_event(), projection_types=(MEMORY, CONTEXT))
    report = engine.replay(dry_run=False)
    assert report.skipped_dependency >= 1


def test_replay_never_deletes_an_event(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.fail(job.job_id, "fatal", max_attempts=1)
    engine.replay(dry_run=False)
    assert store.conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 1


# -- version rebuild --------------------------------------------------------


def test_rebuild_skips_events_already_at_the_current_version(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.complete(job.job_id)
    report = engine.rebuild_version(MEMORY, dry_run=False)
    assert report.enqueued == 0


def test_rebuild_enqueues_events_at_an_older_version(store, engine):
    store.append(_event(), projection_types=(MEMORY,))
    job = store.outbox.claim("w")[0]
    store.outbox.complete(job.job_id)
    # Simulate a row written by an older projection version: both the version
    # and its derived key must move together, or the row is a state that could
    # never have been produced.
    from bio_agent_os.cognitive.outbox import projection_key as _key

    store.conn.execute(
        "UPDATE projection_outbox SET projection_version=0, projection_key=? WHERE job_id=?",
        (_key(job.event_id, job.projection_type, 0), job.job_id),
    )
    store.conn.commit()
    assert engine.rebuild_version(MEMORY, dry_run=False).enqueued == 1


def test_rebuild_refuses_an_unreplayable_type(store, engine):
    store.append(_event())
    report = engine.rebuild_version(CHECKPOINT, dry_run=False)
    assert report.enqueued == 0
    assert report.skipped_unreplayable == 1


# -- orphans and status -----------------------------------------------------


def test_an_outbox_row_without_an_event_is_reported(store, engine):
    store.outbox.enqueue(
        ProjectionJob(event_id="ghost", projection_type=MEMORY, tenant_id="t1"), commit=True
    )
    orphans = engine.orphan_projections()
    assert len(orphans) == 1 and orphans[0]["event_id"] == "ghost"


def test_status_summarises_the_queue(store, engine):
    store.append(_event("a"), projection_types=(MEMORY,))
    store.append(_event("b"))
    status = engine.status()
    assert status["events"] == 2
    assert status["outbox"][JobStatus.PENDING.value] == 1
    assert status["owed"] >= 2
    assert ReplayReason.MISSING_OUTBOX.value in status["owed_by_reason"]


def test_status_counts_dependency_holds(store, engine):
    store.append(_event(), projection_types=(MEMORY, CONTEXT))
    assert engine.status()["held_by_dependency"] >= 1
