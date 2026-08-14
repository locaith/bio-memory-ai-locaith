"""Two embedding models in one store split it in half, silently.

Measured on the live learning store on 2026-08-14, right after an ingest run
used the default local embedder against a store built with OpenAI:

    openai                 dims=3072   n=353
    sentence-transformers  dims= 384   n= 60

    cosine(openai_vector, st_vector) = 0.0

`cosine()` returns 0.0 when the dimensions differ, and `retrieval` drops
anything below the floor. So "these vectors are incomparable" and "these two
things are unrelated" are rendered as the same number, and the second one wins.
Reachability, measured:

    query embedded with openai                 -> 353/413,  60 invisible
    query embedded with sentence-transformers  ->  60/413, 353 invisible

The floor made it worse. `embedding_calibration` is keyed by model, but
`calibrated_floor()` read whichever row was newest and applied it to
everything. The stored floor was 0.6153, measured on OpenAI; two arbitrary
sentence-transformers vectors already score 0.8525, so on that half of the
store the floor rejected nothing at all.

Neither failure raises, logs, or shows up in a count. The store keeps answering
questions, from half its contents.

The fix is not to forbid a second model — a store may legitimately migrate, and
during a migration both exist. It is to make the comparison honest: a memory
embedded by another model has *no vector information* for this query, which is
already a case retrieval handles (it falls back to lexical). Scoring it zero
claims knowledge the system does not have.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")


class WideEmbedder:
    """Stands in for OpenAI: many dimensions."""

    model = "wide-model"
    VOCAB = ("hợp", "đồng", "doanh", "thu", "cổng", "máy", "chủ", "lương")

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        base = [1.0 if w in low else 0.0 for w in self.VOCAB]
        vector = base * 4                      # 32 dims
        if not any(vector):
            vector[-1] = 1.0
        return vector


class NarrowEmbedder:
    """Stands in for the local model: fewer dimensions, different space."""

    model = "narrow-model"
    VOCAB = ("hợp", "đồng", "doanh", "thu")

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [1.0 if w in low else 0.0 for w in self.VOCAB]   # 4 dims
        if not any(vector):
            vector[-1] = 1.0
        return vector


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "mixed.db", embedder=WideEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return getattr(stored, "memory_id", None) or ""


def _mix(memory_os: MemoryOS) -> tuple[list[str], list[str]]:
    """Two memories in the wide space, two in the narrow one."""
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    wide = [_remember(memory_os, "Hợp đồng ký với công ty Bình Minh."),
            _remember(memory_os, "Doanh thu tháng này là 9.960.000 đồng.")]
    backfill_embeddings(memory_os.memories.conn, WideEmbedder())

    narrow = [_remember(memory_os, "Hợp đồng phụ lục số 2."),
              _remember(memory_os, "Doanh thu quý sau dự kiến tăng.")]
    backfill_embeddings(memory_os.memories.conn, NarrowEmbedder())
    return wide, narrow


# --------------------------------------------------------------------------
# the store must know it holds two spaces
# --------------------------------------------------------------------------

def test_the_store_reports_both_models(os_):
    from bio_agent_os.cognitive.semantic_index import coverage

    _mix(os_)
    models = {m["model"]: m["count"] for m in coverage(os_.memories.conn)["models"]}
    assert models == {"wide-model": 2, "narrow-model": 2}


def test_loading_vectors_for_one_model_excludes_the_other(os_):
    """The core fix. A vector from another space must not come back to be
    scored, because scoring it produces 0.0 and 0.0 means 'unrelated'."""
    from bio_agent_os.cognitive.semantic_index import load_vectors

    wide, narrow = _mix(os_)
    loaded = load_vectors(os_.memories.conn, wide + narrow, model="wide-model")

    assert set(loaded) == set(wide), (
        "vector của model khác vẫn được trả về — sẽ bị chấm 0.0 và loại im lặng"
    )


def test_loading_without_a_model_still_returns_everything(os_):
    """Backward compatible: a caller that does not know its model gets the
    previous behaviour rather than an empty result."""
    from bio_agent_os.cognitive.semantic_index import load_vectors

    wide, narrow = _mix(os_)
    loaded = load_vectors(os_.memories.conn, wide + narrow)
    assert set(loaded) == set(wide + narrow)


# --------------------------------------------------------------------------
# the floor belongs to the model that measured it
# --------------------------------------------------------------------------

def test_the_floor_is_read_for_the_asking_model(os_):
    from bio_agent_os.cognitive.semantic_index import calibrated_floor

    from bio_agent_os.cognitive.semantic_index import CALIBRATION_SCHEMA

    _mix(os_)
    conn = os_.memories.conn
    conn.executescript(CALIBRATION_SCHEMA)
    conn.execute(
        "INSERT INTO embedding_calibration(model,dims,samples,mean,p95,p99,floor,measured_at)"
        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET floor=excluded.floor",
        ("wide-model", 32, 8, 0.5, 0.55, 0.6, 0.61, "2026-08-14T00:00:00"))
    conn.execute(
        "INSERT INTO embedding_calibration(model,dims,samples,mean,p95,p99,floor,measured_at)"
        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET floor=excluded.floor",
        ("narrow-model", 4, 8, 0.85, 0.88, 0.9, 0.93, "2026-08-14T01:00:00"))
    conn.commit()

    assert calibrated_floor(conn, model="wide-model") == pytest.approx(0.61)
    assert calibrated_floor(conn, model="narrow-model") == pytest.approx(0.93)


def test_an_unknown_model_does_not_borrow_another_models_floor(os_):
    """Borrowing is how 0.6153 from OpenAI came to be applied to a space whose
    unrelated baseline is 0.85 — a floor that rejected nothing."""
    from bio_agent_os.cognitive.semantic_index import calibrated_floor

    from bio_agent_os.cognitive.semantic_index import CALIBRATION_SCHEMA

    _mix(os_)
    conn = os_.memories.conn
    conn.executescript(CALIBRATION_SCHEMA)
    conn.execute(
        "INSERT INTO embedding_calibration(model,dims,samples,mean,p95,p99,floor,measured_at)"
        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET floor=excluded.floor",
        ("wide-model", 32, 8, 0.5, 0.55, 0.6, 0.61, "2026-08-14T00:00:00"))
    conn.commit()

    assert calibrated_floor(conn, model="some-other-model") is None


# --------------------------------------------------------------------------
# what the user actually experiences
# --------------------------------------------------------------------------

def test_recall_finds_memories_in_the_querying_models_space(os_):
    _mix(os_)
    hits = os_.recall(query="hợp đồng", context=CTX, limit=10) or []
    assert hits, "không tìm thấy gì cả trong chính không gian của truy vấn"


def test_a_memory_from_the_other_space_is_not_silently_erased(os_):
    """It may rank lower without a vector — it must not become unreachable.

    Before the fix a narrow-space memory scored 0.0 against a wide-space query
    and fell below the floor, so 60 of the user's 413 real memories could not
    be retrieved by any query at all.
    """
    _, narrow = _mix(os_)
    hits = os_.recall(query="Hợp đồng phụ lục số 2", context=CTX, limit=20) or []
    found = {h.memory.memory_id for h in hits}
    assert found & set(narrow), (
        "ký ức nhúng bằng model khác không truy xuất được bằng bất kỳ câu nào"
    )


def test_two_sizes_under_one_model_name_are_still_separated(os_):
    """The disguised version of the same bug.

    This store records the backend, not the model: `text-embedding-3-small`
    (1536 dims) and `text-embedding-3-large` (3072) are both written as
    "openai". Filtering by name alone would pass both and `cosine` would score
    the mismatched pair 0.0 — the silent split, returned under one label.
    """
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings, load_vectors

    class SameNameWide(WideEmbedder):
        model = "one-name"

    class SameNameNarrow(NarrowEmbedder):
        model = "one-name"

    big = _remember(os_, "Hợp đồng ký với công ty Bình Minh.")
    backfill_embeddings(os_.memories.conn, SameNameWide())
    small = _remember(os_, "Doanh thu quý sau dự kiến tăng.")
    backfill_embeddings(os_.memories.conn, SameNameNarrow())

    wide_dims = len(SameNameWide().embed("x"))
    loaded = load_vectors(os_.memories.conn, [big, small],
                          model="one-name", dims=wide_dims)
    assert set(loaded) == {big}, (
        "cùng tên model nhưng khác số chiều vẫn lọt — sẽ bị chấm 0.0 im lặng"
    )


def test_backfill_says_when_it_adds_a_second_model(os_):
    """A store quietly acquiring a second vector space is the moment to say so.
    Nothing warned, and the split was found by reading a coverage line."""
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings, models_present

    _remember(os_, "Hợp đồng ký với công ty Bình Minh.")
    backfill_embeddings(os_.memories.conn, WideEmbedder())
    assert models_present(os_.memories.conn) == {"wide-model"}

    _remember(os_, "Doanh thu quý sau dự kiến tăng.")
    backfill_embeddings(os_.memories.conn, NarrowEmbedder())
    assert models_present(os_.memories.conn) == {"wide-model", "narrow-model"}
