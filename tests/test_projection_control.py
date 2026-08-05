"""Pause, resume, drain — and the guarantee that legacy is still the default.

A feature flag turns the projection path off for new work. It does not tell a
running worker to stop, and it does not say when the queue has finished. Both
are needed during a canary, and a rollback that abandons an in-flight
projection is not a rollback, it is a crash somebody chose.
"""

from __future__ import annotations

import os

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.projection_control import ProjectionControl, drain
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.shadow import ProjectionMode, current_mode

MEMORY = ProjectionType.COGNITIVE_MEMORY.value


@pytest.fixture()
def os_(tmp_path) -> MemoryOS:
    return MemoryOS(tmp_path / "control.db")


def _queue(os_: MemoryOS, n: int = 20, start: int = 0) -> None:
    for i in range(start, start + n):
        os_.events.append(
            EventRecord(tenant_id="t1", actor="a", source="unit",
                        payload={"content": f"job {i}"}, event_id=f"evt-{i}"),
            projection_types=(MEMORY,),
        )


# -- pause -------------------------------------------------------------------

def test_a_fresh_database_is_not_paused(os_):
    assert ProjectionControl(os_.events.conn).is_paused() is False


def test_pause_stops_the_worker_claiming(os_):
    _queue(os_, 10)
    control = ProjectionControl(os_.events.conn)
    control.pause(reason="canary rollback", operator="tuan-anh")

    worker = worker_for(os_, worker_id="paused", manage_wal=False)
    worker.run_once(batch_size=10)

    assert worker.metrics.claimed == 0
    assert worker.metrics.paused_cycles == 1
    assert os_.events.outbox.counts()["pending"] == 10


def test_resume_starts_it_again(os_):
    _queue(os_, 10)
    control = ProjectionControl(os_.events.conn)
    control.pause(reason="pausing")
    worker = worker_for(os_, worker_id="w", manage_wal=False)
    worker.run_once(batch_size=10)
    assert worker.metrics.completed == 0

    control.resume(reason="carrying on")
    worker.run_once(batch_size=10)
    assert worker.metrics.completed == 10


def test_pause_records_who_and_why(os_):
    control = ProjectionControl(os_.events.conn)
    state = control.pause(reason="rolling back to legacy", operator="oncall")
    assert state.paused and state.reason == "rolling back to legacy"
    assert state.operator == "oncall" and state.changed_at


def test_a_tenant_pause_does_not_stop_other_tenants(os_):
    control = ProjectionControl(os_.events.conn)
    control.pause(scope="tenant-a", reason="canary only")
    assert control.is_paused("tenant-a") is True
    assert control.is_paused("tenant-b") is False
    assert control.is_paused() is False


def test_a_global_pause_covers_every_tenant(os_):
    control = ProjectionControl(os_.events.conn)
    control.pause(reason="everything")
    assert control.is_paused("tenant-a") is True
    assert control.is_paused() is True


def test_the_pause_is_read_before_a_claim_never_during_one(os_):
    """A claimed batch always finishes. The flag is read once at the top of a
    cycle, so a projection that has started is never abandoned — an
    interrupted transaction is the crash case, and there is no reason to
    create one deliberately.
    """
    _queue(os_, 8)
    control = ProjectionControl(os_.events.conn)
    worker = worker_for(os_, worker_id="mid-flight", manage_wal=False)

    checks: list[int] = []
    original_paused = worker.paused

    def _record() -> bool:
        checks.append(worker.metrics.claimed)
        return original_paused()

    worker.paused = _record

    worker.run_once(batch_size=4)
    assert worker.metrics.completed == 4
    control.pause(reason="paused between cycles, as an operator would")
    worker.run_once(batch_size=4)

    # Two cycles, two checks, each before anything was claimed in that cycle.
    assert len(checks) == 2
    assert worker.metrics.claimed == 4, "a paused cycle claimed work"
    assert worker.metrics.paused_cycles == 1
    # The four that were claimed before the pause are complete and consistent.
    assert os_.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger WHERE target_id IS NULL"
    ).fetchone()[0] == 0
    assert os_.events.outbox.counts()["pending"] == 4


def test_pausing_from_another_connection_waits_for_an_open_projection(os_):
    """Worth knowing, not worth preventing.

    The control flag lives on the queue connection and a projection holds the
    write lock on the target connection, so a pause issued while a projection
    is mid-transaction waits for that transaction — milliseconds in practice,
    bounded by busy_timeout in the worst case. It never interrupts the
    projection, which is the property that matters.
    """
    _queue(os_, 2)
    control = ProjectionControl(os_.events.conn)
    worker = worker_for(os_, worker_id="contended", manage_wal=False)
    worker.run_once(batch_size=2)
    assert worker.metrics.completed == 2

    control.pause(reason="after the transaction closed")
    assert control.is_paused() is True


