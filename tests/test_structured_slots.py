"""Extract the structure once, keep it, reuse it.

Before: ingest a sentence, work out its subject and predicate, throw that
understanding away, then re-derive it on every read by comparing an English
attribute key against Vietnamese text with a cosine. That last step is where
"Lê Sơn đang giữ chức vụ gì?" came back with her phone number.

After: the slot is resolved once, stored on the row, and read by equality.

    RECALL                      58 asked   45 correct   0.7759
    state_at, slot from text    58 asked   34 correct   0.5862
    state_at, slot from the row 58 asked   49 correct   0.8448

Measured on four finished run databases: 914 rows each, 711 resolve to a slot
(77.8%), 203 do not and store nothing. The gate that guards this file reports
`structured_wrong_slot_rate` 0.0000 and `backfill_changed_on_second_run` 0 on
all four.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.slot_backfill import backfill, slot_for

SECRET = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "slots.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


def _slot(memory_os: MemoryOS, memory_id: str) -> dict:
    (blob,) = memory_os.memories.conn.execute(
        "SELECT structured_json FROM cognitive_memories WHERE memory_id=?",
        (memory_id,)).fetchone()
    return json.loads(blob or "{}")


# --------------------------------------------------------------------------
# 1. new writes
# --------------------------------------------------------------------------

def test_a_new_memory_carries_the_slot_it_implies(os_):
    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    slot = _slot(os_, memory_id)
    assert slot["entity"] == "Phạm Vy"
    assert slot["attribute"] == "employer"
    assert slot["resolver"]


def test_a_sentence_the_resolver_cannot_place_stores_nothing(os_):
    """UNKNOWN stays UNKNOWN. Filling the column to raise a coverage number
    turns "I could not tell" into "I decided"."""
    memory_id = _remember(os_, "Hôm nay trời đẹp quá.")
    assert _slot(os_, memory_id).get("attribute") is None


def test_a_caller_supplied_slot_is_not_overwritten(os_):
    """The procedural compiler writes structured content here for a different
    purpose. A slot must not evict it."""
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Phạm Vy làm việc tại công ty An Phát.",
                        workspace_id="w1")
    memory = os_.remember(event=event, memory_type=MemoryType.PROCEDURAL,
                          content="Phạm Vy làm việc tại công ty An Phát.",
                          confidence=0.9,
                          structured_content={"goal": "x", "steps": []})
    assert _slot(os_, memory.memory_id) == {"goal": "x", "steps": []}


def test_the_slot_comes_from_the_redacted_text(os_):
    """A value redacted out of `content` must not reappear here verbatim.

    `CONTENT_COLUMNS` scans this column precisely because it could, and
    deriving from the stored (redacted) text means there is nothing to find.
    """
    memory_id = _remember(os_, SECRET)
    stored = json.dumps(_slot(os_, memory_id), ensure_ascii=False)
    (content,) = os_.memories.conn.execute(
        "SELECT content FROM cognitive_memories WHERE memory_id=?",
        (memory_id,)).fetchone()
    if NEEDLE not in content:                    # the immune system redacted
        assert NEEDLE not in stored


# --------------------------------------------------------------------------
# 2. legacy backfill
# --------------------------------------------------------------------------

def test_the_backfill_is_idempotent(os_):
    """Checked by running it twice and comparing, not by trusting a marker.
    A watermark that says "done" while the pass would still write is the
    failure this catches."""
    for text in ("Phạm Vy làm việc tại công ty An Phát.",
                 "Lê Sơn đang giữ chức trưởng nhóm.",
                 "Hôm nay trời đẹp quá."):
        _remember(os_, text)
    conn = os_.memories.conn
    conn.execute("UPDATE cognitive_memories SET structured_json = '{}'")
    conn.commit()

    first = backfill(conn)
    snapshot = dict(conn.execute(
        "SELECT memory_id, structured_json FROM cognitive_memories").fetchall())
    second = backfill(conn)
    again = dict(conn.execute(
        "SELECT memory_id, structured_json FROM cognitive_memories").fetchall())

    assert first.filled == 2
    assert first.unresolved == 1
    assert second.filled == 0
    assert snapshot == again


def test_the_backfill_is_deterministic(os_, tmp_path):
    """No model, no embedder, no clock. The same sentence yields the same slot
    in two unrelated databases."""
    other = MemoryOS(tmp_path / "other.db")
    try:
        text = "Lê Sơn đang giữ chức trưởng nhóm."
        one = _remember(os_, text)
        two = _remember(other, text)
        assert _slot(os_, one)["attribute"] == _slot(other, two)["attribute"]
        assert slot_for(text)["attribute"] == _slot(os_, one)["attribute"]
    finally:
        other.close()


def test_ingest_and_backfill_agree(os_):
    """One derivation, two callers. A backfilled row must not disagree with a
    freshly written one for identical text — that would be two copies of the
    same idea drifting apart."""
    text = "Phạm Vy sống ở Hà Nội."
    fresh = _slot(os_, _remember(os_, text))
    assert fresh["attribute"] == slot_for(text)["attribute"]
    assert fresh["entity"] == slot_for(text)["entity"]


# --------------------------------------------------------------------------
# 3. privacy
# --------------------------------------------------------------------------

def test_the_deletion_verifier_scans_the_slot_column():
    """Added to `CONTENT_COLUMNS` *before* anything wrote there. A verifier
    that skipped it would report a clean deletion with the value on disk."""
    from bio_agent_os.cognitive.forgetting import CONTENT_COLUMNS

    assert ("cognitive_memories", "structured_json") in CONTENT_COLUMNS


def test_a_deletion_leaves_nothing_in_the_slot_column(os_):
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    _remember(os_, SECRET)
    result = forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.",
                           actor="tuananh")
    assert result.deleted_claims == 1
    left = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE structured_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert left == 0
    assert result.verified_clean


def test_a_replay_does_not_rebuild_the_slot_either(os_):
    """The tombstone stops the row coming back, and the slot comes back with
    the row or not at all."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.projection_control import drain
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    _remember(os_, SECRET)
    forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.", actor="t")

    engine = ProjectionReplayEngine(os_.memories.conn)
    engine.replay(dry_run=False, tenant_id="t1")
    engine.rebuild_version("cognitive_memory", tenant_id="t1", dry_run=False)
    drain(os_, timeout_seconds=30, worker_id="slot-replay-test")

    rows = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories "
        "WHERE content LIKE ? OR structured_json LIKE ?",
        (f"%{NEEDLE}%", f"%{NEEDLE}%")).fetchone()[0]
    assert rows == 0


