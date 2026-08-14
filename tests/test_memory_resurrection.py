"""Quên rồi có sống lại được không.

`forget()` shipped on 2026-08-14 deleting the memory row, its vectors, its
labels and any consolidated memory quoting it -- and reporting `verified_clean
= True`. Measured the same day, on a fresh store:

    forget()                                   -> verified_clean=True
    cognitive_events.payload_json              -> still holds the secret
    rebuild_version('cognitive_memory') + worker
                                               -> the secret is back, verbatim

So the deletion was undone by a routine repair tool, and the report had already
said it was clean. `rebuild_version` is not an attack; it is what you run after
changing projection logic. Nobody would think to check.

That is not a bug in the erasure -- it is a bug in what the erasure *claimed*.
Deleting a projection while its source event survives is a legitimate and
useful operation: it is reversible by design, which is exactly why the event
log is append-only. The failure is calling it "clean" when the content is one
maintenance command away from returning.

Hence three named operations instead of one `forget()`:

    forget_projection   the memory row. The system stops using it.
    forget_derived      + embeddings, labels, consolidated copies.
    erase_history       + redacts the event payload. Not reversible.

The first two are reversible and now say so. Only the third can answer "is it
really gone" with a yes.

`erase_history` breaks the checksum on purpose. The row keeps the hash of the
payload it used to hold, so a redaction can never be silent: integrity
verification sees the mismatch and looks for an erasure record to explain it. A
redaction without a record is tampering, and stays detectable as tampering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

SECRET = "88888888"
SENSITIVE = f"Mật khẩu wifi văn phòng là {SECRET}."


class FakeEmbedder:
    """Offline and deterministic. Under test is which rows survive, not vector
    quality."""

    model = "fake"
    VOCAB = ["mật", "khẩu", "wifi", "văn", "phòng", "hợp", "đồng", "cổng",
             "máy", "chủ", "doanh", "thu", "88888888", "an", "phát"]

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [1.0 if w in low else 0.0 for w in self.VOCAB] + [0.0]
        if not any(vector):
            vector[-1] = 1.0
        return vector


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "resurrect.db", embedder=FakeEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return getattr(stored, "memory_id", None) or ""


def _hits(memory_os: MemoryOS, table: str, column: str, needle: str) -> int:
    import sqlite3

    try:
        return int(memory_os.memories.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?",
            (f"%{needle}%",),
        ).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _rebuild_everything(memory_os: MemoryOS) -> None:
    """The resurrection vector: the ordinary repair you run after changing
    projection logic."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine
    from bio_agent_os.cognitive.reconciliation_worker import worker_for

    engine = ProjectionReplayEngine(memory_os.events.conn, memory_os.events.outbox)
    engine.rebuild_version("cognitive_memory", dry_run=False)
    worker = worker_for(memory_os)
    for _ in range(200):
        if worker.run_once() is None:
            break


# --------------------------------------------------------------------------
# what the reversible levels must admit about themselves
# --------------------------------------------------------------------------

def test_forget_derived_admits_the_content_survives_in_the_event_log(os_):
    """The honesty fix. Deleting the projection is fine; calling it clean while
    the payload sits in `cognitive_events` is not."""
    from bio_agent_os.cognitive.forgetting import forget_derived

    memory_id = _remember(os_, SENSITIVE)
    report = forget_derived(os_, memory_id=memory_id, needle=SECRET)

    assert report.memories_deleted == 1
    assert report.verified_clean is True, "within its own scope it did finish"
    assert report.reversible is True, (
        "the event payload still holds the content, so a replay brings it back"
    )
    assert report.reversible_via, "must name where it survives, not just flag it"
    assert any("cognitive_events" in str(v) for v in report.reversible_via)


def test_a_routine_rebuild_resurrects_after_forget_derived(os_):
    """Documents the real behaviour rather than wishing it away.

    This is not a defect to fix at this level -- an append-only event log is the
    whole basis of replay. It is the reason `reversible` exists and the reason
    `erase_history` has to.
    """
    from bio_agent_os.cognitive.forgetting import forget_derived

    memory_id = _remember(os_, SENSITIVE)
    forget_derived(os_, memory_id=memory_id, needle=SECRET)
    assert _hits(os_, "cognitive_memories", "content", SECRET) == 0

    _rebuild_everything(os_)
    assert _hits(os_, "cognitive_memories", "content", SECRET) == 1, (
        "if this stops resurrecting, replay itself has broken"
    )


