"""Incremental scanning, and the ways a cursor can lie.

A cursor that is wrong is worse than no cursor: it makes the doctor skip data
and report clean. Every test here is about one way that could happen — a crash
mid-scan, a schema change, a new check, a repair that stepped over a problem,
or rows appended while the scan was running.
"""

from __future__ import annotations

import time

import pytest

from bio_agent_os.cognitive.diagnostics import DeepDoctor, Severity
from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor
from bio_agent_os.cognitive.doctor_cursor import (
    DOCTOR_VERSION,
    CursorStore,
    schema_fingerprint,
)
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord, MemoryType
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.reconciliation_worker import worker_for

MEMORY = ProjectionType.COGNITIVE_MEMORY.value


@pytest.fixture()
def os_(tmp_path) -> MemoryOS:
    return MemoryOS(tmp_path / "incremental.db", projection_mode="shadow")


def _observe(os_: MemoryOS, n: int = 5, start: int = 0, tenant: str = "t1") -> list[str]:
    ids = []
    for i in range(start, start + n):
        event = os_.observe(tenant_id=tenant, actor="a", source="unit",
                            content=f"observation {i}")
        os_.remember(event=event, memory_type=MemoryType.EPISODIC,
                     content=f"observation {i}")
        ids.append(event.event_id)
    return ids


def _drain(os_: MemoryOS) -> None:
    worker = worker_for(os_, worker_id="w", manage_wal=False)
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=100)
        if worker.metrics.claimed == before:
            break


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


# -- the window --------------------------------------------------------------

def test_first_run_scans_everything(os_):
    _observe(os_, 5)
    doctor = IncrementalDoctor(os_.events.conn)
    report = doctor.run_incremental()
    assert report.counts["window_events"] == 5
    assert doctor.cursor.reason.startswith("no cursor stored")


def test_second_run_scans_only_what_arrived_since(os_):
    _observe(os_, 5)
    IncrementalDoctor(os_.events.conn).run_incremental()

    _observe(os_, 3, start=5)
    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert report.counts["window_events"] == 3


def test_a_run_with_nothing_new_scans_an_empty_window(os_):
    _observe(os_, 4)
    IncrementalDoctor(os_.events.conn).run_incremental()
    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert report.counts["window_events"] == 0
    assert report.exit_code == 0


def test_since_event_ignores_the_cursor(os_):
    ids = _observe(os_, 6)
    IncrementalDoctor(os_.events.conn).run_incremental()
    report = IncrementalDoctor(os_.events.conn).run_incremental(
        since_event_id=ids[2], advance=False
    )
    assert report.counts["window_events"] == 3


def test_since_event_rejects_an_unknown_id(os_):
    _observe(os_, 2)
    report = IncrementalDoctor(os_.events.conn).run_incremental(
        since_event_id="no-such-event"
    )
    assert report.scan_error and "no-such-event" in report.scan_error
    assert report.exit_code == 3


def test_full_forces_a_complete_scan(os_):
    _observe(os_, 5)
    IncrementalDoctor(os_.events.conn).run_incremental()
    report = IncrementalDoctor(os_.events.conn).run_incremental(full=True, advance=False)
    assert report.counts["window_events"] == 5


def test_no_advance_leaves_the_cursor_alone(os_):
    _observe(os_, 4)
    IncrementalDoctor(os_.events.conn).run_incremental(advance=False)
    store = CursorStore(os_.events.conn)
    assert store.load("").last_event_rowid == 0


# -- cheap invariants still run in full -------------------------------------

def test_global_checks_run_even_with_an_empty_window(os_):
    """A stale lease planted long before the cursor must still be found."""
    _observe(os_, 3)
    _drain(os_)
    IncrementalDoctor(os_.events.conn).run_incremental()

    os_.events.conn.execute(
        "UPDATE projection_outbox SET status='in_progress', locked_by='ghost',"
        " locked_at=? WHERE rowid=(SELECT MIN(rowid) FROM projection_outbox)",
        (time.time() - 100_000,),
    )
    os_.events.conn.commit()

    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert report.counts["window_events"] == 0
    assert "JOB_STALE_LEASE" in _codes(report), (
        "a global invariant was skipped because the window was empty"
    )