# --------------------------------------------------------------------------
# 4. migration
# --------------------------------------------------------------------------

def test_a_store_written_before_slots_existed_still_opens(tmp_path):
    """No schema change was made — the column was already in the base table
    and every existing row already held '{}'. This asserts that, so a future
    change that does need a migration cannot slip in unnoticed."""
    path = tmp_path / "legacy.db"
    memory_os = MemoryOS(path)
    _remember(memory_os, "Phạm Vy sống ở Hà Nội.")
    memory_os.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = '{}'")
    memory_os.memories.conn.commit()
    memory_os.close()

    reopened = MemoryOS(path)
    try:
        rows = reopened.memories.conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        assert rows == 1
        report = backfill(reopened.memories.conn)
        assert report.filled == 1
    finally:
        reopened.close()


def test_the_backfill_touches_no_embedding(os_):
    """A migration that forces every vector to be recomputed is a migration
    nobody runs. This one writes one text column."""
    _remember(os_, "Phạm Vy sống ở Hà Nội.")
    conn = os_.memories.conn
    conn.execute("UPDATE cognitive_memories SET structured_json = '{}'")
    conn.commit()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        before = None
    backfill(conn)
    if before is not None:
        after = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        assert after == before


# --------------------------------------------------------------------------
# known limitations, found by an independent adversarial review and NOT fixed
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True,
                   reason="không có embedder thì không phân biệt được trong "
                          "tập, nên fallback từ chối cứu bừa")
def test_a_wrong_slot_does_not_hide_a_memory_without_an_embedder(os_):
    """The bounded fallback declines when it cannot discriminate.

    With an embedder the mis-slotted row is rescued — measured, and asserted
    in `test_an_untrusted_mismatch_is_rescued_not_deleted`. Without one,
    `_by_aspect` cannot separate a birthday from an employer inside one
    person's records, and its escape hatch returns *everything*. Rescuing on
    that basis puts a birthday into an employer history, which is how the
    first attempt broke four tests.

    So the fallback demands positive evidence and this case stays open. It is
    the honest shape of the trade: no discrimination available, no rescue —
    rather than a rescue that is indistinguishable from no filter at all.
    """
    from bio_agent_os.cognitive.temporal_operator import claim_history
    from bio_agent_os.cognitive.models import AccessContext

    assert os_.retrieval.embedder is None
    # A correctly slotted row has to exist, or the structured branch never
    # runs and the mis-slotted one is rescued by the text path — which is how
    # the first version of this test XPASSed and proved nothing.
    _remember(os_, "Phạm Vy làm việc tại công ty Locaith.")
    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"entity": "Phạm Vy", "attribute": "birthday"}), memory_id))
    os_.memories.conn.commit()

    spans = claim_history(os_, subject="Phạm Vy", aspect="employer",
                          predicate="employer",
                          context=AccessContext(tenant_id="t1",
                                                workspace_id="w1"))
    assert any("An Phát" in s.content for s in spans)