def test_forget_projection_leaves_the_derived_stores_and_says_so(os_):
    """The narrowest level. Useful when the memory is wrong rather than
    unwanted -- but it must not pretend the vectors went with it."""
    from bio_agent_os.cognitive.forgetting import forget_projection
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    memory_id = _remember(os_, SENSITIVE)
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)
    assert _hits(os_, "memory_embeddings", "memory_id", memory_id) == 1

    report = forget_projection(os_, memory_id=memory_id)
    assert report.memories_deleted == 1
    assert _hits(os_, "memory_embeddings", "memory_id", memory_id) == 1, (
        "forget_projection is defined as not touching derived stores"
    )
    assert report.reversible is True
    assert report.verified_clean is False, (
        "a vector that still retrieves is residue, and this level must admit it"
    )


# --------------------------------------------------------------------------
# erasure that actually erases
# --------------------------------------------------------------------------

def test_erase_history_redacts_the_event_payload(os_):
    from bio_agent_os.cognitive.forgetting import erase_history

    memory_id = _remember(os_, SENSITIVE)
    assert _hits(os_, "cognitive_events", "payload_json", SECRET) == 1

    report = erase_history(os_, memory_id=memory_id, needle=SECRET,
                           reason="test", actor="tuananh", confirm=True)
    assert report.events_redacted == 1
    assert _hits(os_, "cognitive_events", "payload_json", SECRET) == 0
    assert report.reversible is False
    assert report.verified_clean is True


def test_a_rebuild_cannot_resurrect_after_erase_history(os_):
    """The test the whole file exists for."""
    from bio_agent_os.cognitive.forgetting import erase_history

    memory_id = _remember(os_, SENSITIVE)
    erase_history(os_, memory_id=memory_id, needle=SECRET,
                  reason="test", actor="tuananh", confirm=True)

    _rebuild_everything(os_)
    assert _hits(os_, "cognitive_memories", "content", SECRET) == 0, (
        "the secret came back from the event log after a hard erasure"
    )


def test_erase_history_refuses_without_explicit_confirmation(os_):
    """Irreversible and one keyword away from the reversible call. A default
    that acts is a default that gets run by accident -- the same reason
    `replay(dry_run=True)` defaults the way it does."""
    from bio_agent_os.cognitive.forgetting import erase_history

    memory_id = _remember(os_, SENSITIVE)
    with pytest.raises(PermissionError):
        erase_history(os_, memory_id=memory_id, reason="test", actor="a")
    assert _hits(os_, "cognitive_events", "payload_json", SECRET) == 1


def test_erase_history_requires_a_reason_and_an_actor(os_):
    """An irreversible operation with no record of who or why is not auditable,
    and an erasure nobody can audit is the thing being prevented."""
    from bio_agent_os.cognitive.forgetting import erase_history

    memory_id = _remember(os_, SENSITIVE)
    with pytest.raises(ValueError):
        erase_history(os_, memory_id=memory_id, confirm=True, actor="a", reason="")
    with pytest.raises(ValueError):
        erase_history(os_, memory_id=memory_id, confirm=True, actor="", reason="r")


# --------------------------------------------------------------------------
# a redaction can never be silent
# --------------------------------------------------------------------------

def test_the_erasure_is_recorded_with_who_why_and_the_old_checksum(os_):
    from bio_agent_os.cognitive.forgetting import erase_history, erasures

    memory_id = _remember(os_, SENSITIVE)
    erase_history(os_, memory_id=memory_id, needle=SECRET,
                  reason="khách yêu cầu xoá", actor="tuananh", confirm=True)

    records = erasures(os_.events.conn)
    assert len(records) == 1
    record = records[0]
    assert record["reason"] == "khách yêu cầu xoá"
    assert record["actor"] == "tuananh"
    assert record["original_checksum"], "without the old hash the break is unexplainable"
    assert record["erased_at"]
    assert SECRET not in str(record), "the erasure record must not quote what it erased"


