"""Three ways a claim stops being current, and they are not the same thing.

    2026-01: CTO = An
    2026-08: CTO = Bình

    "Ai là CTO bây giờ?"      -> Bình
    "Ai là CTO tháng 1/2026?" -> An
    "An từng là CTO không?"   -> có

An's memory is neither wrong nor deleted. It is *superseded*: true then, not
now. Getting that distinction into the store is what separates this from a
vector database with timestamps, and it needs almost no new storage — the
fields are already there and unused.

WHAT THIS MUST NOT DO, measured on the shipped store before writing any of it:

`candidate_pool` filters `superseded_at IS NULL` in SQL (memory_store.py:481),
before `as_of` is applied at all. So marking An `superseded_at` makes An vanish
at *every* point in time including January — the historical answer disappears
along with the current one, and the query that proves the feature works is the
one that breaks. `superseded_at` stays what it already is: row-version
supersession, a storage concept.

Belief supersession is `valid_to`, which `_valid_at` already honours as a
half-open window (`valid_from <= as_of < valid_to`).

That same mechanism gives CORRECTED for free. A corrected claim was never true,
so it must not answer at any `as_of` — and a window where `valid_to ==
valid_from` is empty by construction, since no instant satisfies both
`as_of >= valid_from` and `as_of < valid_from`. The row survives, its history
survives, and it answers nothing. No new filter, no new column, nothing else to
keep in sync.

The three invariants, stated so a regression names itself:

    REPEAT     does not change what is true, only how fresh it is
    SUPERSEDE  the old value WAS true, and still answers historical questions
    CORRECT    the old value was NEVER true, and answers nothing
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

JAN = "2026-01-05T00:00:00+00:00"
MID_JAN = "2026-01-15T00:00:00+00:00"
AUG = "2026-08-01T00:00:00+00:00"
NOW = "2026-08-14T00:00:00+00:00"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "lifecycle.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, valid_from: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1",
                              valid_from=valid_from, observed_at=valid_from)
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return stored.memory_id


def _pool(memory_os: MemoryOS, query: str, as_of: str | None) -> list[str]:
    return [m.content for m in memory_os.memories.candidate_pool(
        "t1", query, workspace_id="w1", as_of=as_of)]


@pytest.fixture()
def cto(os_):
    """The worked example from the requirement, as data."""
    an = _remember(os_, "CTO là An.", valid_from=JAN)
    binh = _remember(os_, "CTO là Bình.", valid_from=AUG)
    return an, binh


# --------------------------------------------------------------------------
# the constraint that shaped the design
# --------------------------------------------------------------------------

def test_superseded_at_hides_a_memory_at_every_point_in_time(os_, cto):
    """Why belief supersession is not `superseded_at`.

    Documents the shipped behaviour rather than wishing it away: the SQL filter
    runs before `as_of` does, so this is not tunable from the outside.
    """
    an, _ = cto
    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET superseded_at=? WHERE memory_id=?",
        (NOW, an))
    os_.memories.conn.commit()

    assert not any("An" in c for c in _pool(os_, "CTO", MID_JAN)), (
        "dùng superseded_at thì câu hỏi về tháng 1 cũng mất câu trả lời"
    )


# --------------------------------------------------------------------------
# SUPERSEDE — true then, not now
# --------------------------------------------------------------------------

def test_supersede_answers_now_with_the_new_value(os_, cto):
    from bio_agent_os.cognitive.claim_lifecycle import supersede

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    now = _pool(os_, "CTO", NOW)
    assert any("Bình" in c for c in now)
    assert not any("An" in c for c in now)


def test_supersede_still_answers_the_historical_question(os_, cto):
    """The half the requirement is really about."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    january = _pool(os_, "CTO", MID_JAN)
    assert any("An" in c for c in january), "mất câu trả lời cho tháng 1"
    assert not any("Bình" in c for c in january), "Bình chưa nhậm chức tháng 1"


