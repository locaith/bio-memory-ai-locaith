"""Labels are found by a join, not remembered in a queue.

The 36-hour A/B soak of 2026-08-11 measured what enqueueing a label job inside
`observe()` costs: +0.196 ms at p95 on every write, and 2.31% of cycles
breaching the 1.0 ms SLO against a baseline of 0.21%. Nothing broke — 53.6
million jobs, zero lost — but it was a permanent tax on the hot path.

The outbox earns that tax for a memory, which is gone if it is lost. It does not
earn it for a label, which is derived data recomputable from an immutable event
at any later time. "Unlabelled" is a property of the data. So it is discovered
by a join, off the write path, in bounded batches.

The first test is the one that matters: `observe()` must be unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hippocampus_label import (
    HippocampusLabelBuilder,
    backfill_labels,
    unlabelled_count,
)
from bio_agent_os.cognitive.reconciliation_worker import worker_for

HIPPO = HippocampusLabelBuilder.projection_type


@pytest.fixture()
def os_(tmp_path: Path) -> MemoryOS:
    memory_os = MemoryOS(tmp_path / "backfill.db", projection_mode="shadow")
    yield memory_os
    memory_os.close()


def _write(memory_os: MemoryOS, n: int) -> None:
    for index in range(n):
        memory_os.observe(tenant_id="t1", actor="a", source="u",
                          content=f"Hợp đồng {index} trị giá 84.000.000 đồng",
                          workspace_id="w1")


def _jobs(memory_os: MemoryOS, ptype: str) -> int:
    return memory_os.events.conn.execute(
        "SELECT COUNT(*) FROM projection_outbox WHERE projection_type = ?",
        (ptype,),
    ).fetchone()[0]


# --------------------------------------------------------------------------
# the regression this whole redesign exists to prevent
# --------------------------------------------------------------------------

def test_observe_enqueues_no_label_job(os_):
    """The hot path must cost exactly what it costs today. A label job created
    here is a tax paid on every write, for ever, and the soak measured it at
    +0.196 ms p95."""
    _write(os_, 25)
    assert _jobs(os_, HIPPO) == 0
    assert _jobs(os_, "cognitive_memory") == 25


def test_every_event_is_still_discoverable_as_unlabelled(os_):
    """Not enqueueing must not mean losing track. A queue can only deliver what
    was put into it; a join cannot miss an event that exists."""
    _write(os_, 25)
    assert unlabelled_count(os_.events.conn) == 25


# --------------------------------------------------------------------------
# backfill
# --------------------------------------------------------------------------

def test_backfill_enqueues_the_missing_jobs(os_):
    _write(os_, 25)
    assert backfill_labels(os_.events.conn, os_.events.outbox) == 25
    assert _jobs(os_, HIPPO) == 25
    assert unlabelled_count(os_.events.conn) == 0


def test_backfill_is_idempotent(os_):
    """Safe to run from anywhere, as often as you like — that is what makes it
    usable from a cron, a hook, and a test at the same time."""
    _write(os_, 25)
    assert backfill_labels(os_.events.conn, os_.events.outbox) == 25
    assert backfill_labels(os_.events.conn, os_.events.outbox) == 0
    assert backfill_labels(os_.events.conn, os_.events.outbox) == 0
    assert _jobs(os_, HIPPO) == 25


def test_backfill_is_bounded_and_the_caller_loops(os_):
    """An unbounded scan on a large database holds a read snapshot, and a held
    read snapshot is what ended canary runs 8 and 9."""
    _write(os_, 25)
    assert backfill_labels(os_.events.conn, os_.events.outbox, limit=10) == 10
    assert backfill_labels(os_.events.conn, os_.events.outbox, limit=10) == 10
    assert backfill_labels(os_.events.conn, os_.events.outbox, limit=10) == 5
    assert backfill_labels(os_.events.conn, os_.events.outbox, limit=10) == 0
    assert _jobs(os_, HIPPO) == 25


def test_backfill_picks_up_events_written_before_the_feature_existed(os_):
    """The real reason a join beats a queue here. Every memory already in a
    user's database was written when nothing was enqueueing labels. A queue
    could never deliver those; a join sees them because they exist."""
    _write(os_, 10)                       # "before"
    assert _jobs(os_, HIPPO) == 0
    _write(os_, 5)                        # "after"
    assert backfill_labels(os_.events.conn, os_.events.outbox) == 15


def test_backfill_then_drain_produces_real_labels(os_):
    _write(os_, 12)
    backfill_labels(os_.events.conn, os_.events.outbox)

    worker = worker_for(os_)
    for _ in range(100):
        if worker.run_once() is None:
            break

    rows = os_.memories.conn.execute(
        "SELECT COUNT(*), SUM(label_pending), MIN(importance_score), "
        "MAX(importance_score) FROM hippocampus_labels"
    ).fetchone()
    assert rows[0] == 12, "one label per event"
    assert rows[1] == 12, "all still awaiting the model upgrade"
    assert 1 <= rows[2] <= rows[3] <= 10
    assert worker.metrics.dead_lettered == 0
    assert worker.metrics.failed == 0


def test_unlabelled_count_is_zero_on_a_database_without_the_tables(tmp_path: Path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert unlabelled_count(conn) == 0
