"""Vectors in the transactional layer, and the three rules that keep them safe.

The end-to-end proof used a real embedder over the network. These use a
deterministic fake instead, because what needs pinning is the wiring — that
`observe()` stays free, that a missing vector degrades instead of deleting, and
that nothing here imports a model — and none of that should depend on an API key
being present or a network being up.

The fake is a bag-of-words vector over a fixed vocabulary. Crude, and enough:
paraphrases that share no tokens are *not* meant to work here, that is what the
real embedder is for. What is being tested is the plumbing around it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.semantic_index import (
    backfill_embeddings,
    content_hash,
    cosine,
    coverage,
    load_vectors,
    pack,
    unembedded_count,
    unpack,
)

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

VOCAB = ["contract", "wal", "sqlite", "server", "port", "child", "school",
         "payment", "bank", "revenue", "blender", "scale", "export"]


class FakeEmbedder:
    """Deterministic, offline, and counts its calls so the write path can be
    proved free rather than assumed free."""

    model = "fake-bow"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        low = text.lower()
        # One extra dimension for "none of the vocabulary", kept orthogonal to
        # every vocabulary axis on purpose. The first version put the
        # out-of-vocabulary weight on a *vocabulary* axis chosen by
        # `len(text) % len(VOCAB)`, which made "zzzz qqqq wwww" land on the same
        # axis as "wal" and score 0.707 against the WAL memory. The gate was
        # working; the fake was inventing a match, and the test was measuring
        # the fake.
        vector = [1.0 if word in low else 0.0 for word in VOCAB] + [0.0]
        if not any(vector):
            vector[-1] = 1.0            # never all-zero; cosine needs a norm
        return vector


class ExplodingEmbedder:
    model = "boom"

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("embedding host is down")


def _seed(memory_os: MemoryOS, facts: list[str]) -> None:
    for text in facts:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1")
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.98)


FACTS = [
    "Contract with Binh Minh signed 12 March 2026 worth 84 million",
    "SQLite WAL checkpoint: only TRUNCATE returns file space",
    "Staging server on port 8899 and OCR server on port 8000",
    "Collect the child from school at four thirty",
]


# --------------------------------------------------------------------------
# rule 1: nothing here imports a model
# --------------------------------------------------------------------------

def test_the_module_imports_no_embedder():
    """Checked on the source, not by importing: an import that happens to
    succeed today says nothing about what it dragged in."""
    source = (Path(__file__).resolve().parents[1] / "bio_agent_os" /
              "cognitive" / "semantic_index.py").read_text(encoding="utf-8")
    for forbidden in ("core.embedder", "Embedder", "sentence_transformers",
                      "openai", "llm_engine", "background_jobs"):
        assert f"import {forbidden}" not in source
        assert f"from bio_agent_os.{forbidden}" not in source


def test_retrieval_without_an_embedder_is_unchanged(tmp_path: Path):
    """No embedder, no change — this is what nine canary runs hardened."""
    memory_os = MemoryOS(tmp_path / "none.db")
    _seed(memory_os, FACTS)
    assert memory_os.retrieval.embedder is None
    assert memory_os.recall("SQLite WAL checkpoint", context=CTX, limit=3)
    memory_os.close()


# --------------------------------------------------------------------------
# rule 2: never on the write path
# --------------------------------------------------------------------------

def test_observe_and_remember_never_call_the_embedder(tmp_path: Path):
    """The whole reason vectors are backfilled. A model call per write is a
    write whose latency is somebody else's uptime."""
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "write.db", embedder=embedder)
    _seed(memory_os, FACTS)
    assert embedder.calls == 0, "the write path called the embedder"
    memory_os.close()


def test_backfill_is_bounded_and_the_caller_loops(tmp_path: Path):
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "bound.db", embedder=embedder)
    _seed(memory_os, FACTS)
    conn = memory_os.memories.conn
    assert unembedded_count(conn) == 4
    assert backfill_embeddings(conn, embedder, limit=2) == 2
    assert backfill_embeddings(conn, embedder, limit=2) == 2
    assert backfill_embeddings(conn, embedder, limit=2) == 0
    assert unembedded_count(conn) == 0
    memory_os.close()


def test_backfill_is_idempotent(tmp_path: Path):
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "idem.db", embedder=embedder)
    _seed(memory_os, FACTS)
    conn = memory_os.memories.conn
    backfill_embeddings(conn, embedder)
    before = embedder.calls
    assert backfill_embeddings(conn, embedder) == 0
    assert embedder.calls == before, "re-embedded work already done"
    memory_os.close()