def test_a_trusted_mismatch_is_excluded_outright(os_):
    """A schema outranks a guess. That is the one case where a mismatch may
    remove a row with no second chance."""
    from bio_agent_os.cognitive.temporal_operator import claim_history
    from bio_agent_os.cognitive.models import AccessContext

    _remember(os_, "Phạm Vy làm việc tại công ty Locaith.")
    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"entity": "Phạm Vy", "attribute": "birthday",
                     "predicate_epistemic_status": "trusted"}), memory_id))
    os_.memories.conn.commit()

    spans = claim_history(os_, subject="Phạm Vy", aspect="employer",
                          predicate="employer",
                          context=AccessContext(tenant_id="t1",
                                                workspace_id="w1"))
    assert not any("An Phát" in s.content for s in spans)


def test_every_slot_written_today_is_untrusted(os_):
    """Nothing in this system produces a TRUSTED slot, and the API is built
    as though something does. An interface designed only against the sources
    that happen to exist breaks on the first new one."""
    from bio_agent_os.cognitive.slot_backfill import UNTRUSTED, status_of

    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    slot = _slot(os_, memory_id)
    assert status_of(slot) == UNTRUSTED
    assert slot["predicate_source"] == "ingest"
    assert slot["resolver_version"]
    assert slot["derived_at"]


def test_a_slot_with_no_recorded_status_reads_as_untrusted():
    """Rows written before this field existed came from the same resolver. A
    missing provenance must never read as a stronger claim than a present
    one."""
    from bio_agent_os.cognitive.slot_backfill import (
        UNKNOWN, UNTRUSTED, status_of,
    )

    assert status_of({"attribute": "employer"}) == UNTRUSTED
    assert status_of({"attribute": "employer",
                      "predicate_epistemic_status": "nonsense"}) == UNTRUSTED
    assert status_of({}) == UNKNOWN
    assert status_of(None) == UNKNOWN


@pytest.mark.xfail(strict=True,
                   reason="tên bị cắt còn âm tiết cuối khi câu bắt đầu bằng "
                          "chữ thường")
def test_the_entity_survives_a_lowercased_sentence(os_):
    """One person, two entities.

    The world lowercases the first character on SUPERSEDE / CORRECT / REPEAT /
    CONTRADICT sentences, and `_names` only starts a run on an uppercase
    token — so "Từ hôm nay, phạm Vy …" stores `entity: "Vy"` while the ASSERT
    form stores `entity: "Phạm Vy"`. Measured by the review: 20 of 35
    templates.

    Latent today: nothing production reads `entity`, and subject matching
    still runs on text. It becomes real the moment someone matches subjects by
    slot equality — which is the obvious next step — and would then split
    every person's history in two.
    """
    upper = _slot(os_, _remember(os_, "Phạm Vy làm việc tại công ty An Phát."))
    lower = _slot(os_, _remember(
        os_, "Từ hôm nay, phạm Vy làm việc tại công ty Bình Minh."))
    assert upper["entity"] == lower["entity"]


def test_a_stored_slot_never_disagrees_with_its_own_sentence(os_):
    """`structured_wrong_slot_rate = 0`, as an invariant rather than a report.
    A slot that disagrees is worse than no slot: every reader now trusts it
    instead of falling back to the text."""
    for text in ("Phạm Vy làm việc tại công ty An Phát.",
                 "Phạm Vy đang giữ chức phó giám đốc.",
                 "Số điện thoại của Phạm Vy là 0912345678.",
                 "Phạm Vy sống ở Hà Nội.",
                 "Lương của Phạm Vy là 40 triệu."):
        _remember(os_, text)

    wrong = []
    for content, blob in os_.memories.conn.execute(
            "SELECT content, structured_json FROM cognitive_memories"):
        stored = json.loads(blob or "{}").get("attribute")
        fresh = slot_for(str(content)).get("attribute")
        if stored and fresh and stored != fresh:
            wrong.append((content, stored, fresh))
    assert not wrong, wrong