def test_schema_and_capability_checks_always_run(os_):
    _observe(os_, 2)
    report = IncrementalDoctor(os_.events.conn).run_incremental()
    codes = _codes(report)
    assert "CAPABILITY_SUMMARY" in codes
    assert "SQLITE_INTEGRITY" in codes


def test_incremental_uses_quick_check_not_the_full_integrity_scan(os_):
    """integrity_check reads every page and every index entry; that belongs to
    an audit, not to something run every few minutes."""
    _observe(os_, 2)
    report = IncrementalDoctor(os_.events.conn).run_incremental()
    integrity = next(f for f in report.findings if f.code == "SQLITE_INTEGRITY")
    assert integrity.evidence["pragma"] == "quick_check"

    full = DeepDoctor(os_.events.conn).run(deep=True)
    integrity = next(f for f in full.findings if f.code == "SQLITE_INTEGRITY")
    assert integrity.evidence["pragma"] == "integrity_check"


# -- invalidation ------------------------------------------------------------

def test_a_doctor_version_change_invalidates_the_cursor(os_, monkeypatch):
    _observe(os_, 4)
    IncrementalDoctor(os_.events.conn).run_incremental()

    store = CursorStore(os_.events.conn)
    os_.events.conn.execute("UPDATE doctor_cursor SET doctor_version=?",
                            (DOCTOR_VERSION - 1,))
    os_.events.conn.commit()

    cursor = store.load("")
    assert cursor.last_event_rowid == 0
    assert "doctor version changed" in cursor.reason


def test_a_schema_change_invalidates_the_cursor(os_):
    _observe(os_, 4)
    IncrementalDoctor(os_.events.conn).run_incremental()

    os_.events.conn.execute(
        "UPDATE doctor_cursor SET schema_fingerprint='something-else'"
    )
    os_.events.conn.commit()

    cursor = CursorStore(os_.events.conn).load("")
    assert cursor.last_event_rowid == 0
    assert "schema fingerprint changed" in cursor.reason


def test_the_fingerprint_actually_changes_when_the_schema_does(os_):
    before = schema_fingerprint(os_.events.conn)
    os_.events.conn.execute("CREATE INDEX idx_probe ON cognitive_events(actor)")
    os_.events.conn.commit()
    assert schema_fingerprint(os_.events.conn) != before


def test_reset_forces_the_next_scan_to_be_full(os_):
    _observe(os_, 5)
    IncrementalDoctor(os_.events.conn).run_incremental()
    assert CursorStore(os_.events.conn).reset() == 1
    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert report.counts["window_events"] == 5


# -- never skipping ----------------------------------------------------------

def test_an_unfinished_scan_does_not_move_the_cursor(os_, monkeypatch):
    """A crash mid-scan re-covers the same ground next time. Re-checking is
    cheap; missing a row is not."""
    _observe(os_, 5)

    doctor = IncrementalDoctor(os_.events.conn)

    def _explode() -> None:
        raise RuntimeError("scan died halfway")

    monkeypatch.setattr(doctor, "check_ledger_consistency", _explode)
    report = doctor.run_incremental()
    assert report.scan_error and "scan died halfway" in report.scan_error
    assert CursorStore(os_.events.conn).load("").last_event_rowid == 0

    recovered = IncrementalDoctor(os_.events.conn).run_incremental()
    assert recovered.counts["window_events"] == 5