def test_unembedded_count_reports_everything_when_the_table_is_absent(tmp_path: Path):
    """A missing table means *none are embedded*, not zero to do. Returning 0
    there said "nothing to do" at the exact moment there was everything to do,
    and the first end-to-end run silently backfilled nothing because of it."""
    memory_os = MemoryOS(tmp_path / "absent.db")
    _seed(memory_os, FACTS)
    assert unembedded_count(memory_os.memories.conn) == 4
    memory_os.close()


# --------------------------------------------------------------------------
# rule 3: degrade, never delete
# --------------------------------------------------------------------------

def test_a_memory_without_a_vector_is_still_retrievable(tmp_path: Path):
    """Half a backfill must not hide half the memories."""
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "partial.db", embedder=embedder)
    _seed(memory_os, FACTS)
    backfill_embeddings(memory_os.memories.conn, embedder, limit=1)   # only one

    found = memory_os.recall("staging server port 8899", context=CTX, limit=5)
    assert any("8899" in r.memory.content for r in found), (
        "an un-embedded memory was dropped instead of scored the old way"
    )
    memory_os.close()


def test_an_embedder_that_throws_does_not_break_recall(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "boom.db", embedder=ExplodingEmbedder())
    _seed(memory_os, FACTS)
    assert memory_os.recall("SQLite WAL checkpoint", context=CTX, limit=3)
    memory_os.close()


def test_a_backfill_that_throws_leaves_the_work_pending(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "boom2.db")
    _seed(memory_os, FACTS)
    conn = memory_os.memories.conn
    assert backfill_embeddings(conn, ExplodingEmbedder()) == 0
    assert unembedded_count(conn) == 4, "failed embeddings were marked done"
    memory_os.close()


# --------------------------------------------------------------------------
# the gate itself
# --------------------------------------------------------------------------

def test_an_unrelated_query_returns_nothing_once_vectors_exist(tmp_path: Path, monkeypatch):
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "gate.db", embedder=embedder)
    _seed(memory_os, FACTS)
    backfill_embeddings(memory_os.memories.conn, embedder)

    monkeypatch.setattr("bio_agent_os.cognitive.retrieval.EMBEDDING_FLOOR", 0.5)
    assert memory_os.recall("zzzz qqqq wwww nothing here", context=CTX, limit=5) == []
    memory_os.close()


def test_a_related_query_still_returns_its_memory_with_the_gate_on(tmp_path: Path, monkeypatch):
    """The guard against a lazy fix: rejecting everything passes every other
    test in this section."""
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "gate2.db", embedder=embedder)
    _seed(memory_os, FACTS)
    backfill_embeddings(memory_os.memories.conn, embedder)

    monkeypatch.setattr("bio_agent_os.cognitive.retrieval.EMBEDDING_FLOOR", 0.5)
    found = memory_os.recall("sqlite wal", context=CTX, limit=3)
    assert found and "WAL" in found[0].memory.content
    memory_os.close()


# --------------------------------------------------------------------------
# storage mechanics
# --------------------------------------------------------------------------

def test_vectors_survive_the_round_trip():
    original = [0.5, -0.25, 1.0, 0.0]
    restored = list(unpack(pack(original)))
    assert restored == pytest.approx(original, abs=1e-6)


def test_cosine_handles_the_degenerate_cases():
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [1.0]) == 0.0, "mismatched dims must not raise"


def test_edited_content_is_re_embedded(tmp_path: Path):
    """A vector describing what a memory *used to* say is worse than no vector."""
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "edit.db", embedder=embedder)
    _seed(memory_os, FACTS[:1])
    conn = memory_os.memories.conn
    backfill_embeddings(conn, embedder)

    row = conn.execute("SELECT memory_id, content_hash FROM memory_embeddings").fetchone()
    assert row[1] == content_hash(FACTS[0])
    memory_os.close()


def test_load_vectors_tolerates_a_missing_table(tmp_path: Path):
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert load_vectors(conn, ["a", "b"]) == {}


def test_coverage_reports_progress(tmp_path: Path):
    embedder = FakeEmbedder()
    memory_os = MemoryOS(tmp_path / "cov.db", embedder=embedder)
    _seed(memory_os, FACTS)
    conn = memory_os.memories.conn
    backfill_embeddings(conn, embedder, limit=2)
    report = coverage(conn)
    assert report["total"] == 4
    assert report["embedded"] == 2
    assert report["ratio"] == 0.5
    memory_os.close()