def test_supersede_writes_both_sides_of_the_boundary(os_, cto):
    """A gap between the two windows is an interval where nothing is true, and
    no single-row invariant can see it."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    old = os_.memories.latest(an, "t1")
    new = os_.memories.latest(binh, "t1")
    assert old.valid_to == AUG
    assert new.valid_from == AUG, "hai cửa sổ không chạm nhau — có khoảng trống"


def test_the_old_claim_is_still_on_record(os_, cto):
    """"An từng là CTO không?" has to remain answerable."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    assert os_.memories.latest(an, "t1") is not None
    assert any("An" in c for c in _pool(os_, "CTO", None)), (
        "hỏi không kèm mốc thời gian thì cả hai phải còn"
    )


# --------------------------------------------------------------------------
# CORRECT — never true
# --------------------------------------------------------------------------

def test_a_corrected_claim_answers_at_no_point_in_time(os_):
    """The distinction that a timestamp alone cannot express.

    Treating a correction as a supersession answers "what was the birthday in
    January?" with a date that was never anyone's birthday — and it looks
    exactly like working temporal reasoning.
    """
    from bio_agent_os.cognitive.claim_lifecycle import correct

    wrong = _remember(os_, "Sinh nhật là 12/03/1990.", valid_from=JAN)
    right = _remember(os_, "Sinh nhật là 05/07/1988.", valid_from=AUG)
    correct(os_, wrong_id=wrong, right_id=right, actor="tuananh")

    for as_of in (MID_JAN, AUG, NOW):
        found = _pool(os_, "sinh nhật", as_of)
        assert not any("12/03/1990" in c for c in found), (
            f"giá trị đã đính chính vẫn trả lời ở mốc {as_of}"
        )


def test_a_correction_keeps_the_row_and_its_history(os_):
    """Explainability. "Why did you change your mind?" needs the wrong belief
    to still exist somewhere."""
    from bio_agent_os.cognitive.claim_lifecycle import correct, transitions

    wrong = _remember(os_, "Sinh nhật là 12/03/1990.", valid_from=JAN)
    right = _remember(os_, "Sinh nhật là 05/07/1988.", valid_from=AUG)
    correct(os_, wrong_id=wrong, right_id=right, actor="tuananh")

    assert os_.memories.latest(wrong, "t1") is not None
    record = transitions(os_, memory_id=wrong)
    assert record and record[-1]["to_state"] == "corrected"
    assert right in record[-1]["evidence"]


def test_correction_and_supersession_are_distinguishable_afterwards(os_):
    from bio_agent_os.cognitive.claim_lifecycle import (
        ClaimState, correct, state_of, supersede,
    )

    an = _remember(os_, "CTO là An.", valid_from=JAN)
    binh = _remember(os_, "CTO là Bình.", valid_from=AUG)
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    wrong = _remember(os_, "Sinh nhật là 12/03/1990.", valid_from=JAN)
    right = _remember(os_, "Sinh nhật là 05/07/1988.", valid_from=AUG)
    correct(os_, wrong_id=wrong, right_id=right, actor="tuananh")

    assert state_of(os_, an) is ClaimState.SUPERSEDED
    assert state_of(os_, wrong) is ClaimState.CORRECTED
    assert state_of(os_, binh) is ClaimState.ACTIVE


# --------------------------------------------------------------------------
# REPEAT — nothing changes but freshness
# --------------------------------------------------------------------------

def test_reconfirming_changes_nothing_about_what_is_true(os_, cto):
    """The trap a recency-ranked store falls into: a restated old value read as
    a new one. Measured in the lifetime benchmark, `latest_mention` scores 0.533
    on historical questions for exactly this reason."""
    from bio_agent_os.cognitive.claim_lifecycle import reconfirm, supersede

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")
    before = _pool(os_, "CTO", NOW)

    reconfirm(os_, memory_id=an, at=NOW, actor="tuananh")

    assert _pool(os_, "CTO", NOW) == before, (
        "nhắc lại một giá trị cũ đã làm thay đổi câu trả lời hiện tại"
    )
    assert os_.memories.latest(an, "t1").valid_to == AUG, "cửa sổ bị đổi"