def test_an_unresolved_finding_holds_the_cursor(os_):
    """The failure mode this exists for: a scan that steps over a problem
    hides it permanently."""
    _observe(os_, 3)
    _drain(os_)
    os_.memories.conn.execute("DELETE FROM cognitive_memories")
    os_.memories.conn.commit()

    first = IncrementalDoctor(os_.events.conn).run_incremental()
    assert "COMPLETED_WITHOUT_PROJECTION" in _codes(first)

    stored = CursorStore(os_.events.conn).load("")
    assert stored.blocked_by_findings
    assert "COMPLETED_WITHOUT_PROJECTION" in (stored.blocked_reason or "")

    second = IncrementalDoctor(os_.events.conn).run_incremental()
    assert "COMPLETED_WITHOUT_PROJECTION" in _codes(second), (
        "the problem stopped being reported without being fixed"
    )


def test_the_cursor_advances_once_the_problem_is_gone(os_):
    _observe(os_, 3)
    _drain(os_)
    os_.memories.conn.execute("DELETE FROM cognitive_memories")
    os_.memories.conn.commit()
    IncrementalDoctor(os_.events.conn).run_incremental()
    assert CursorStore(os_.events.conn).load("").blocked_by_findings

    # Rebuild what was deleted: reset the jobs and let the worker run.
    os_.events.conn.execute(
        "UPDATE projection_outbox SET status='pending', attempts=0,"
        " locked_by=NULL, locked_at=NULL"
    )
    os_.events.conn.commit()
    os_.memories.conn.execute("DELETE FROM projection_ledger")
    os_.memories.conn.commit()
    _drain(os_)

    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert "COMPLETED_WITHOUT_PROJECTION" not in _codes(report)
    stored = CursorStore(os_.events.conn).load("")
    assert not stored.blocked_by_findings
    assert stored.last_event_rowid > 0


def test_rows_appended_during_a_scan_are_not_marked_as_covered(os_):
    """The high-water mark is read before the scan, so anything appended while
    it runs falls into the next window rather than being skipped."""
    _observe(os_, 3)
    doctor = IncrementalDoctor(os_.events.conn)

    original = doctor.check_shadow

    def _append_midway() -> None:
        _observe(os_, 2, start=100)
        original()

    doctor.check_shadow = _append_midway
    doctor.run_incremental()

    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert report.counts["window_events"] == 2, (
        "events appended during the previous scan were treated as covered"
    )


# -- agreement with the full scan -------------------------------------------

def test_incremental_and_full_agree_on_a_healthy_database(os_):
    _observe(os_, 10)
    _drain(os_)
    incremental = IncrementalDoctor(os_.events.conn).run_incremental(full=True)
    full = DeepDoctor(os_.events.conn).run(deep=True)
    assert incremental.exit_code == full.exit_code
    # quick_check vs integrity_check is the one deliberate difference.
    assert _codes(incremental) == _codes(full)


def test_incremental_finds_damage_in_its_own_window(os_):
    _observe(os_, 4)
    _drain(os_)
    IncrementalDoctor(os_.events.conn).run_incremental()

    ids = _observe(os_, 2, start=50)
    _drain(os_)
    os_.memories.conn.execute(
        "DELETE FROM cognitive_memories WHERE memory_id IN ("
        " SELECT memory_id FROM memory_source_events WHERE event_id=?)", (ids[0],)
    )
    os_.memories.conn.commit()

    report = IncrementalDoctor(os_.events.conn).run_incremental()
    assert "COMPLETED_WITHOUT_PROJECTION" in _codes(report)


def test_tenant_scoped_cursors_are_independent(os_):
    _observe(os_, 3, tenant="tenant-a")
    _observe(os_, 3, tenant="tenant-b")

    a = IncrementalDoctor(os_.events.conn, tenant_id="tenant-a").run_incremental()
    assert a.counts["window_events"] == 3

    b = IncrementalDoctor(os_.events.conn, tenant_id="tenant-b").run_incremental()
    assert b.counts["window_events"] == 3

    again = IncrementalDoctor(os_.events.conn, tenant_id="tenant-a").run_incremental()
    assert again.counts["window_events"] == 0
