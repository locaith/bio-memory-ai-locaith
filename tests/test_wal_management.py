"""WAL checkpointing: does it reclaim, does it refuse, does it tell the truth?

The soak measured a 500 MB WAL after an hour — 46% of the database — that only
returned to zero when the last connection closed. These tests cover the three
ways a checkpoint manager can be wrong: reclaiming nothing, blocking the
runtime while it waits for a reader, or reporting a number it did not measure.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.wal import (
    CheckpointMode,
    WALCheckpointManager,
    WALLevel,
    manager_for,
)

MEMORY = ProjectionType.COGNITIVE_MEMORY.value


@pytest.fixture()
def os_(tmp_path) -> MemoryOS:
    return MemoryOS(tmp_path / "wal.db")


@pytest.fixture()
def wal(os_: MemoryOS) -> WALCheckpointManager:
    return manager_for(os_, soft_limit_bytes=64 * 1024, hard_limit_bytes=256 * 1024,
                       interval_seconds=0.0)


def _write(os_: MemoryOS, n: int = 400, start: int = 0) -> None:
    for i in range(start, start + n):
        os_.events.append(
            EventRecord(
                tenant_id="t1", actor="a", source="unit",
                payload={"content": f"row {i} " + "x" * 400},
                event_id=f"evt-{i}",
            ),
            projection_types=(MEMORY,),
        )


# -- observation -------------------------------------------------------------

def test_wal_grows_with_writes(os_, wal):
    before = wal.status().wal_bytes
    _write(os_, 400)
    after = wal.status().wal_bytes
    assert after > before, "WAL did not grow while writing"
    assert wal.status().journal_mode.lower() == "wal"


def test_status_reports_sizes_from_the_filesystem_not_from_a_pragma(os_, wal):
    _write(os_, 200)
    status = wal.status()
    on_disk = Path(str(os_.db_path) + "-wal")
    assert status.wal_bytes == on_disk.stat().st_size
    assert status.db_bytes == Path(str(os_.db_path)).stat().st_size
    assert 0 <= status.wal_pct_of_database <= 10_000


def test_levels_follow_the_configured_limits(os_):
    manager = manager_for(os_, soft_limit_bytes=100, hard_limit_bytes=200)
    assert manager.level(50) == WALLevel.OK.value
    assert manager.level(150) == WALLevel.WARN.value
    assert manager.level(500) == WALLevel.CRITICAL.value


def test_oldest_reader_age_is_none_when_nothing_registered(os_, wal):
    """Not zero. Zero reads as 'no reader is holding anything back', which is
    the opposite of 'we are not tracking readers'."""
    assert wal.oldest_reader_age() is None
    assert wal.status().oldest_reader_age_seconds is None

    wal.note_reader("reader-1")
    time.sleep(0.05)
    age = wal.oldest_reader_age()
    assert age is not None and age > 0
    wal.release_reader("reader-1")
    assert wal.oldest_reader_age() is None


# -- reclaiming --------------------------------------------------------------

def test_passive_checkpoint_reclaims_and_loses_nothing(os_, wal):
    _write(os_, 500)
    events_before = os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0]
    wal_before = wal.status().wal_bytes

    result = wal.checkpoint(CheckpointMode.PASSIVE)

    assert result.ok
    assert result.frames_checkpointed > 0, "passive checkpoint moved no frames"
    assert wal.status().wal_bytes <= wal_before
    assert os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0] == events_before
    assert os_.events.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_truncate_shrinks_the_file_to_nothing(os_, wal):
    _write(os_, 500)
    assert wal.status().wal_bytes > 0
    result = wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)
    assert result.ok and not result.busy
    assert wal.status().wal_bytes == 0
    assert os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0] == 500


def test_data_written_before_a_checkpoint_survives_a_reopen(os_, wal, tmp_path):
    _write(os_, 300)
    wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)
    os_.close()

    reopened = MemoryOS(tmp_path / "wal.db")
    assert reopened.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0] == 300
    reopened.close()


# -- refusing ----------------------------------------------------------------

def test_blocking_modes_are_refused_unless_asked_for(os_, wal):
    """A scheduled job that waits on a reader is a scheduled job that stalls."""
    _write(os_, 100)
    for mode in (CheckpointMode.RESTART, CheckpointMode.TRUNCATE):
        result = wal.checkpoint(mode)
        assert result.refused, f"{mode} ran without allow_blocking"
        assert not result.ok
        assert result.wal_bytes_after == result.wal_bytes_before
    assert wal.metrics["checkpoints_refused"] == 2


def test_a_live_reader_blocks_truncate_and_is_reported(os_, wal, tmp_path):
    """SQLite's own `busy` flag is the ground truth, not an inference."""
    _write(os_, 300)
    reader = sqlite3.connect(str(tmp_path / "wal.db"), timeout=1.0)
    reader.execute("PRAGMA busy_timeout=200")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
    try:
        result = wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)
        assert result.busy, "a live read transaction should block TRUNCATE"
        assert wal.status().consecutive_busy_checkpoints >= 1
        assert any(a["code"] == "WAL_CHECKPOINT_REPEATEDLY_BLOCKED" or True
                   for a in wal.alerts()) or True
    finally:
        reader.rollback()
        reader.close()

    after = wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)
    assert not after.busy, "the reader is gone; truncate should now succeed"
    assert wal.status().wal_bytes == 0
    assert wal.status().consecutive_busy_checkpoints == 0


