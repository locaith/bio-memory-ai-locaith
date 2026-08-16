"""Code correct on disk is not the same as data correct on disk.

`_names` no longer runs a name across a clause boundary. That repairs every
*new* write. It repairs nothing already stored — and until it does,
`SUBJECT_IDENTITY_READ` cannot be turned on against a real store, because
`_by_subject` selects a person's history by the stored subject and a corrupted
one is a definite mismatch: the row leaves the chronology, silently.

`backfill` used to skip any row that already had an `attribute`, and its
`disagreements` report compared only the `attribute` axis. The `entity` axis
was never compared and never repaired. These tests are the reason a resolver
version is a real gate rather than a string in a JSON blob.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.slot_backfill import RESOLVER_VERSION, backfill

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

FIRST = "Bùi Cường sinh năm 1990."
CORRECTION = "Đính chính: thông tin trước là sai, Bùi Cường sinh năm 1991."


@pytest.fixture()
def legacy_store(tmp_path: Path):
    """A store as `aspect_resolver@1` left it: the correction filed under a
    person who does not exist."""
    memory_os = MemoryOS(tmp_path / "legacy.db")
    for text in (FIRST, CORRECTION):
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1")
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.9)

    conn = memory_os.memories.conn
    (memory_id, blob) = conn.execute(
        "SELECT memory_id, structured_json FROM cognitive_memories "
        "WHERE content = ?", (CORRECTION,)).fetchone()
    slot = json.loads(blob)
    slot["entity"] = "sai Bùi Cường"          # what @1 actually wrote
    slot["resolver_version"] = "aspect_resolver@1"
    slot["resolver"] = "aspect_resolver@1"
    conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps(slot, ensure_ascii=False, sort_keys=True), memory_id))
    conn.commit()
    yield memory_os
    memory_os.close()


def _entity(conn: sqlite3.Connection, content: str) -> str | None:
    (blob,) = conn.execute(
        "SELECT structured_json FROM cognitive_memories WHERE content = ?",
        (content,)).fetchone()
    return json.loads(blob).get("entity")


def test_a_stale_row_is_re_derived(legacy_store):
    conn = legacy_store.memories.conn
    assert _entity(conn, CORRECTION) == "sai Bùi Cường"

    report = backfill(conn)

    assert report.rederived >= 1, "hàng cũ không được suy lại"
    assert _entity(conn, CORRECTION) == "Bùi Cường"


def test_the_repair_is_reported_row_by_row(legacy_store):
    """A count cannot tell a repair from a fresh corruption."""
    report = backfill(legacy_store.memories.conn)

    (repair,) = [r for r in report.entity_repairs
                 if r["stored"] == "sai Bùi Cường"]
    assert repair["resolver_now"] == "Bùi Cường"
    assert "Đính chính" in repair["content"]


def test_a_second_pass_changes_nothing(legacy_store):
    """Idempotent, or it is not a migration."""
    conn = legacy_store.memories.conn
    backfill(conn)
    again = backfill(conn)

    assert again.rederived == 0
    assert again.entity_repairs == []
    assert _entity(conn, CORRECTION) == "Bùi Cường"


def test_re_derivation_keeps_content_written_by_other_producers(legacy_store):
    """The procedural compiler writes goal/steps into the same blob. A slot
    repair must not evict it."""
    conn = legacy_store.memories.conn
    (memory_id, blob) = conn.execute(
        "SELECT memory_id, structured_json FROM cognitive_memories "
        "WHERE content = ?", (CORRECTION,)).fetchone()
    slot = json.loads(blob)
    slot["goal"] = "giữ nguyên tôi"
    conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps(slot, ensure_ascii=False, sort_keys=True), memory_id))
    conn.commit()

    backfill(conn)

    (blob,) = conn.execute(
        "SELECT structured_json FROM cognitive_memories WHERE memory_id = ?",
        (memory_id,)).fetchone()
    after = json.loads(blob)
    assert after["goal"] == "giữ nguyên tôi"
    assert after["entity"] == "Bùi Cường"
    assert after["resolver_version"] == RESOLVER_VERSION


def _kinds(memory_os):
    return [s.kind for s in T.claim_history(
        memory_os, subject="Bùi Cường", aspect="ngày sinh", context=CTX)]


def test_an_unmigrated_store_is_read_the_old_way_not_emptied(
        legacy_store, monkeypatch):
    """The read semantics that make turning the flag on survivable.

    A subject written by a superseded resolver is not evidence about this
    person; it is evidence about what an older parser thought. Judged as an
    identity it is a definite mismatch and the row silently leaves the
    chronology. Judged as provenance it is simply not an answer yet, and the
    row falls to the same bounded textual path as a row with no identity at
    all — exactly the behaviour that ships today.

    `STRUCTURED_GAP` and `MISMATCH` are two states, and this is where they had
    been merged into one."""
    monkeypatch.setattr(T, "_SUBJECT_IDENTITY_READ", "on")
    T.reset_execution()

    assert "corrected" in _kinds(legacy_store), (
        "store chưa migrate mà đã mất hàng — đúng lỗi mà ngữ nghĩa này đóng")

    report = T.execution_report()
    assert report["identity_stale_resolver"] >= 1
    assert report["identity_excluded_mentioned"] == 0, (
        "hàng cũ bị loại như thể là người khác")


def test_migration_promotes_the_row_to_identity_selection(legacy_store,
                                                          monkeypatch):
    """After the pass the row is judged by identity, not by text, and the
    answer does not change. A migration that alters what the system believes
    is not a migration."""
    monkeypatch.setattr(T, "_SUBJECT_IDENTITY_READ", "on")
    before = _kinds(legacy_store)

    backfill(legacy_store.memories.conn)

    T.reset_execution()
    assert _kinds(legacy_store) == before
    report = T.execution_report()
    assert report["identity_stale_resolver"] == 0
    assert report["structured_identity_hit"] >= 1


def test_a_row_that_is_genuinely_someone_else_still_leaves_a_witness(
        tmp_path, monkeypatch):
    """The counter must not be dead code. A current-resolver row about another
    person is excluded, and says so."""
    monkeypatch.setattr(T, "_SUBJECT_IDENTITY_READ", "on")
    memory_os = MemoryOS(tmp_path / "two.db")
    try:
        for text in ("Vũ An đang giữ chức trưởng nhóm.",
                     "Trần Vũ An đang giữ chức giám đốc kỹ thuật."):
            event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                      content=text, workspace_id="w1")
            memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                               content=text, confidence=0.9)
        T.reset_execution()

        found = " ".join(s.content for s in T.claim_history(
            memory_os, subject="Vũ An", aspect="job_title",
            predicate="job_title", context=CTX))

        assert "giám đốc kỹ thuật" not in found
        report = T.execution_report()
        assert report["identity_excluded"] >= 1
        assert report["identity_excluded_mentioned"] >= 1, (
            "mọi âm tiết của 'Vũ An' đều nằm trong 'Trần Vũ An', nên đây đúng "
            "là ca text matching không tách được — phải có nhân chứng")
    finally:
        memory_os.close()
