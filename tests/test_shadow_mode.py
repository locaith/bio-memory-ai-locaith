"""Shadow mode for cognitive_memory: isolation, determinism, recovery."""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive import fault_points as fault
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.fault_points import ProjectionFaultPoint as FP
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.outbox import JobStatus
from bio_agent_os.cognitive.projection_capability import (
    CAPABILITIES,
    enqueueable,
    is_supported,
    render,
    report,
    shadow_types,
    supported_types,
    unsupported_types,
)
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.shadow import (
    COGNITIVE_MEMORY,
    MODE_ENV,
    CanonicalObservation,
    ComparisonStatus,
    ProjectionMode,
    ShadowReport,
    compare,
    current_mode,
    normalise_text,
    output_hash,
    projected_fields,
)
from bio_agent_os.cognitive.shadow_runner import (
    check_determinism,
    compare_event,
    legacy_projection,
    run_shadow_comparison,
    shadow_projection,
    shadow_worker,
    write_report,
)

sys.path.insert(0, str(Path(__file__).parent))

CTX = mp.get_context("spawn")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(MODE_ENV, raising=False)
    fault.clear()
    yield
    fault.clear()


@pytest.fixture()
def db(tmp_path) -> str:
    return str(tmp_path / "shadow.db")


def _observe_and_remember(os_: MemoryOS, content="hello world", tenant="t1",
                          workspace=None, event_id=None):
    """One observation through both halves of the legacy path."""
    kwargs = {}
    event = os_.observe(
        tenant_id=tenant, actor="tester", source="unit",
        content=content, workspace_id=workspace, **kwargs
    )
    os_.remember(event=event, memory_type=MemoryType.EPISODIC, content=content)
    return event


# ==========================================================================
# 1. Capability registry
# ==========================================================================


def test_only_cognitive_memory_is_supported():
    assert supported_types() == (COGNITIVE_MEMORY,)
    assert set(unsupported_types()) == {
        ProjectionType.SELF_MODEL_UPDATE.value,
        ProjectionType.PROSPECTIVE_MEMORY.value,
        ProjectionType.CONTEXT_BLOCK.value,
        ProjectionType.CHECKPOINT_REFERENCE.value,
    }


def test_the_four_unsupported_types_say_why():
    for ptype in unsupported_types():
        cap = CAPABILITIES[ptype]
        assert cap.status == "unsupported"
        assert not cap.shadow_supported
        assert not cap.replayable, "a type that cannot be built cannot be rebuilt"
        assert cap.reason, f"{ptype} must state why it is unsupported"
        assert cap.target_store is None


def test_capability_report_never_calls_unsupported_passed():
    rows = {r["projection_type"]: r for r in report()}
    assert rows[COGNITIVE_MEMORY]["status"] == "supported"
    for ptype in unsupported_types():
        assert rows[ptype]["status"] == "unsupported"
    assert "unsupported" in render()


def test_enqueueable_splits_supported_from_missing():
    supported, skipped = enqueueable(
        (COGNITIVE_MEMORY, ProjectionType.CONTEXT_BLOCK.value)
    )
    assert supported == (COGNITIVE_MEMORY,)
    assert skipped == (ProjectionType.CONTEXT_BLOCK.value,)


def test_shadow_types_matches_supported_types():
    assert shadow_types() == supported_types()


# ==========================================================================
# 2. Modes
# ==========================================================================


def test_default_mode_is_legacy():
    assert current_mode() is ProjectionMode.LEGACY


def test_mode_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "shadow")
    assert current_mode() is ProjectionMode.SHADOW
    monkeypatch.setenv(MODE_ENV, "outbox")
    assert current_mode() is ProjectionMode.OUTBOX


def test_an_unknown_mode_falls_back_to_legacy(monkeypatch):
    """A typo must not quietly change how memories are written."""
    monkeypatch.setenv(MODE_ENV, "shadowy")
    assert current_mode() is ProjectionMode.LEGACY


def test_legacy_mode_enqueues_nothing(db):
    os_ = MemoryOS(db)
    assert os_.projection_mode is ProjectionMode.LEGACY
    event = _observe_and_remember(os_)
    assert os_.events.outbox.by_event(event.event_id) == []
    assert legacy_projection(os_, event.event_id) is not None
    assert os_.shadow_memories.count() == 0


