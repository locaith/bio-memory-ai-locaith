"""The WAL state machine, written against what Run 7 measured.

Run 7 died at 6.48 hours of a 24-hour canary on `wal_above_hard_limit`. The
shape of the failure, from its 382 samples against a 64 MB soft limit:

    median WAL          118 MB
    samples above soft  81%
    final excursion     220 MB -> 612 MB in 42 seconds

The mechanism: PASSIVE copies frames back to the database but never returns the
file to the filesystem, so a log that only ever receives PASSIVE has no downward
force. The soft limit was not an alerting threshold that happened to be crossed
a lot — it was a threshold the manager's own action could not satisfy.

Every test here measures the **file on disk**, because the SQLite tuple is what
misled the earlier analysis: a checkpoint reporting `(0, 27033, 27033)` — every
frame copied, nobody blocking — sat on a 106 MB file. `frames_checkpointed`
answers "did frames move"; only `st_size` answers "did the disk come back".
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.wal import (
    SCHEDULED_TRUNCATE_BUDGET_MS,
    CheckpointMode,
    WALCheckpointManager,
    WALState,
    manager_for,
)

MEMORY = ProjectionType.COGNITIVE_MEMORY.value


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything section 2 of the Run 8 gate requires a WAL test to record."""

    wal_bytes_before: int
    wal_bytes_after: int
    mode: str
    busy: bool
    frames_checkpointed: int
    duration_ms: float
    reader_count: int
    oldest_reader_age: float | None
    state: str

    @property
    def reclaimed(self) -> int:
        return self.wal_bytes_before - self.wal_bytes_after

    def __str__(self) -> str:  # shows up in assertion output, so make it useful
        return (f"{self.mode} busy={self.busy} frames={self.frames_checkpointed} "
                f"{self.wal_bytes_before:,}B -> {self.wal_bytes_after:,}B "
                f"({self.reclaimed:+,}B) in {self.duration_ms:.1f}ms "
                f"readers={self.reader_count} state={self.state}")


def observe(manager: WALCheckpointManager, run) -> Observation:
    """Run one checkpoint through `run` and record the full picture."""
    before = manager.status()
    started = time.perf_counter()
    result = run()
    duration = (time.perf_counter() - started) * 1000
    after = manager.status()
    return Observation(
        wal_bytes_before=before.wal_bytes,
        wal_bytes_after=after.wal_bytes,
        mode=result.mode if result else "none",
        busy=bool(result and result.busy),
        frames_checkpointed=result.frames_checkpointed if result else 0,
        duration_ms=duration,
        reader_count=after.registered_readers,
        oldest_reader_age=after.oldest_reader_age_seconds,
        state=after.state,
    )


@pytest.fixture()
def os_(tmp_path) -> MemoryOS:
    return MemoryOS(tmp_path / "wal.db")


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


def _reader(tmp_path: Path) -> sqlite3.Connection:
    """A connection holding a real read snapshot, which is what blocks TRUNCATE."""
    conn = sqlite3.connect(str(tmp_path / "wal.db"), timeout=1.0)
    conn.execute("PRAGMA busy_timeout=100")
    conn.execute("BEGIN")
    conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()
    return conn


# ==========================================================================
# what each mode actually does to the file
# ==========================================================================

def test_passive_moves_frames_but_never_returns_the_file(os_):
    """The finding that invalidated two rounds of analysis.

    PASSIVE reports success and a large `frames_checkpointed`, and the file does
    not shrink by a byte. Anyone reading the tuple concludes the log is under
    control; anyone reading `ls -l` sees it is not.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=10 ** 9, hard_limit_bytes=10 ** 10,
                          interval_seconds=0.0)
    o = observe(manager, lambda: manager.checkpoint(CheckpointMode.PASSIVE))

    assert not o.busy, f"nothing should have blocked this: {o}"
    assert o.frames_checkpointed > 0, f"PASSIVE moved no frames at all: {o}"
    assert o.reclaimed == 0, (
        f"PASSIVE returned file space, which contradicts the premise of the "
        f"whole state machine: {o}")


def test_restart_is_not_reclaim_either(os_):
    """RESTART blocks like TRUNCATE and gives back what PASSIVE gives back: nothing.

    Worth pinning because RESTART is the intuitive middle ground, and choosing it
    costs the blocking of TRUNCATE while buying none of the reclaim.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=10 ** 9, hard_limit_bytes=10 ** 10,
                          interval_seconds=0.0)
    o = observe(manager, lambda: manager.checkpoint(
        CheckpointMode.RESTART, allow_blocking=True, busy_timeout_ms=300))

    assert not o.busy, f"no reader was registered: {o}"
    assert o.reclaimed == 0, f"RESTART reclaimed file space: {o}"


