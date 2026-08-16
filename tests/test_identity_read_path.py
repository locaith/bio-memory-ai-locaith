"""One person's question never sees another person's records.

The asymmetry this closes: a predicate was resolved at ingest, persisted, and
read back by equality — while the subject was resolved at ingest, persisted,
and then re-derived from the text on every read. Understand, discard, guess
again, on the other axis.

The adversarial world is three pairs sharing a family name, which is where
text matching quietly leaks:

    Nguyễn Quân / Nguyễn Dũng
    Trần Hà     / Trần Thảo
    Vũ An       / Trần An

and one surname that ends two established identities, where the only correct
answer is to refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

WORLD = [
    ("Số điện thoại của Nguyễn Quân là 0911111111.", "phone"),
    ("Số điện thoại của Nguyễn Dũng là 0922222222.", "phone"),
    ("Trần Hà làm việc tại công ty Locaith.", "employer"),
    ("Trần Thảo làm việc tại công ty An Phát.", "employer"),
    ("Vũ An đang giữ chức trưởng nhóm.", "job_title"),
    ("Trần An đang giữ chức giám đốc kỹ thuật.", "job_title"),
]


@pytest.fixture(autouse=True)
def identity_read_on(monkeypatch):
    """P0-B ships behind a flag, off, until three temporal failures are
    explained. These tests exercise it explicitly."""
    monkeypatch.setattr(T, "_SUBJECT_IDENTITY_READ", "on")


@pytest.fixture()
def world(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "identity.db")
    for text, _ in WORLD:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1")
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.9)
    T.reset_execution()
    yield memory_os
    memory_os.close()


def _contents(memory_os, subject: str, predicate: str) -> list[str]:
    return [s.content for s in T.claim_history(
        memory_os, subject=subject, aspect=predicate, predicate=predicate,
        context=CTX)]


@pytest.mark.parametrize("subject,predicate,mine,theirs", [
    ("Nguyễn Quân", "phone", "0911111111", "0922222222"),
    ("Nguyễn Dũng", "phone", "0922222222", "0911111111"),
    ("Trần Hà", "employer", "Locaith", "An Phát"),
    ("Trần Thảo", "employer", "An Phát", "Locaith"),
    ("Vũ An", "job_title", "trưởng nhóm", "giám đốc kỹ thuật"),
    ("Trần An", "job_title", "giám đốc kỹ thuật", "trưởng nhóm"),
])
def test_a_shared_family_name_does_not_leak(world, subject, predicate,
                                            mine, theirs):
    """`cross_identity_candidate_rate = 0`. Not a ranking imperfection — a
    cross-person leak has no benign reading."""
    found = " ".join(_contents(world, subject, predicate))
    assert mine in found, f"{subject} không thấy hồ sơ của chính mình"
    assert theirs not in found, (
        f"hồ sơ của người khác lọt vào câu hỏi về {subject}")


def test_an_ambiguous_surface_answers_nobody(world):
    """"An" ends both "Vũ An" and "Trần An". Nothing ranks people."""
    assert _contents(world, "An", "job_title") == []
    assert T.EXECUTION["ambiguous_identity_abstain"] >= 1


def test_the_structured_identity_is_what_selects(world):
    T.reset_execution()
    _contents(world, "Nguyễn Quân", "phone")
    assert T.EXECUTION["structured_identity_hit"] >= 1


def test_a_row_with_no_stored_identity_still_reaches_its_owner(world):
    """Legacy rows must not go missing. Written before slots existed, they
    have no identity and fall back to text — bounded, and reported."""
    import json

    (memory_id,) = world.memories.conn.execute(
        "SELECT memory_id FROM cognitive_memories WHERE content LIKE ?",
        ("%0911111111%",)).fetchone()
    world.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"attribute": "phone"}), memory_id))
    world.memories.conn.commit()

    T.reset_execution()
    found = " ".join(_contents(world, "Nguyễn Quân", "phone"))
    assert "0911111111" in found
    assert T.EXECUTION["identity_fallback_attempted"] >= 1


def test_the_text_mutant_leaks_and_must_fail(world, monkeypatch):
    """IGNORE_SUBJECT_ID_USE_TEXT_MATCHING — the implementation that shipped
    until now, reproduced so the suite can kill it.

    `_mentions` requires every syllable of the subject to appear, so
    "Nguyễn Quân" does not match "Nguyễn Dũng" — but a *partial* surface does.
    Asking about "Nguyễn" under text matching returns both people; under
    identity it returns nobody, because "Nguyễn" identifies no one.
    """
    def text_only(rows, subject):
        return [(m, c, o) for m, c, o, _, _, _ in rows
                if T._mentions(str(c), subject)]

    real = T._by_subject
    monkeypatch.setattr(T, "_by_subject", text_only)
    leaked = " ".join(_contents(world, "Nguyễn", "phone"))
    assert "0911111111" in leaked and "0922222222" in leaked, (
        "mutant không tái tạo được rò rỉ — test này chưa chứng minh gì")

    # Restore only the mutant. `monkeypatch.undo()` would also unwind the
    # autouse fixture that turns the flag on, so the "clean" half would run
    # with identity selection disabled and pass for the wrong reason.
    monkeypatch.setattr(T, "_by_subject", real)
    clean = " ".join(_contents(world, "Nguyễn", "phone"))
    assert "0911111111" not in clean and "0922222222" not in clean, (
        "false_merge_read_rate > 0 — họ chung vẫn gộp hai người"
    )


def test_a_definite_mismatch_is_never_rescued_by_text(world):
    """The one exclusion with no second chance. Everything else in this
    system prefers unresolved over wrong; here, wrong means one person
    answering with another person's records."""
    found = " ".join(_contents(world, "Trần Hà", "employer"))
    assert "An Phát" not in found
