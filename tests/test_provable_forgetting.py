"""Deleting a memory has to delete everything derived from it.

A memory system that stores a fact in one table and its shadow in four others
cannot honour "forget this". Measured on the live learning store on 2026-08-14,
before any of this existed:

    one consolidated memory built from 8 sources
    -> 2 of the first 4 sources still legible inside it, word for word
    plus 353 rows in memory_embeddings and 308 in hippocampus_labels

So deleting the source row leaves the content in the merged memory, leaves a
vector that still matches queries about it, and leaves a label describing it.
The record is gone and the knowledge is not. For anything touching personal
data that is the difference between a deletion and the appearance of one.

Derived artefacts as of today, each of which has to be dealt with:

    cognitive_memories      the row itself
    memory_embeddings       a vector that still retrieves
    hippocampus_labels      a label naming the topic
    consolidated memories   the merged text, which quotes its sources

These tests are written to fail first. A deletion that "passes" because nothing
checked the derived copies is exactly the failure being prevented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

SECRET = "88888888"
SENSITIVE = f"Mật khẩu wifi văn phòng là {SECRET}."


class FakeEmbedder:
    """Offline and deterministic. What is under test is which rows survive a
    delete, not how good the vectors are."""

    model = "fake"
    VOCAB = ["mật", "khẩu", "wifi", "văn", "phòng", "hợp", "đồng", "cổng",
             "máy", "chủ", "doanh", "thu", "88888888"]

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [1.0 if w in low else 0.0 for w in self.VOCAB] + [0.0]
        if not any(vector):
            vector[-1] = 1.0
        return vector


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "forget.db", embedder=FakeEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return getattr(stored, "memory_id", None) or ""


def _count(memory_os: MemoryOS, table: str, where: str = "", params=()) -> int:
    import sqlite3

    try:
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return int(memory_os.memories.conn.execute(sql, params).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _everything_mentioning(memory_os: MemoryOS, needle: str) -> list[tuple[str, str]]:
    """Every trace of a string anywhere in the store.

    The point of the test: a delete that only removes the obvious row leaves
    hits here, and this is what a regulator or a customer would run.
    """
    import sqlite3

    found: list[tuple[str, str]] = []
    conn = memory_os.memories.conn
    for table, column in (("cognitive_memories", "content"),
                          ("cognitive_memories", "metadata_json"),
                          ("hippocampus_labels", "topic"),
                          ("shadow_memories", "content")):
        try:
            rows = conn.execute(
                f"SELECT {column} FROM {table} WHERE {column} LIKE ?",
                (f"%{needle}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for (value,) in rows:
            found.append((f"{table}.{column}", str(value)[:80]))
    return found


# --------------------------------------------------------------------------
# what a delete must achieve
# --------------------------------------------------------------------------

def test_forget_removes_the_memory_row(os_):
    memory_id = _remember(os_, SENSITIVE)
    assert _count(os_, "cognitive_memories") == 1

    from bio_agent_os.cognitive.forgetting import forget

    report = forget(os_, memory_id=memory_id)
    assert report.memories_deleted == 1
    assert _count(os_, "cognitive_memories") == 0


def test_forget_removes_the_vector_too(os_):
    """A surviving vector still matches queries about the deleted fact. The row
    is gone and retrieval still finds it — the worst of both."""
    from bio_agent_os.cognitive.forgetting import forget
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    memory_id = _remember(os_, SENSITIVE)
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)
    assert _count(os_, "memory_embeddings") == 1

    forget(os_, memory_id=memory_id)
    assert _count(os_, "memory_embeddings") == 0


def test_forget_removes_the_hippocampus_label(os_):
    from bio_agent_os.cognitive.forgetting import forget
    from bio_agent_os.cognitive.hippocampus_label import backfill_labels
    from bio_agent_os.cognitive.reconciliation_worker import worker_for

    memory_id = _remember(os_, SENSITIVE)
    backfill_labels(os_.events.conn, os_.events.outbox)
    worker = worker_for(os_)
    for _ in range(20):
        if worker.run_once() is None:
            break
    assert _count(os_, "hippocampus_labels") >= 1

    forget(os_, memory_id=memory_id)
    assert _count(os_, "hippocampus_labels") == 0


def test_forget_removes_a_consolidated_memory_that_quotes_it(os_):
    """The leak measured on the live store: a merged memory keeps its sources'
    wording, so deleting a source leaves the words behind."""
    from bio_agent_os.cognitive.consolidation import CONSOLIDATED_MARKER
    from bio_agent_os.cognitive.forgetting import forget
    from bio_agent_os.cognitive.models import CognitiveMemory

    memory_id = _remember(os_, SENSITIVE)
    other = _remember(os_, "Máy chủ OCR chạy ở cổng 8000.")

    merged = CognitiveMemory(
        tenant_id="t1", workspace_id="w1", memory_type=MemoryType.SEMANTIC,
        content=f"Tổng hợp: {SENSITIVE} Máy chủ OCR chạy ở cổng 8000.",
        source_event_ids=[], confidence=0.75,
        metadata={CONSOLIDATED_MARKER: [memory_id, other]},
    )
    os_.memories.put(merged, commit=True)
    assert _count(os_, "cognitive_memories") == 3

    forget(os_, memory_id=memory_id)
    leaks = _everything_mentioning(os_, SECRET)
    assert leaks == [], f"bí mật vẫn còn ở: {leaks}"


def test_forget_by_subject_removes_every_related_memory(os_):
    """"Forget everything about An Phát" has to take the phone number with it.
    Deleting only the row whose text contains the name is the bug."""
    from bio_agent_os.cognitive.forgetting import forget

    _remember(os_, "Khách hàng An Phát có số điện thoại 0912345678.")
    _remember(os_, "An Phát ký hợp đồng ngày 12/03/2026.")
    _remember(os_, "Máy chủ OCR chạy ở cổng 8000.")

    report = forget(os_, subject="An Phát")
    assert report.memories_deleted == 2
    assert _everything_mentioning(os_, "0912345678") == []
    assert _count(os_, "cognitive_memories") == 1      # the unrelated one stays


def test_forget_reports_what_it_did(os_):
    """A deletion nobody can audit is not a deletion anyone can rely on."""
    from bio_agent_os.cognitive.forgetting import forget

    memory_id = _remember(os_, SENSITIVE)
    report = forget(os_, memory_id=memory_id)
    payload = report.as_dict()
    assert payload["memories_deleted"] == 1
    assert "derived" in payload
    assert payload["verified_clean"] is True


def test_forget_is_verified_not_assumed(os_):
    """`forget` re-reads the store afterwards and says whether anything is left.

    Reporting success without looking is how the first version of this would
    have passed while leaving the vector in place.
    """
    from bio_agent_os.cognitive.forgetting import forget

    memory_id = _remember(os_, SENSITIVE)
    report = forget(os_, memory_id=memory_id, needle=SECRET)
    assert report.verified_clean is True
    assert report.residue == []


def test_forget_refuses_to_claim_success_when_residue_remains(os_):
    """The honesty case. If something cannot be removed, `forget` has to say so
    rather than return a clean report — a false all-clear on a deletion request
    is worse than an error."""
    from bio_agent_os.cognitive.forgetting import forget

    memory_id = _remember(os_, SENSITIVE)

    # A copy the deleter does not reach. Written through the store's own API
    # rather than a hand-built INSERT: guessing the column list failed three
    # times on NOT NULL constraints, and a test that fights the schema is
    # testing the schema instead of the deletion.
    #
    # `shadow_memories` is a real table the projection pipeline writes to, and
    # `forget` deliberately does not delete from it — so this is the honest
    # shape of "something survived", not a contrived one.
    columns = [row[1] for row in os_.memories.conn.execute(
        "PRAGMA table_info(shadow_memories)")]
    defaults = {
        "shadow_projection_key": "k-leak", "source_event_id": "e-leak",
        "legacy_memory_id": None, "projection_version": 1,
        "tenant_id": "t1", "workspace_id": "w1", "memory_type": "semantic",
        "content": SENSITIVE, "confidence": 0.5, "epistemic_status": "observed",
        "security_label": "internal", "valid_from": None, "valid_to": None,
        "provenance_json": "{}", "metadata_json": "{}",
        "canonical_input_hash": "h1", "output_hash": "h2",
        "comparison_status": "match", "created_at": "2026-08-14T00:00:00",
    }
    used = [c for c in columns if c in defaults]
    os_.memories.conn.execute(
        f"INSERT INTO shadow_memories({','.join(used)}) "
        f"VALUES({','.join('?' * len(used))})",
        tuple(defaults[c] for c in used),
    )
    os_.memories.conn.commit()

    report = forget(os_, memory_id=memory_id, needle=SECRET)
    assert report.verified_clean is False
    assert report.residue, "phải nêu ra chỗ còn sót, không được báo sạch"