def test_truncate_returns_the_file_when_nothing_blocks(os_):
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=10 ** 9, hard_limit_bytes=10 ** 10,
                          interval_seconds=0.0)
    o = observe(manager, lambda: manager.checkpoint(
        CheckpointMode.TRUNCATE, allow_blocking=True, busy_timeout_ms=300))

    assert not o.busy, f"{o}"
    assert o.wal_bytes_before > 0
    assert o.wal_bytes_after == 0, f"TRUNCATE left the file behind: {o}"


# ==========================================================================
# blocked reclaim: bounded, honest, and retried
# ==========================================================================

def test_a_blocked_truncate_gives_up_inside_its_budget(os_, tmp_path):
    """Run 6's lesson, kept under the new policy.

    Unbounded, this took 23,236 ms to move zero frames while a producer's write
    died on its own 10-second busy timeout. The budget is the whole defence.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0)
    manager.conn.execute("PRAGMA busy_timeout=10000")  # staging's setting

    reader = _reader(tmp_path)
    try:
        o = observe(manager, lambda: manager.checkpoint(
            CheckpointMode.TRUNCATE, allow_blocking=True,
            busy_timeout_ms=SCHEDULED_TRUNCATE_BUDGET_MS))
    finally:
        reader.rollback(); reader.close()

    assert o.busy, f"a live read snapshot should have blocked this: {o}"
    assert o.reclaimed == 0, f"busy and yet it reclaimed: {o}"
    assert o.duration_ms < 3000, (
        f"waited {o.duration_ms:.0f}ms against a "
        f"{SCHEDULED_TRUNCATE_BUDGET_MS}ms budget: {o}")


def test_a_blocked_reclaim_is_not_counted_as_success(os_, tmp_path):
    """`busy` must not be laundered into the success counter.

    If it were, a manager that never reclaimed anything would report a healthy
    campaign, which is precisely the reading error that let Run 7 run for six
    hours while the log climbed.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0, truncate_cooldown_seconds=0.0)
    reader = _reader(tmp_path)
    try:
        manager.maybe_checkpoint()
    finally:
        reader.rollback(); reader.close()

    assert manager.metrics["truncate_attempts"] == 1
    assert manager.metrics["truncate_busy"] == 1
    assert manager.metrics["truncate_succeeded"] == 0
    assert manager.metrics["truncate_bytes_reclaimed"] == 0
    assert {a["code"] for a in manager.alerts()} >= {"WAL_CHECKPOINT_BUSY"}


def test_the_retry_after_the_reader_leaves_actually_reclaims(os_, tmp_path):
    """The property that makes "give up and try later" a strategy rather than
    a surrender."""
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0, truncate_cooldown_seconds=0.0)

    reader = _reader(tmp_path)
    blocked = observe(manager, manager.maybe_checkpoint)
    assert blocked.busy and blocked.reclaimed == 0, f"{blocked}"
    reader.rollback(); reader.close()

    freed = observe(manager, manager.maybe_checkpoint)
    assert not freed.busy, f"the reader is gone; this should have run: {freed}"
    assert freed.reclaimed > 0, f"retry reclaimed nothing: {freed}"
    assert manager.metrics["truncate_succeeded"] == 1