def test_a_broken_control_table_never_stops_the_worker(os_):
    class _Broken:
        def is_paused(self, tenant_id=None):
            raise RuntimeError("control table unreadable")

    _queue(os_, 5)
    worker = worker_for(os_, worker_id="resilient", manage_wal=False, control=_Broken())
    worker.run_once(batch_size=5)
    assert worker.metrics.completed == 5


# -- drain -------------------------------------------------------------------

def test_drain_empties_the_queue(os_):
    _queue(os_, 30)
    result = drain(os_, timeout_seconds=60)
    assert result["drained"] is True
    assert result["remaining"] == 0
    assert result["queue_after"]["completed"] == 30


def test_drain_overrides_a_pause(os_):
    """A paused system that cannot be drained can only be waited out."""
    _queue(os_, 15)
    ProjectionControl(os_.events.conn).pause(reason="paused, but drain anyway")
    result = drain(os_, timeout_seconds=60)
    assert result["drained"] is True
    assert result["queue_after"]["pending"] == 0


def test_drain_reports_what_it_could_not_finish(os_):
    """A dead letter is a legitimate reason for a queue not to reach zero, and
    draining harder does not resolve it."""
    _queue(os_, 5)
    os_.events.conn.execute(
        "UPDATE projection_outbox SET status='dead_letter', last_error='deliberate'"
        " WHERE rowid=(SELECT MIN(rowid) FROM projection_outbox)"
    )
    os_.events.conn.commit()

    result = drain(os_, timeout_seconds=30)
    assert result["drained"] is True          # dead letters are terminal
    assert result["queue_after"]["dead_letter"] == 1
    assert result["queue_after"]["completed"] == 4


def test_drain_on_an_empty_queue_is_immediate(os_):
    result = drain(os_, timeout_seconds=30)
    assert result["drained"] is True
    assert result["seconds"] < 5


# -- legacy is still the default --------------------------------------------

def test_legacy_is_the_default_with_no_environment_variable(monkeypatch):
    """The single most important assertion in this file: doing nothing gets
    you the behaviour that shipped."""
    monkeypatch.delenv("BIO_AGENT_PROJECTION_MODE", raising=False)
    assert current_mode() is ProjectionMode.LEGACY


def test_observe_enqueues_nothing_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BIO_AGENT_PROJECTION_MODE", raising=False)
    runtime = MemoryOS(tmp_path / "default.db")
    try:
        assert runtime.projection_mode is ProjectionMode.LEGACY
        event = runtime.observe(tenant_id="t1", actor="a", source="unit",
                                content="legacy behaviour")
        assert runtime.events.outbox.by_event(event.event_id) == []
        assert runtime.events.outbox.counts()["pending"] == 0
    finally:
        runtime.close()


def test_an_unrecognised_mode_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_AGENT_PROJECTION_MODE", "something-invented")
    runtime = MemoryOS(tmp_path / "weird.db")
    try:
        assert runtime.projection_mode is ProjectionMode.LEGACY
        event = runtime.observe(tenant_id="t1", actor="a", source="unit", content="x")
        assert runtime.events.outbox.by_event(event.event_id) == []
    finally:
        runtime.close()


def test_shadow_mode_requires_being_asked_for(tmp_path, monkeypatch):
    monkeypatch.delenv("BIO_AGENT_PROJECTION_MODE", raising=False)
    runtime = MemoryOS(tmp_path / "explicit.db", projection_mode="shadow")
    try:
        event = runtime.observe(tenant_id="t1", actor="a", source="unit", content="x")
        assert len(runtime.events.outbox.by_event(event.event_id)) == 1
    finally:
        runtime.close()


def test_rollback_to_legacy_keeps_committed_debt(tmp_path, monkeypatch):
    """Rolling back must not delete what was already owed: the debt waits for
    a replay rather than disappearing."""
    monkeypatch.setenv("BIO_AGENT_PROJECTION_MODE", "shadow")
    runtime = MemoryOS(tmp_path / "rollback.db", projection_mode="shadow")
    event = runtime.observe(tenant_id="t1", actor="a", source="unit", content="owed")
    assert len(runtime.events.outbox.by_event(event.event_id)) == 1
    runtime.close()

    monkeypatch.setenv("BIO_AGENT_PROJECTION_MODE", "legacy")
    after = MemoryOS(tmp_path / "rollback.db")
    try:
        assert after.projection_mode is ProjectionMode.LEGACY
        assert len(after.events.outbox.by_event(event.event_id)) == 1, (
            "rolling back deleted a committed projection debt"
        )
        assert after.events.conn.execute(
            "SELECT COUNT(*) FROM cognitive_events"
        ).fetchone()[0] == 1
        # And a new observation owes nothing, because legacy is legacy.
        fresh = after.observe(tenant_id="t1", actor="a", source="unit", content="after")
        assert after.events.outbox.by_event(fresh.event_id) == []
    finally:
        after.close()