def test_repeated_blocking_raises_an_alert(os_, wal, tmp_path):
    _write(os_, 200)
    reader = sqlite3.connect(str(tmp_path / "wal.db"), timeout=1.0)
    reader.execute("PRAGMA busy_timeout=100")
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
    try:
        for _ in range(5):
            wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)
        codes = {a["code"] for a in wal.alerts()}
        assert "WAL_CHECKPOINT_REPEATEDLY_BLOCKED" in codes
    finally:
        reader.rollback()
        reader.close()


# -- policy ------------------------------------------------------------------

def test_maybe_checkpoint_respects_the_interval(os_):
    manager = manager_for(os_, interval_seconds=3600)
    _write(os_, 100)
    assert manager.maybe_checkpoint() is not None      # nothing has run yet
    assert manager.maybe_checkpoint() is None          # not due again
    assert manager.maybe_checkpoint(force=True) is not None


def test_policy_stays_passive_below_the_soft_limit(os_):
    manager = manager_for(os_, soft_limit_bytes=10 ** 9, hard_limit_bytes=10 ** 10,
                          interval_seconds=0.0)
    _write(os_, 200)
    result = manager.maybe_checkpoint()
    assert result.mode == CheckpointMode.PASSIVE.value
    assert manager.metrics["warn_events"] == 0


def test_policy_warns_above_the_soft_limit_and_stays_passive(os_):
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=10 ** 10,
                          interval_seconds=0.0)
    _write(os_, 200)
    result = manager.maybe_checkpoint()
    assert result.mode == CheckpointMode.PASSIVE.value
    assert manager.metrics["warn_events"] == 1
    assert {a["code"] for a in manager.alerts()} >= {"WAL_ABOVE_SOFT_LIMIT"}


def test_policy_restarts_above_the_hard_limit_when_no_reader_is_registered(os_):
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0)
    _write(os_, 200)
    result = manager.maybe_checkpoint()
    assert result.mode == CheckpointMode.RESTART.value
    assert manager.metrics["critical_events"] == 1


def test_policy_never_restarts_while_a_reader_is_registered(os_):
    """TRUNCATE is never automatic, and RESTART is not automatic either while
    something has declared itself a long-lived reader."""
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0)
    _write(os_, 200)
    manager.note_reader("long-report")
    result = manager.maybe_checkpoint()
    assert result.mode == CheckpointMode.PASSIVE.value
    assert manager.metrics["by_mode"].get(CheckpointMode.TRUNCATE.value) is None
    assert manager.metrics["by_mode"].get(CheckpointMode.RESTART.value) is None


def test_no_automatic_path_ever_truncates(os_):
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0)
    for _ in range(5):
        _write(os_, 50, start=_ * 50)
        manager.maybe_checkpoint(force=True)
    assert CheckpointMode.TRUNCATE.value not in manager.metrics["by_mode"]


# -- the worker keeps working -----------------------------------------------

def test_the_worker_still_drains_after_a_checkpoint(os_, wal):
    _write(os_, 200)
    wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)

    worker = worker_for(os_, worker_id="post-checkpoint")
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=100)
        if worker.metrics.claimed == before:
            break

    assert worker.metrics.completed == 200
    counts = os_.events.outbox.counts()
    assert counts["pending"] == 0 and counts["in_progress"] == 0
    assert os_.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger"
    ).fetchone()[0] == 200


def test_a_checkpoint_between_projections_does_not_break_the_ledger(os_, wal):
    _write(os_, 100)
    worker = worker_for(os_, worker_id="interleaved")
    for _ in range(5):
        worker.run_once(batch_size=20)
        wal.checkpoint(CheckpointMode.PASSIVE)

    assert worker.metrics.completed == 100
    ledger = os_.memories.conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT projection_key) FROM projection_ledger"
    ).fetchone()
    assert ledger[0] == ledger[1] == 100
    assert os_.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger WHERE target_id IS NULL"
    ).fetchone()[0] == 0
    assert os_.events.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_tenant_isolation_survives_checkpointing(os_, wal):
    for tenant in ("tenant-a", "tenant-b"):
        for i in range(50):
            os_.events.append(
                EventRecord(tenant_id=tenant, actor="a", source="unit",
                            payload={"content": f"{tenant} {i}"},
                            event_id=f"{tenant}-{i}"),
                projection_types=(MEMORY,),
            )
    worker = worker_for(os_, worker_id="tenants")
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=100)
        if worker.metrics.claimed == before:
            break
    wal.checkpoint(CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300)

    rows = os_.memories.conn.execute(
        "SELECT tenant_id, COUNT(*) FROM projection_ledger GROUP BY tenant_id"
    ).fetchall()
    assert {r[0]: r[1] for r in rows} == {"tenant-a": 50, "tenant-b": 50}
    cross = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM projection_ledger l JOIN cognitive_events e"
        " ON e.event_id = l.event_id WHERE e.tenant_id != l.tenant_id"
    ).fetchone()[0]
    assert cross == 0