def test_a_registered_reader_defers_the_attempt_entirely(os_, tmp_path):
    """When the answer is already known, do not take the write lock to learn it.

    An optimisation, not the safety mechanism — registration is voluntary and
    Run 7 recorded `registered_readers = 0` on all 382 samples while readers
    demonstrably existed. The budget is what actually protects the writers.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0, truncate_cooldown_seconds=0.0)
    reader = _reader(tmp_path)
    manager.note_reader("long-report")
    try:
        o = observe(manager, manager.maybe_checkpoint)
    finally:
        reader.rollback(); reader.close()
        manager.release_reader("long-report")

    assert o.mode == CheckpointMode.PASSIVE.value, f"{o}"
    assert manager.metrics["truncate_skipped_reader"] == 1
    assert manager.metrics["truncate_attempts"] == 0


# ==========================================================================
# the state machine itself
# ==========================================================================

def test_the_soft_limit_starts_the_campaign_and_the_hard_limit_keeps_it(os_):
    manager = manager_for(os_, soft_limit_bytes=64 * 1024, hard_limit_bytes=256 * 1024,
                          resume_threshold_bytes=16 * 1024, interval_seconds=0.0,
                          truncate_cooldown_seconds=0.0)
    assert manager.state() == WALState.NORMAL.value

    _write(os_, 120)                      # over soft, under hard
    o = observe(manager, manager.maybe_checkpoint)
    assert o.mode == CheckpointMode.TRUNCATE.value, f"{o}"
    assert o.reclaimed > 0, f"pressure ran but the file did not come back: {o}"

    tight = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                        resume_threshold_bytes=0, interval_seconds=0.0,
                        truncate_cooldown_seconds=0.0)
    _write(os_, 120, start=500)
    o = observe(tight, tight.maybe_checkpoint)
    assert o.state == WALState.CRITICAL.value, f"{o}"
    assert o.mode == CheckpointMode.TRUNCATE.value, f"{o}"
    assert o.reclaimed > 0, f"critical ran but the file did not come back: {o}"


def test_hysteresis_holds_the_state_between_resume_and_soft(os_):
    """Without the band, a log sitting near its trigger re-arms every sample.

    Resume at 63.9 MB, re-enter at 64.1 MB, forever. The band is what turns a
    threshold into a campaign with a beginning and an end.
    """
    soft, resume = 64 * 1024, 16 * 1024
    manager = manager_for(os_, soft_limit_bytes=soft, hard_limit_bytes=10 ** 9,
                          resume_threshold_bytes=resume, interval_seconds=0.0,
                          truncate_cooldown_seconds=999.0)  # no reclaim; drive by hand

    assert manager._advance_state(0, time.time()) is WALState.NORMAL
    assert manager._advance_state(soft + 1, time.time()) is WALState.PRESSURE
    # inside the band: below the soft limit, above resume -> still the campaign
    assert manager._advance_state(soft - 1, time.time()) is WALState.PRESSURE
    assert manager._advance_state(resume + 1, time.time()) is WALState.PRESSURE
    # and only below resume does it end
    assert manager._advance_state(resume - 1, time.time()) is WALState.NORMAL
    # re-entry needs the soft limit again, not merely the band
    assert manager._advance_state(resume + 1, time.time()) is WALState.NORMAL


def test_a_falling_critical_does_not_stop_at_pressure(os_):
    """Coming down from CRITICAL, the campaign ends at resume, not at soft.

    Otherwise the harness would be told to resume load while the log is still
    two thirds of the way to the limit that just stopped it.
    """
    soft, hard, resume = 64 * 1024, 256 * 1024, 16 * 1024
    manager = manager_for(os_, soft_limit_bytes=soft, hard_limit_bytes=hard,
                          resume_threshold_bytes=resume, interval_seconds=0.0,
                          truncate_cooldown_seconds=999.0)
    assert manager._advance_state(hard + 1, time.time()) is WALState.CRITICAL
    assert manager._advance_state(soft + 1, time.time()) is WALState.CRITICAL
    assert manager._advance_state(soft - 1, time.time()) is WALState.CRITICAL
    assert manager._advance_state(resume - 1, time.time()) is WALState.NORMAL


def test_a_resume_threshold_above_the_soft_limit_is_refused(os_):
    """A resume threshold that is not below the soft limit is not hysteresis."""
    manager = manager_for(os_, soft_limit_bytes=1000, hard_limit_bytes=10_000,
                          resume_threshold_bytes=1000, interval_seconds=0.0)
    assert manager.resume_threshold_bytes < manager.soft_limit_bytes


def test_the_cooldown_stops_a_checkpoint_storm(os_, tmp_path):
    """Under a persistent reader, retrying every interval is a tax with no reclaim.

    Each blocked attempt costs the budget of write-lock contention, so a manager
    that retries freely turns one stuck reader into sustained latency for every
    writer.
    """
    _write(os_, 400)
    manager = manager_for(os_, soft_limit_bytes=1, hard_limit_bytes=2,
                          interval_seconds=0.0, truncate_cooldown_seconds=60.0)
    reader = _reader(tmp_path)
    try:
        for _ in range(8):
            manager.maybe_checkpoint()
    finally:
        reader.rollback(); reader.close()

    assert manager.metrics["truncate_attempts"] == 1, (
        f"cooldown did not hold: {manager.metrics['truncate_attempts']} attempts "
        f"in eight cycles")
    assert manager.metrics["truncate_skipped_cooldown"] == 7
    # The deferred cycles still did the cheap useful thing.
    assert manager.metrics["by_mode"].get(CheckpointMode.PASSIVE.value, 0) >= 7


def test_time_above_each_limit_is_accounted(os_):
    """Run 8 reports `time_above_hard_seconds` as an SLO, so it has to be real."""
    manager = manager_for(os_, soft_limit_bytes=64 * 1024, hard_limit_bytes=256 * 1024,
                          resume_threshold_bytes=16 * 1024, interval_seconds=0.0,
                          truncate_cooldown_seconds=999.0)
    t = time.time()
    manager._advance_state(0, t)
    manager._advance_state(300 * 1024, t + 10)     # was NORMAL for 10s -> nothing billed
    manager._advance_state(300 * 1024, t + 15)     # was CRITICAL for 5s
    manager._advance_state(0, t + 18)              # was CRITICAL for 3s

    s = manager.status()
    assert s.time_above_hard_seconds == pytest.approx(8.0, abs=0.5)
    assert s.time_above_soft_seconds == pytest.approx(8.0, abs=0.5)


# ==========================================================================
# sustained load: the property Run 7 failed
# ==========================================================================

def test_the_log_stays_bounded_across_many_pressure_cycles(os_):
    """Dozens of cycles that each push the log past the soft limit.

    This is the shape of the canary compressed into a test. What it has to show
    is not that the WAL is always small — the soft limit triggers an action, it
    does not promise a ceiling — but that every excursion comes back down, so
    the high-water mark does not climb a staircase the way Run 7's did.
    """
    soft, hard, resume = 96 * 1024, 8 * 1024 * 1024, 24 * 1024
    manager = manager_for(os_, soft_limit_bytes=soft, hard_limit_bytes=hard,
                          resume_threshold_bytes=resume, interval_seconds=0.0,
                          truncate_cooldown_seconds=0.0)

    peaks: list[int] = []
    troughs: list[int] = []
    for cycle in range(30):
        _write(os_, 60, start=cycle * 60)
        peaks.append(manager.status().wal_bytes)      # after the burst
        manager.maybe_checkpoint()
        troughs.append(manager.status().wal_bytes)    # after the campaign acted

    # The peak is set by the burst, not by the manager: between two scheduled
    # points the log grows by exactly what was written, and no policy can
    # intervene mid-burst. Asserting a ceiling on the peak would be asserting
    # something the design does not promise. What the design promises is that
    # every excursion is *returned*, and that is the trough.
    assert max(troughs) < soft, (
        f"the log did not come back below the soft limit after reclaim; worst "
        f"trough {max(troughs):,}B against a {soft:,}B limit")

    # And the staircase — the actual Run 7 failure — is a trend in the peaks.
    first_third, last_third = max(peaks[:10]), max(peaks[20:])
    assert last_third <= first_third * 1.5, (
        f"the high-water mark is climbing: first third peaked at {first_third:,}B, "
        f"last third at {last_third:,}B — this is the Run 7 staircase")

    assert manager.metrics["truncate_succeeded"] > 0, "nothing was ever reclaimed"
    assert manager.metrics["truncate_ineffective"] == 0, (
        "reclaim ran unblocked and returned nothing, repeatedly")
    # No storm: one bounded attempt per cycle at most, never a spin.
    assert manager.metrics["truncate_attempts"] <= 30


def test_reclaim_never_runs_inside_the_observe_hot_path(os_, monkeypatch):
    """Section 3 of the gate, pinned rather than assumed.

    `observe()` writes, records what it must, and returns. If a checkpoint were
    ever reachable from it, one blocked reclaim would become one stalled write —
    which is exactly how Run 6 turned a 23-second checkpoint into a failed
    append and a dead canary.
    """
    calls: list[str] = []
    original = WALCheckpointManager.checkpoint

    def spy(self, *a, **kw):
        calls.append("checkpoint")
        return original(self, *a, **kw)

    monkeypatch.setattr(WALCheckpointManager, "checkpoint", spy)

    for i in range(50):
        event = os_.observe(tenant_id="t1", actor="a", source="unit",
                            content=f"hot path {i}")
        os_.remember(event=event, memory_type=__import__(
            "bio_agent_os.cognitive.models", fromlist=["MemoryType"]
        ).MemoryType.EPISODIC, content=f"hot path {i}")

    assert calls == [], (
        f"observe()/remember() reached the checkpoint manager {len(calls)} times; "
        f"the write path must never checkpoint")