def test_integrity_check_accepts_a_recorded_redaction(os_):
    """The row keeps the hash of the payload it used to hold, so redaction
    breaks the checksum on purpose. An accounted-for break is not damage."""
    from bio_agent_os.cognitive.forgetting import erase_history, verify_event_integrity

    memory_id = _remember(os_, SENSITIVE)
    erase_history(os_, memory_id=memory_id, needle=SECRET,
                  reason="test", actor="tuananh", confirm=True)

    verdict = verify_event_integrity(os_.events.conn, tenant_id="t1")
    assert verdict.tampered == [], f"a recorded erasure read as tampering: {verdict.tampered}"
    assert verdict.redacted, "the redaction should be reported, just not as damage"


def test_integrity_check_still_catches_an_unrecorded_redaction(os_):
    """The security property. If a redaction without a record read as clean,
    the erasure log would be decoration."""
    from bio_agent_os.cognitive.forgetting import (
        ERASURE_TABLE,
        erase_history,
        verify_event_integrity,
    )

    memory_id = _remember(os_, SENSITIVE)
    erase_history(os_, memory_id=memory_id, needle=SECRET,
                  reason="test", actor="tuananh", confirm=True)

    # Someone removes the paper trail and leaves the redaction.
    os_.events.conn.execute(f"DELETE FROM {ERASURE_TABLE}")
    os_.events.conn.commit()

    verdict = verify_event_integrity(os_.events.conn, tenant_id="t1")
    assert verdict.tampered, (
        "a payload that no longer matches its checksum, with nothing explaining "
        "why, is tampering and must be reported as tampering"
    )


# --------------------------------------------------------------------------
# six ways back in
# --------------------------------------------------------------------------

def test_no_query_path_returns_the_erased_content(os_):
    """The resurrection attack proper.

    One fact, then everything built on top of it, then erase the fact and come
    at it from every direction the system offers. Any path that reproduces the
    content is a leak, regardless of which layer produced it.
    """
    from bio_agent_os.cognitive.consolidation import CONSOLIDATED_MARKER
    from bio_agent_os.cognitive.forgetting import erase_history
    from bio_agent_os.cognitive.models import AccessContext, CognitiveMemory
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    memory_id = _remember(os_, SENSITIVE)
    neighbour = _remember(os_, "Máy chủ OCR chạy ở cổng 8000.")

    merged = CognitiveMemory(
        tenant_id="t1", workspace_id="w1", memory_type=MemoryType.SEMANTIC,
        content=f"Tổng hợp: {SENSITIVE} Máy chủ OCR chạy ở cổng 8000.",
        source_event_ids=[], confidence=0.75,
        metadata={CONSOLIDATED_MARKER: [memory_id, neighbour]},
    )
    os_.memories.put(merged, commit=True)
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)

    erase_history(os_, memory_id=memory_id, needle=SECRET,
                  reason="test", actor="tuananh", confirm=True)
    _rebuild_everything(os_)          # and try to bring it back afterwards

    ctx = AccessContext(tenant_id="t1", workspace_id="w1")
    probes = {
        "direct": "mật khẩu wifi văn phòng là gì",
        "paraphrase": "wifi ở công ty đăng nhập bằng gì",
        "semantic-near": "văn phòng có mật khẩu nào không",
        "multi-hop": "máy chủ OCR và wifi văn phòng",
        "by-number": SECRET,
        "historical": "trước đây mật khẩu wifi là gì",
    }
    leaked: dict[str, str] = {}
    for name, question in probes.items():
        for item in os_.recall(query=question, context=ctx, limit=10) or []:
            text = str(getattr(item, "content", item))
            if SECRET in text:
                leaked[name] = text[:120]
                break

    assert leaked == {}, f"nội dung đã xoá quay lại qua: {leaked}"
    for table, column in (("cognitive_memories", "content"),
                          ("cognitive_events", "payload_json"),
                          ("shadow_memories", "content"),
                          ("hippocampus_labels", "topic")):
        assert _hits(os_, table, column, SECRET) == 0, f"còn sót ở {table}.{column}"


def test_forget_keeps_working_as_before(os_):
    """`forget` is what the CLI and the tests already call. Renaming it out from
    under them would be a breaking change for no benefit."""
    from bio_agent_os.cognitive.forgetting import forget

    memory_id = _remember(os_, SENSITIVE)
    report = forget(os_, memory_id=memory_id, needle=SECRET)
    assert report.memories_deleted == 1
    assert report.verified_clean is True