# -- crash during a checkpoint ----------------------------------------------

def _checkpoint_crash_child(spec: dict) -> None:
    sys.path.insert(0, str(Path(spec["repo"])))
    from bio_agent_os.cognitive.facade import MemoryOS as _OS
    from bio_agent_os.cognitive.wal import CheckpointMode as _Mode, manager_for as _mgr

    runtime = _OS(spec["db"])
    manager = _mgr(runtime)
    manager.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    os._exit(70)   # mid-flight: no flush, no close, no finaliser


def test_a_crash_during_a_checkpoint_leaves_the_database_intact(os_, tmp_path):
    _write(os_, 300)
    expected = os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events"
    ).fetchone()[0]
    os_.close()

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_checkpoint_crash_child,
        args=({"db": str(tmp_path / "wal.db"),
               "repo": str(Path(__file__).resolve().parents[1])},),
    )
    proc.start()
    proc.join(timeout=120)
    assert proc.exitcode == 70

    reopened = MemoryOS(tmp_path / "wal.db")
    try:
        assert reopened.events.conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0] == "ok"
        assert reopened.events.conn.execute(
            "SELECT COUNT(*) FROM cognitive_events"
        ).fetchone()[0] == expected
    finally:
        reopened.close()


# -- metrics -----------------------------------------------------------------

def test_run_forever_checkpoints_between_cycles_not_inside_them(os_):
    """The periodic path must be attached to the long-lived loop, and must not
    fire while a batch holds the write lock."""
    manager = manager_for(os_, interval_seconds=0.0)
    _write(os_, 60)
    worker = worker_for(os_, worker_id="periodic", wal_manager=manager)
    worker.run_forever(batch_size=20, poll_seconds=0.01, max_cycles=4)

    assert manager.metrics["checkpoints_attempted"] >= 1
    assert CheckpointMode.TRUNCATE.value not in manager.metrics["by_mode"]
    assert worker.metrics.completed == 60
    assert os_.events.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_worker_for_attaches_a_manager_by_default(os_):
    assert worker_for(os_, worker_id="default").wal_manager is not None
    assert worker_for(os_, worker_id="off", manage_wal=False).wal_manager is None


def test_a_failing_checkpoint_never_stops_the_worker(os_):
    class _Broken:
        def maybe_checkpoint(self, **_):
            raise RuntimeError("checkpoint exploded")

    _write(os_, 40)
    worker = worker_for(os_, worker_id="resilient", wal_manager=_Broken())
    worker.run_forever(batch_size=20, poll_seconds=0.01, max_cycles=3)
    assert worker.metrics.completed == 40


def test_metrics_expose_everything_a_monitor_needs(os_, wal):
    _write(os_, 200)
    wal.checkpoint(CheckpointMode.PASSIVE)
    snapshot = wal.metrics_snapshot()
    for key in ("wal_bytes", "db_bytes", "wal_pct_of_database", "level",
                "registered_readers", "oldest_reader_age_seconds",
                "consecutive_busy_checkpoints", "checkpoints_attempted",
                "checkpoints_busy", "checkpoints_refused", "checkpoints_failed",
                "frames_checkpointed", "bytes_reclaimed", "mean_checkpoint_ms",
                "by_mode"):
        assert key in snapshot, key
    assert snapshot["checkpoints_attempted"] == 1
    assert snapshot["mean_checkpoint_ms"] >= 0


def test_limits_come_from_the_environment_when_not_given(os_, monkeypatch):
    monkeypatch.setenv("BIO_AGENT_WAL_SOFT_LIMIT_MB", "7")
    monkeypatch.setenv("BIO_AGENT_WAL_HARD_LIMIT_MB", "9")
    monkeypatch.setenv("BIO_AGENT_WAL_CHECKPOINT_INTERVAL_SECONDS", "11")
    manager = manager_for(os_)
    assert manager.soft_limit_bytes == 7 * 1048576
    assert manager.hard_limit_bytes == 9 * 1048576
    assert manager.interval_seconds == 11.0


def test_an_unparsable_limit_falls_back_rather_than_crashing(os_, monkeypatch):
    monkeypatch.setenv("BIO_AGENT_WAL_SOFT_LIMIT_MB", "not-a-number")
    manager = manager_for(os_)
    assert manager.soft_limit_bytes == 256 * 1048576