def test_restating_a_superseded_claim_does_not_make_it_current_again(os_, cto):
    """`state_of` reads the transition log backwards, and a restatement is the
    newest row.

    Found in the worked example: `reconfirm` is told when the restatement
    happened in the world while `supersede` stamps the wall clock, so ordering
    the log by `at` interleaved two clocks and put a 2026-08-14 world event
    after a wall-clock entry from today. Ordering by insertion fixes the
    sequence; skipping restatements fixes the meaning. Both are needed —
    either alone lets a superseded claim read back as active.
    """
    from bio_agent_os.cognitive.claim_lifecycle import (
        ClaimState, reconfirm, state_of, supersede,
    )

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")
    reconfirm(os_, memory_id=an, at="2026-01-20T00:00:00+00:00", actor="tuananh")

    assert state_of(os_, an) is ClaimState.SUPERSEDED, (
        "nhắc lại một ký ức đã bị thay thế đã đưa nó về trạng thái hiện hành"
    )
    assert not any("An" in c for c in _pool(os_, "CTO", NOW))


def test_the_transition_log_is_in_the_order_it_was_written(os_, cto):
    """Two clocks in one column made the order meaningless."""
    from bio_agent_os.cognitive.claim_lifecycle import reconfirm, supersede, transitions

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")
    reconfirm(os_, memory_id=an, at="2020-01-01T00:00:00+00:00", actor="tuananh")

    reasons = [r["reason"] for r in transitions(os_, memory_id=an)]
    assert reasons == ["replaced_by_newer", "reconfirmed"], reasons


def test_reconfirming_is_recorded_and_raises_the_confirmation_count(os_, cto):
    from bio_agent_os.cognitive.claim_lifecycle import reconfirm, transitions

    _, binh = cto
    before = os_.memories.latest(binh, "t1").reinforcement_count
    reconfirm(os_, memory_id=binh, at=NOW, actor="tuananh")

    assert os_.memories.latest(binh, "t1").reinforcement_count == before + 1
    assert transitions(os_, memory_id=binh)[-1]["reason"] == "reconfirmed"


# --------------------------------------------------------------------------
# every transition has to be explainable
# --------------------------------------------------------------------------

def test_every_transition_records_who_why_when_and_on_what_evidence(os_, cto):
    from bio_agent_os.cognitive.claim_lifecycle import supersede, transitions

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    record = transitions(os_, memory_id=an)[-1]
    assert record["from_state"] == "active"
    assert record["to_state"] == "superseded"
    assert record["reason"] == "replaced_by_newer"
    assert record["actor"] == "tuananh"
    assert record["at"]
    assert binh in record["evidence"]


def test_a_transition_needs_an_actor(os_, cto):
    """A state change with no record of who made it is not auditable, and an
    audit trail nobody can read is decoration."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede

    an, binh = cto
    with pytest.raises(ValueError):
        supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="")


def test_the_transition_log_holds_no_memory_content(os_, cto):
    """It is written on every state change, so any content in it is content
    that `forget` has to reach. Ids and enum values only."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede, transitions

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")

    blob = str(transitions(os_, memory_id=an))
    for word in ("An", "Bình", "CTO"):
        assert word not in blob.replace(an, "").replace(binh, ""), (
            f"nhật ký chuyển trạng thái có trích nội dung: {word!r}"
        )


def test_forgetting_removes_the_transition_log_too(os_, cto):
    """A new derived store that `forget` does not reach is a new place for
    deleted data to survive. The list in `forgetting.DERIVED_TABLES` is the one
    the verification re-reads, so this belongs in it."""
    from bio_agent_os.cognitive.claim_lifecycle import supersede, transitions
    from bio_agent_os.cognitive.forgetting import DERIVED_TABLES, forget_derived

    an, binh = cto
    supersede(os_, old_id=an, new_id=binh, boundary=AUG, actor="tuananh")
    assert transitions(os_, memory_id=an)

    assert any(t == "memory_lifecycle_transitions" for t, _ in DERIVED_TABLES), (
        "bảng mới chưa được khai báo trong DERIVED_TABLES"
    )
    forget_derived(os_, memory_id=an, needle="An")
    assert transitions(os_, memory_id=an) == []
