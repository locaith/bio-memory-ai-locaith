"""The seam between the benchmark and the system it measures.

Five methods, and the reason the list is that short is that every extra one is
another way for the answer key to reach the system under test:

    ingest(event)          one world event, at its tick
    query(question, tick)  what the system says
    forget(scope)          a deletion request
    snapshot()             size and cost, for the curve
    reset()                a clean store

The benchmark keeps its ledger to itself. `cognitive/` never sees the truth,
the expected answer, or which kind of event it is being handed — otherwise the
benchmark becomes a tutorial and its scores measure how well it taught.

That is not a style preference; it is the whole validity of the measurement, so
it is tested rather than asserted in a comment. `test_the_adapter_is_never_told
_the_answer` inspects what actually crosses the seam.

**Two scores, not one.** Every question is graded twice:

    retrieval_hit    did the right value appear in what was retrieved
    answer_correct   did the model's answer contain it

A single end-to-end number cannot tell "the memory did not find it" from "the
model was handed it and answered badly", and those two failures need different
fixes. The retrieval column also costs nothing to run, so a first look at the
system needs no model at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.evals.lifetime_world import EventKind, WorldEvent, generate


@pytest.fixture()
def adapter(tmp_path: Path):
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    made = CognitiveAdapter(tmp_path / "adapter.db")
    yield made
    made.close()


# --------------------------------------------------------------------------
# the seam itself
# --------------------------------------------------------------------------

def test_the_adapter_exposes_only_the_five_methods():
    """Every extra method is another way for the answer key to get across."""
    from bio_agent_os.evals.lifetime_adapter import MemorySystem

    public = {n for n in dir(MemorySystem)
              if not n.startswith("_") and callable(getattr(MemorySystem, n))}
    assert public == {"ingest", "query", "forget", "snapshot", "reset"}, public


def test_the_adapter_is_never_told_the_answer(adapter, monkeypatch):
    """The measurement's validity, checked rather than promised.

    Records everything that crosses the seam and asserts that no ledger value,
    expected answer or event kind is in it. A benchmark that leaks its key
    measures how well it taught.
    """
    seen: list[str] = []
    original = adapter.memory_os.observe

    def spy(**kwargs):
        seen.append(str(kwargs.get("content", "")))
        seen.append(str(kwargs.get("metadata", "")))
        return original(**kwargs)

    monkeypatch.setattr(adapter.memory_os, "observe", spy)

    events, ledger, _ = generate(ticks=60, subjects=4, seed=11)
    for event in events:
        adapter.ingest(event)

    blob = " ".join(seen)
    for kind in EventKind:
        assert kind.value not in blob, f"loại sự kiện lọt sang hệ: {kind.value}"
    for word in ("truth", "ledger", "expected", "answer", "claim",
                 "valid_until", "superseded"):
        assert word not in blob.lower(), f"rò khái niệm của sổ đáp án: {word}"


def test_ingest_carries_the_world_clock_not_the_wall_clock(adapter):
    """A thousand events across two simulated years land aged zero otherwise,
    and the staleness stratum measures nothing."""
    from bio_agent_os.evals.lifetime_world import tick_to_iso

    adapter.ingest(WorldEvent(tick=500, kind=EventKind.ASSERT,
                              text="Trần An sống ở Hà Nội.",
                              subject_id="S00", attribute="city",
                              value="Hà Nội"))
    stamps = [r[0] for r in adapter.memory_os.memories.conn.execute(
        "SELECT observed_at FROM cognitive_memories")]
    assert stamps == [tick_to_iso(500)]


def test_reset_leaves_nothing_behind(adapter):
    """Checkpoints re-run from scratch; a leftover row from the previous run
    would score for work the system did not do this time."""
    adapter.ingest(WorldEvent(tick=1, kind=EventKind.ASSERT,
                              text="Trần An sống ở Hà Nội.",
                              subject_id="S00", attribute="city",
                              value="Hà Nội"))
    assert adapter.snapshot()["memories"] == 1
    adapter.reset()
    assert adapter.snapshot()["memories"] == 0


def test_snapshot_reports_size_and_cost(adapter):
    for tick in range(1, 6):
        adapter.ingest(WorldEvent(tick=tick, kind=EventKind.ASSERT,
                                  text=f"Sự kiện số {tick} về dự án.",
                                  subject_id="S00", attribute="project",
                                  value=f"dự án {tick}"))
    snap = adapter.snapshot()
    assert snap["memories"] == 5
    assert snap["bytes"] > 0
    assert "embedded" in snap


# --------------------------------------------------------------------------
# querying, and the two ways to grade it
# --------------------------------------------------------------------------

def test_query_returns_retrieved_memories_without_needing_a_model(adapter):
    """The retrieval column runs with no engine at all, so a first look at the
    system costs nothing and cannot be blamed on the model."""
    adapter.ingest(WorldEvent(tick=1, kind=EventKind.ASSERT,
                              text="Trần An sống ở Hà Nội.",
                              subject_id="S00", attribute="city",
                              value="Hà Nội"))
    result = adapter.query("Trần An đang sống ở đâu?", tick=10)

    assert result.retrieved, "không truy xuất được gì"
    assert any("Hà Nội" in m for m in result.retrieved)
    assert result.answer == "", "chưa cấu hình model mà vẫn sinh ra câu trả lời"


def test_the_result_says_which_route_the_planner_took(adapter):
    """Failure attribution needs to know whether the planner sent the question
    down the wrong path before anything else is blamed."""
    adapter.ingest(WorldEvent(tick=1, kind=EventKind.ASSERT,
                              text="Trần An sống ở Hà Nội.",
                              subject_id="S00", attribute="city",
                              value="Hà Nội"))
    result = adapter.query("Trần An đang sống ở đâu?", tick=10)
    assert result.route in {"recall", "select_by_class"}
    assert result.query_kind


def test_a_forget_request_actually_deletes(adapter):
    adapter.ingest(WorldEvent(tick=1, kind=EventKind.ASSERT,
                              text="Số điện thoại của Trần An là 0912345678.",
                              subject_id="S00", attribute="phone",
                              value="0912345678"))
    assert adapter.query("số điện thoại Trần An", tick=5).retrieved

    report = adapter.forget("0912345678")
    assert report["memories_deleted"] >= 1
    assert report["checks_run"] > 0

    left = adapter.memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        ("%0912345678%",)).fetchone()[0]
    assert left == 0


def test_ingesting_a_noise_event_stores_it_like_any_other(adapter):
    """The system must not be able to tell noise from signal — that is part of
    the task. Filtering it here would hand over a distinction the ledger
    knows and the system is supposed to work out."""
    adapter.ingest(WorldEvent(tick=1, kind=EventKind.NOISE,
                              text="Hôm nay trời mưa to."))
    assert adapter.snapshot()["memories"] == 1