def test_shadow_mode_enqueues_exactly_one_cognitive_job(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    jobs = os_.events.outbox.by_event(event.event_id)
    assert len(jobs) == 1
    assert jobs[0].projection_type == COGNITIVE_MEMORY


def test_shadow_mode_never_enqueues_unsupported_types(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    types = {j.projection_type for j in os_.events.outbox.by_event(event.event_id)}
    assert types == {COGNITIVE_MEMORY}
    assert not types & set(unsupported_types())


def test_switching_back_to_legacy_leaves_old_data_alone(db):
    shadow_os = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(shadow_os)
    shadow_worker(shadow_os, worker_id="w", lease_seconds=0).run_once()
    before = shadow_os.shadow_memories.count()

    legacy_os = MemoryOS(db)
    new_event = _observe_and_remember(legacy_os, content="later", event_id=None)
    assert legacy_os.events.outbox.by_event(new_event.event_id) == []
    assert legacy_os.shadow_memories.count() == before, "old shadow rows untouched"
    assert legacy_projection(legacy_os, event.event_id) is not None


# ==========================================================================
# 3. Isolation
# ==========================================================================


def test_shadow_projection_is_written_and_counted(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    assert os_.shadow_memories.count() == 1
    assert shadow_projection(os_, event.event_id) is not None


def test_shadow_rows_live_in_their_own_table(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()

    production = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories"
    ).fetchone()[0]
    assert production == 1, "the shadow row must not land in the production table"
    assert os_.shadow_memories.count() == 1


def test_production_recall_cannot_see_shadow(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    _observe_and_remember(os_, content="findable content")
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()

    results = os_.recall("findable content", context=AccessContext(tenant_id="t1"), limit=20)
    for item in results:
        memory = getattr(item, "memory", item)
        assert not str(getattr(memory, "memory_id", "")).startswith("shadow")
    assert len(results) <= 1, "only the production memory may surface"


def test_shadow_does_not_bump_the_legacy_memory_version(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    before = os_.memories.conn.execute(
        "SELECT MAX(version) FROM cognitive_memories"
    ).fetchone()[0]
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    after = os_.memories.conn.execute(
        "SELECT MAX(version) FROM cognitive_memories"
    ).fetchone()[0]
    assert before == after


def test_tenant_isolation_in_the_shadow_table(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    _observe_and_remember(os_, content="tenant a data", tenant="tenant-a")
    _observe_and_remember(os_, content="tenant b data", tenant="tenant-b")
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once(batch_size=10)

    assert os_.shadow_memories.count(tenant_id="tenant-a") == 1
    assert os_.shadow_memories.count(tenant_id="tenant-b") == 1
    rows = os_.memories.conn.execute(
        "SELECT tenant_id, content FROM shadow_memories"
    ).fetchall()
    for row in rows:
        assert row["tenant_id"].split("-")[1] in row["content"]


def test_workspace_isolation_is_carried_through(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    _observe_and_remember(os_, content="ws one", workspace="ws-1")
    _observe_and_remember(os_, content="ws two", workspace="ws-2")
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once(batch_size=10)
    spaces = {
        r["workspace_id"]
        for r in os_.memories.conn.execute("SELECT workspace_id FROM shadow_memories")
    }
    assert spaces == {"ws-1", "ws-2"}


# ==========================================================================
# 4. Canonical input and determinism
# ==========================================================================


def test_canonical_input_is_reconstructed_from_the_event(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_, content="canonical text")
    a = CanonicalObservation.from_event(event)
    b = CanonicalObservation.from_event(event)
    assert a.input_hash() == b.input_hash()
    assert a.content == "canonical text"
    assert a.event_id == event.event_id


def test_the_same_event_projects_to_the_same_output_ten_times(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    n_inputs, n_outputs = check_determinism(os_, event.event_id, runs=10)
    assert (n_inputs, n_outputs) == (1, 1)


def test_different_events_hash_differently(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    a = _observe_and_remember(os_, content="first")
    b = _observe_and_remember(os_, content="second")
    ha = CanonicalObservation.from_event(a).input_hash()
    hb = CanonicalObservation.from_event(b).input_hash()
    assert ha != hb


def test_output_hash_covers_the_projected_fields(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    fields = projected_fields(CanonicalObservation.from_event(event))
    first = output_hash(fields)
    fields["content"] = fields["content"] + " altered"
    assert output_hash(fields) != first


# ==========================================================================
# 5. Comparator
# ==========================================================================


def _pair(**overrides):
    base = {
        "tenant_id": "t1", "workspace_id": None, "memory_type": "episodic",
        "content": "same text", "confidence": 0.6, "epistemic_status": "observed",
        "security_label": "internal", "valid_from": None, "valid_to": None,
        "source_event_id": "evt-1",
    }
    return dict(base), {**base, **overrides}


def test_identical_projections_match():
    legacy, shadow = _pair()
    assert compare(event_id="evt-1", legacy=legacy, shadow=shadow).status == \
        ComparisonStatus.MATCH.value


def test_whitespace_difference_is_a_normalised_match():
    legacy, shadow = _pair(content="same    text")
    result = compare(event_id="evt-1", legacy=legacy, shadow=shadow)
    assert result.status == ComparisonStatus.MATCH_NORMALIZED.value
    assert result.detail, "the normalisation applied must be stated"


def test_real_content_difference_is_not_normalised_away():
    legacy, shadow = _pair(content="completely different")
    assert compare(event_id="evt-1", legacy=legacy, shadow=shadow).status == \
        ComparisonStatus.CONTENT_MISMATCH.value


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"tenant_id": "other"}, ComparisonStatus.TENANT_MISMATCH.value),
        ({"workspace_id": "ws-9"}, ComparisonStatus.WORKSPACE_MISMATCH.value),
        ({"memory_type": "semantic"}, ComparisonStatus.TYPE_MISMATCH.value),
        ({"epistemic_status": "inferred"}, ComparisonStatus.EPISTEMIC_MISMATCH.value),
        ({"security_label": "restricted"}, ComparisonStatus.SECURITY_MISMATCH.value),
        ({"valid_from": "2026-01-01"}, ComparisonStatus.TEMPORAL_MISMATCH.value),
        ({"confidence": 0.9}, ComparisonStatus.CONFIDENCE_MISMATCH.value),
        ({"source_event_id": "evt-other"}, ComparisonStatus.PROVENANCE_MISMATCH.value),
    ],
)
def test_each_mismatch_class_is_detected(override, expected):
    legacy, shadow = _pair(**override)
    assert compare(event_id="evt-1", legacy=legacy, shadow=shadow).status == expected


def test_missing_sides_are_distinguished():
    legacy, _ = _pair()
    assert compare(event_id="e", legacy=legacy, shadow=None).status == \
        ComparisonStatus.MISSING_SHADOW.value
    assert compare(event_id="e", legacy=None, shadow=legacy).status == \
        ComparisonStatus.MISSING_LEGACY.value


def test_normalise_text_rules():
    assert normalise_text("  a   b  ") == "a b"
    assert normalise_text("a\nb") == "a b"


# ==========================================================================
# 6. Recovery and concurrency
# ==========================================================================


def test_replaying_the_same_event_does_not_duplicate_the_shadow(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    for _ in range(10):
        os_.events.conn.execute(
            "UPDATE projection_outbox SET status=?, locked_at=NULL, available_at=? "
            "WHERE event_id=?",
            (JobStatus.PENDING.value, time.time() - 1, event.event_id),
        )
        os_.events.conn.commit()
        shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    assert os_.shadow_memories.count() == 1


def test_a_shadow_builder_failure_does_not_break_observe(db):
    """The legacy caller must succeed even when the shadow path cannot."""
    os_ = MemoryOS(db, projection_mode="shadow")

    class _Broken:
        def build(self, event, job, conn):
            raise RuntimeError("shadow builder down")

    from bio_agent_os.cognitive.reconciliation_worker import ReconciliationWorker

    event = _observe_and_remember(os_, content="still works")
    assert legacy_projection(os_, event.event_id) is not None

    w = ReconciliationWorker(
        os_.events.conn, projection_conn=os_.memories.conn, outbox=os_.events.outbox,
        builders={COGNITIVE_MEMORY: _Broken()}, worker_id="w", lease_seconds=0,
    )
    w.run_once()
    assert w.metrics.failed == 1
    assert os_.shadow_memories.count() == 0
    assert legacy_projection(os_, event.event_id) is not None, "legacy is untouched"


def test_shadow_survives_a_worker_crash_and_still_compares(db):
    """PROCESS-LEVEL: kill after the shadow commit, recover, then compare."""
    import fault_children

    os_ = MemoryOS(db, projection_mode="shadow")
    event = _observe_and_remember(os_)
    os_.events.conn.close()
    os_.memories.conn.close()

    reached = CTX.Event()
    p = CTX.Process(
        target=_crash_shadow_child, args=(db, FP.AFTER_PROJECTION_COMMIT.value, reached)
    )
    p.start()
    assert reached.wait(60), "child never reached the fault point"
    p.join(30)
    assert not p.is_alive()

    live = MemoryOS(db, projection_mode="shadow")
    assert live.shadow_memories.count() == 1
    shadow_worker(live, worker_id="recover", lease_seconds=0).run_once()

    assert live.shadow_memories.count() == 1, "recovery must not duplicate the shadow"
    result = compare_event(live, event.event_id)
    assert result.ok, f"comparison after recovery: {result.status}"


def test_two_workers_share_the_shadow_queue_without_duplicating(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    for i in range(5):
        _observe_and_remember(os_, content=f"item {i}")
    a = shadow_worker(os_, worker_id="a", lease_seconds=300)
    b = shadow_worker(os_, worker_id="b", lease_seconds=300)
    a.run_once(batch_size=3)
    b.run_once(batch_size=3)
    assert a.metrics.completed + b.metrics.completed == 5
    assert os_.shadow_memories.count() == 5


def test_report_survives_a_reopen(db, tmp_path):
    os_ = MemoryOS(db, projection_mode="shadow")
    events = [_observe_and_remember(os_, content=f"row {i}") for i in range(5)]
    report_obj = run_shadow_comparison(os_, [e.event_id for e in events])
    json_path, md_path = write_report(report_obj, tmp_path / "reports")

    assert json_path.exists() and md_path.exists()
    os_.memories.conn.close()
    os_.events.conn.close()

    reopened = MemoryOS(db, projection_mode="shadow")
    assert reopened.shadow_memories.count() == 5
    statuses = {
        r["comparison_status"]
        for r in reopened.memories.conn.execute(
            "SELECT comparison_status FROM shadow_memories"
        )
    }
    assert statuses <= {ComparisonStatus.MATCH.value, ComparisonStatus.MATCH_NORMALIZED.value}


# ==========================================================================
# 7. End to end
# ==========================================================================


def test_shared_memory_runtime_supports_shadow():
    os_ = MemoryOS(":memory:", projection_mode="shadow")
    event = _observe_and_remember(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    assert os_.shadow_memories.count() == 1
    assert compare_event(os_, event.event_id).ok


def test_a_clean_run_reports_clean(db):
    os_ = MemoryOS(db, projection_mode="shadow")
    events = [_observe_and_remember(os_, content=f"content {i}", tenant=f"t{i % 3}")
              for i in range(30)]
    result = run_shadow_comparison(os_, [e.event_id for e in events])

    assert result.observations == 30
    assert result.shadow_projections == 30
    assert result.matches == len(result.comparisons)
    assert result.clean
    assert set(result.unsupported) == set(unsupported_types())
    assert "UNSUPPORTED (not tested, not passed)" in result.render()


def _crash_shadow_child(db_path: str, point: str, reached) -> None:
    """Module-level for spawn: build a shadow projection, then die."""
    from bio_agent_os.cognitive import fault_points as f
    from bio_agent_os.cognitive.facade import MemoryOS as _OS
    from bio_agent_os.cognitive.shadow_runner import shadow_worker as _sw

    def hook(fired: str) -> None:
        if fired == point:
            reached.set()
            os._exit(137)

    f.install(hook)
    runtime = _OS(db_path, projection_mode="shadow")
    _sw(runtime, worker_id=f"crash-{os.getpid()}", lease_seconds=0).run_once()
    reached.set()
