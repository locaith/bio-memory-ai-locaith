"""The model half: three boundaries that must hold when the model misbehaves.

Section 2.3 of the join plan calls these non-negotiable. Until now they existed
only as prose in a plan document, which is another way of saying they did not
exist. A boundary nobody checks is a comment.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.background_jobs.label_upgrader import (
    ALLOW_REMOTE_ENV,
    LabelUpgrader,
    RemoteLabellingRefused,
)
from bio_agent_os.cognitive.hippocampus_label import CHEAP, MODEL, ensure_schema


def _db(tmp_path: Path, rows: int = 3) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "up.db"))
    ensure_schema(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cognitive_events ("
        "event_id TEXT PRIMARY KEY, payload_json TEXT)"
    )
    for index in range(rows):
        payload = json.dumps({"content": f"Hợp đồng {index} trị giá 84.000.000 đồng"})
        conn.execute("INSERT INTO cognitive_events VALUES(?,?)", (f"e{index}", payload))
        conn.execute(
            "INSERT INTO hippocampus_labels(projection_key, event_id, tenant_id, "
            "workspace_id, topic, importance_score, is_junk_or_transient, "
            "user_state, label_source, label_pending, projection_version, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,1,?)",
            (f"k{index}", f"e{index}", "t1", "w1", "unlabeled", 7, 0, "unknown",
             CHEAP, f"2026-08-11T00:00:0{index}"),
        )
    conn.commit()
    return conn


class _Engine:
    backend = "ollama"
    model_id = "gemma4:12b"

    def __init__(self, labels) -> None:
        self.labels = labels
        self.calls = 0

    async def generate_structured(self, prompt, schema, temperature=0.1, effort=None):
        self.calls += 1
        if isinstance(self.labels, Exception):
            raise self.labels
        return {"labels": self.labels}


def _label(topic="Contract", score=9, junk=False):
    return {"topic": topic, "importance_score": score,
            "is_junk_or_transient": junk, "user_state": "focused"}


# --------------------------------------------------------------------------
# boundary: memories must not leave this machine without someone saying so
# --------------------------------------------------------------------------

def test_a_cloud_backend_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)

    class _Cloud:
        backend = "anthropic"
        model_id = "some-cloud-model"

    with pytest.raises(RemoteLabellingRefused):
        LabelUpgrader(_Cloud(), _db(tmp_path))


def test_a_cloud_backend_is_allowed_when_a_human_says_so(tmp_path, monkeypatch):
    """Refusing outright would be the wrong shape: there are legitimate reasons,
    and the point is that it has to be said out loud rather than happening by
    default."""
    monkeypatch.setenv(ALLOW_REMOTE_ENV, "1")

    class _Cloud:
        backend = "openai"
        model_id = "some-cloud-model"

    LabelUpgrader(_Cloud(), _db(tmp_path))          # does not raise


def test_a_local_backend_needs_no_permission(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE_ENV, raising=False)
    LabelUpgrader(_Engine([]), _db(tmp_path))


# --------------------------------------------------------------------------
# boundary: a failure must leave work retryable, never fabricate a label
# --------------------------------------------------------------------------

def test_a_failed_batch_leaves_every_row_pending(tmp_path):
    conn = _db(tmp_path)
    up = LabelUpgrader(_Engine(RuntimeError("model exploded")), conn)
    assert asyncio.run(up.upgrade_batch(3)) == 0

    still = conn.execute(
        "SELECT COUNT(*) FROM hippocampus_labels WHERE label_pending = 1"
    ).fetchone()[0]
    assert still == 3, "a failed batch stamped rows as done and lost the work"
    assert up.metrics.failures


def test_a_hanging_model_does_not_hang_the_drain(tmp_path, monkeypatch):
    class _Hangs:
        backend = "ollama"
        model_id = "hangs"

        async def generate_structured(self, *a, **k):
            await asyncio.sleep(30)

    monkeypatch.setattr(
        "bio_agent_os.background_jobs.label_upgrader.BATCH_TIMEOUT_SECONDS", 0.2
    )
    conn = _db(tmp_path)
    up = LabelUpgrader(_Hangs(), conn)

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        got = await up.upgrade_batch(3)
        return got, loop.time() - started

    got, elapsed = asyncio.run(run())
    assert got == 0
    assert elapsed < 5, f"waited {elapsed:.1f}s — the batch timeout did not fire"


def test_a_short_batch_does_not_shift_labels_onto_the_wrong_rows(tmp_path):
    """The failure mode that matters most: a confidently wrong label attached to
    the wrong memory is worse than no label at all."""
    conn = _db(tmp_path, rows=3)
    up = LabelUpgrader(_Engine([_label("First", 9)]), conn)      # 1 label, 3 rows
    assert asyncio.run(up.upgrade_batch(3)) == 1

    rows = dict(conn.execute(
        "SELECT projection_key, topic FROM hippocampus_labels"
    ).fetchall())
    assert rows["k0"] == "First"
    assert rows["k1"] == "unlabeled"
    assert rows["k2"] == "unlabeled"


def test_an_out_of_range_score_is_not_written(tmp_path):
    conn = _db(tmp_path, rows=1)
    up = LabelUpgrader(_Engine([_label("X", 99)]), conn)
    assert asyncio.run(up.upgrade_batch(1)) == 0
    row = conn.execute(
        "SELECT importance_score, label_pending FROM hippocampus_labels"
    ).fetchone()
    assert row == (7, 1), "an unbounded score reached the table"


# --------------------------------------------------------------------------
# the happy path, and not undoing good work
# --------------------------------------------------------------------------

def test_a_good_batch_upgrades_every_row(tmp_path):
    conn = _db(tmp_path, rows=3)
    up = LabelUpgrader(_Engine([_label("A", 9), _label("B", 8), _label("C", 2, True)]), conn)
    assert asyncio.run(up.upgrade_batch(3)) == 3

    rows = conn.execute(
        "SELECT topic, importance_score, label_source, label_pending "
        "FROM hippocampus_labels ORDER BY projection_key"
    ).fetchall()
    assert rows == [("A", 9, MODEL, 0), ("B", 8, MODEL, 0), ("C", 2, MODEL, 0)]


def test_an_already_upgraded_row_is_not_picked_up_again(tmp_path):
    """Twenty seconds of GPU per label is too expensive to spend twice."""
    conn = _db(tmp_path, rows=2)
    engine = _Engine([_label("A", 9), _label("B", 8)])
    up = LabelUpgrader(engine, conn)
    assert asyncio.run(up.upgrade_batch(2)) == 2
    assert up.pending(10) == []
    assert asyncio.run(up.upgrade_batch(2)) == 0
    assert engine.calls == 1, "asked the model again for work already done"


def test_drain_is_bounded(tmp_path):
    """`max_batches` is not a formality — ten batches of ten is roughly half an
    hour of GPU, and nothing GPU-bound runs unattended here."""
    conn = _db(tmp_path, rows=20)
    engine = _Engine([_label(f"T{i}", 8) for i in range(5)])
    up = LabelUpgrader(engine, conn)
    asyncio.run(up.drain(batch=5, max_batches=2, rest_seconds=0))
    assert engine.calls == 2
    assert up.metrics.rows_upgraded == 10
