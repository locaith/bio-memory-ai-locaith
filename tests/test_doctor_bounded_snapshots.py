"""The doctor may take as long as it likes. It may not hold the log while it does.

Run 8 died at 2.07 hours of a 24-hour canary on `wal_dwelling_above_hard_limit`,
and the post-mortem found the cause in its own logs:

    21:42 - 23:02   doctor 0.5 - 4.9 s   ->  WAL grew 0 MB during it
    23:07 - 23:37   doctor 8.0 - 11.7 s  ->  WAL grew +30 to +43 MB, every run
    total           111 s of held read   ->  294 MB of log, 2.6 MB per second

A held read snapshot stops SQLite resetting the write-ahead log, so everything
written during the hold accumulates with nothing able to reclaim it. Measured in
isolation, the effect is not subtle — identical write volume, changing only how
the reader behaves:

    no reader                     peak     4.0 MB
    BEGIN, hold 0.35 s            peak 1,284.3 MB
    BEGIN, release immediately    peak     4.0 MB

So the property under test is **not** that the doctor is fast. A scan taking
thirty seconds in three hundred hundred-millisecond slices is safe; one taking
ten seconds in a single slice is not. Total duration is the wrong number and
watching it is what let this run for eight canary runs.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive.diagnostics import DeepDoctor
from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.shadow_runner import run_shadow_comparison, shadow_worker

#: Section 5 of the Run 9 gate. A slice aims for the target and fails the build
#: past the ceiling; between them it is a smell, not a defect.
HOLD_TARGET_MS = 100.0
HOLD_WARN_MS = 250.0
HOLD_FAIL_MS = 1_000.0

BODY = "x" * 380


def _fill(os_: MemoryOS, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        event = os_.observe(tenant_id="t1", actor="a", source="unit",
                            content=f"row {i} {BODY}", workspace_id="ws")
        os_.remember(event=event, memory_type=MemoryType.EPISODIC,
                     content=f"row {i} {BODY}")


def _with_shadow(os_: MemoryOS) -> None:
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    run_shadow_comparison(os_, [r["event_id"] for r in
                                os_.events.conn.execute("SELECT event_id FROM cognitive_events")])


@pytest.fixture()
def loaded(tmp_path) -> MemoryOS:
    os_ = MemoryOS(tmp_path / "d.db", projection_mode="shadow")
    _fill(os_, 4_000)
    _with_shadow(os_)
    return os_


# ==========================================================================
# the hold, sliced
# ==========================================================================

def test_no_single_read_exceeds_the_fail_threshold(loaded):
    """The one that would have caught Run 8.

    Measured on a 1.7 GB copy of Run 8's database, before and after: the worst
    single hold went from 2,147 ms to 104 ms, while total scan time moved from
    4,684 ms to 4,736 ms. The work did not get cheaper. It got interruptible.
    """
    doctor = IncrementalDoctor(sqlite3.connect(str(loaded.db_path), timeout=60.0))
    doctor.run_incremental()                      # advance the cursor
    doctor = IncrementalDoctor(sqlite3.connect(str(loaded.db_path), timeout=60.0))
    report = doctor.run_incremental()

    worst = report.holds_ranked()[0] if report.reader_holds else ("none", {"max_ms": 0})
    assert report.max_hold_ms < HOLD_FAIL_MS, (
        f"{worst[0]} held the read snapshot for {report.max_hold_ms:.0f} ms. "
        f"Anything past {HOLD_FAIL_MS:.0f} ms pins the write-ahead log for long "
        f"enough that a busy writer cannot reclaim it.")


def test_the_shadow_scan_is_sliced_rather_than_one_statement(loaded):
    """check_shadow was the largest single hold in Run 8's shape: 2,147 ms.

    It cannot be made cheap — proving no shadow row leaked into production means
    examining every shadow row. It can be made short.
    """
    doctor = DeepDoctor(sqlite3.connect(str(loaded.db_path), timeout=60.0))
    doctor.report.counts = {"shadow": 4_000}
    doctor.check_shadow()

    assert doctor.report.chunks > 1, (
        "the shadow checks ran as single statements; the whole point is that "
        "they yield the snapshot between slices")
    holds = doctor.report.reader_holds.get("unattributed") or \
        next(iter(doctor.report.reader_holds.values()), {"max_ms": 0})
    assert holds["max_ms"] < HOLD_WARN_MS, f"slice held {holds['max_ms']:.0f} ms"


def test_an_empty_slice_does_not_end_the_scan(tmp_path):
    """The bug this suite exists to prevent, found while writing it.

    A checked-for-absence query returns nothing from almost every slice. An
    early version stopped at the first empty one — which reports "no leak"
    correctly for the wrong reason and would miss a leak past the first slice.
    A CRITICAL check that stops looking is worse than no check.
    """
    os_ = MemoryOS(tmp_path / "e.db", projection_mode="shadow")
    _fill(os_, 1_500)
    _with_shadow(os_)
    doctor = DeepDoctor(sqlite3.connect(str(tmp_path / "e.db"), timeout=60.0))

    seen = list(doctor._chunked(
        "shadow_memories", alias="s",
        sql="SELECT s.rowid FROM shadow_memories s WHERE s.rowid > 1200{window}"))
    total = doctor.conn.execute(
        "SELECT COUNT(*) FROM shadow_memories WHERE rowid > 1200").fetchone()[0]

    assert doctor.report.chunks > 2, "the scan did not slice at all"
    assert len(seen) == total, (
        f"the scan stopped early: {len(seen)} rows seen of {total} that match. "
        f"Every slice before rowid 1200 is empty, and an empty slice is the "
        f"normal case, not the end.")


def test_the_slice_size_adapts_to_the_hold(loaded):
    """Shrinks fast, grows slowly. Overshooting the hold is the failure mode;
    undershooting only costs round trips."""
    doctor = DeepDoctor(sqlite3.connect(str(loaded.db_path), timeout=60.0))
    start = doctor.CHUNK_ROWS_START

    assert doctor._next_chunk_size(start, held_ms=500.0) < start, "did not shrink"
    assert doctor._next_chunk_size(start, held_ms=1.0) > start, "did not grow"
    assert doctor._next_chunk_size(start, held_ms=60.0) == start, "moved inside the corridor"
    assert doctor._next_chunk_size(doctor.CHUNK_ROWS_MIN, 9_999.0) >= doctor.CHUNK_ROWS_MIN
    assert doctor._next_chunk_size(doctor.CHUNK_ROWS_MAX, 0.1) <= doctor.CHUNK_ROWS_MAX


def test_the_scan_does_not_chase_rows_written_while_it_runs(loaded):
    """The ceiling is read once, before the first slice.

    Without it a scan on a live database follows its own tail: every slice
    reveals more rows appended during the previous one, and the snapshot the
    scan is trying to release keeps being renewed.
    """
    doctor = DeepDoctor(sqlite3.connect(str(loaded.db_path), timeout=60.0))
    gen = doctor._chunked("shadow_memories", alias="s",
                          sql="SELECT s.rowid FROM shadow_memories s WHERE 1=1{window}")
    first = next(gen)
    _fill(loaded, 500, start=90_000)          # arrives mid-scan
    _with_shadow(loaded)
    seen = 1 + sum(1 for _ in gen)

    ceiling_at_start = doctor.conn.execute(
        "SELECT COUNT(*) FROM shadow_memories WHERE rowid <= ?",
        (first[0] + seen + 600,)).fetchone()[0]
    assert seen <= ceiling_at_start, (
        "the scan picked up rows written after it began; those belong to the "
        "next pass, not this one")


# ==========================================================================
# the property the canary actually failed on
# ==========================================================================

def test_a_doctor_running_against_a_live_writer_leaves_the_log_reclaimable(tmp_path):
    """Run 8's shape, compressed: write continuously, run the doctor throughout,
    and require that the log can still be reset.

    Measured by file size, not by a counter. Run 7's analysis was wrong for
    exactly this reason — a checkpoint reporting every frame copied, nobody
    blocking, sitting on a 106 MB file.
    """
    db = tmp_path / "live.db"
    os_ = MemoryOS(db, projection_mode="shadow")
    _fill(os_, 2_000)
    _with_shadow(os_)

    wal = lambda: Path(str(db) + "-wal").stat().st_size if Path(str(db) + "-wal").exists() else 0
    stop = threading.Event()
    holds: list[float] = []

    def run_doctor() -> None:
        conn = sqlite3.connect(str(db), timeout=60.0)
        while not stop.is_set():
            d = IncrementalDoctor(conn)
            report = d.run_incremental()
            holds.append(report.max_hold_ms)
            time.sleep(0.05)

    thread = threading.Thread(target=run_doctor, daemon=True)
    thread.start()
    peaks = []
    try:
        for cycle in range(6):
            _fill(os_, 500, start=10_000 + cycle * 500)
            os_.events.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            peaks.append(wal())
    finally:
        stop.set()
        thread.join(timeout=10)

    assert holds, "the doctor never completed a pass"
    assert max(holds) < HOLD_FAIL_MS, (
        f"a doctor pass held the snapshot for {max(holds):.0f} ms while a writer "
        f"was running; that is the Run 8 failure")
    # TRUNCATE ran after every burst. If the doctor were holding a snapshot
    # across it, the file could not come back down and the peaks would climb.
    assert max(peaks[3:]) <= max(peaks[:3]) * 2 + 1_000_000, (
        f"the log is ratcheting despite reclaim: early peaks {peaks[:3]}, "
        f"late peaks {peaks[3:]}")
