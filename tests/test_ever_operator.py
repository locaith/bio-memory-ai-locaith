"""«Đã từng ... chưa?» executed rather than ranked.

Measured on the answer layer, same world, same model, same prompt:

    generic top-6 + model     19/40 = 0.4750
    existence operator        39/40 = 0.9750      (40/40 with the lifecycle on)

and the model was not called once for that family — prompt volume fell 34%
and total latency with it.

The failure it removes was watched happening. On the A/B of 16/08 one arm
returned "Nhắc lại, phạm Giang làm việc tại công ty Locaith" — the only
evidence for the question — and the other did not, because 232 reconfirmations
had each added up to 0.25 to other memories' scores and pushed it out of the
window. Both stores held the answer. One of them handed it over.

The lesson is the same one the temporal operator taught in August, learned
again on a different question shape: **classifying a question is not
answering it.** `_EVER` existed as a planner label first, and 408 model calls
later the numbers had not moved by a single character.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.aspect_resolver import Predicate
from bio_agent_os.cognitive.ever_operator import answer_ever, parse_ever
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "ever.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, at: str = "2026-01-01T00:00:00+00:00"):
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1", observed_at=at)
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def test_a_question_becomes_a_slot_and_a_value():
    subject, predicate, value = parse_ever(
        "Phạm Vy đã từng có chức vụ là nhân viên kinh doanh đúng không?")
    assert subject == "Phạm Vy"
    assert predicate is Predicate.ROLE
    assert value == "nhân viên kinh doanh"


def test_the_subject_is_never_the_value():
    """A greedy pattern will happily produce "Trần Hà đã từng là Trần Hà"."""
    _, _, value = parse_ever("Trần Hà đã từng là Trần Hà đúng không?")
    assert value == ""


def test_a_question_that_is_not_an_existence_test_does_not_parse():
    """The guard a test caught within the hour of this being written.

    "tôi từng làm sai câu nào" carries the marker and is a question about the
    asker's own record. Claiming it here routed it past `select_by_class` and
    answered it from generic recall.
    """
    subject, predicate, value = parse_ever("tôi từng làm sai câu nào")
    assert not (subject and predicate is not Predicate.UNKNOWN and value)


# --------------------------------------------------------------------------
# the three verdicts
# --------------------------------------------------------------------------

def test_a_superseded_value_still_answers_yes(os_):
    """The whole point. "Was he ever?" is answered by the claim that stopped
    being current — that is what makes it evidence rather than noise."""
    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm.",
              at="2026-01-01T00:00:00+00:00")
    _remember(os_, "Từ hôm nay, Trần Hà đang giữ chức trưởng phòng.",
              at="2026-06-01T00:00:00+00:00")

    answer = answer_ever(
        os_, "Trần Hà đã từng có chức vụ là trưởng nhóm đúng không?",
        context=CTX)
    assert answer.executed
    assert answer.verdict == "yes"
    assert "trưởng nhóm" in answer.evidence_content


def test_a_value_the_slot_never_held_answers_no(os_):
    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm.")
    answer = answer_ever(
        os_, "Trần Hà đã từng có chức vụ là giám đốc kỹ thuật đúng không?",
        context=CTX)
    assert answer.executed
    assert answer.verdict == "no"


def test_an_empty_slot_is_unknown_not_no(os_):
    """"I have no record of that" and "that never happened" are different
    sentences, and a memory that cannot tell them apart will eventually deny
    something true with confidence."""
    _remember(os_, "Trần Hà sống ở Hà Nội.")
    answer = answer_ever(
        os_, "Phạm Vy đã từng có chức vụ là trưởng nhóm đúng không?",
        context=CTX)
    assert answer.verdict == "unknown"
    assert not answer.executed
    assert answer.stage_failed == "history"


def test_a_rumour_is_not_evidence_that_something_was_ever_true(os_):
    """One of four wrong answers was a YES built entirely on a sentence the
    world emitted as an unresolved contradiction. The store had never held
    that claim as true."""
    _remember(os_, "Phạm Nam phụ trách dự án hệ thống OCR.",
              at="2026-01-01T00:00:00+00:00")
    _remember(os_,
              "Theo một nguồn khác, phạm Nam phụ trách dự án cổng thanh toán.",
              at="2026-03-01T00:00:00+00:00")

    answer = answer_ever(
        os_, "Phạm Nam đã từng có dự án phụ trách là cổng thanh toán đúng không?",
        context=CTX)
    assert answer.verdict == "no", answer.history


def test_a_corrected_value_answers_no(os_):
    """CORRECT means the system was misinformed. "Was it ever?" is answered by
    the correction, not by the sentence it corrected."""
    _remember(os_, "Lương của Bùi Cường là 18 triệu.",
              at="2026-01-01T00:00:00+00:00")
    _remember(os_,
              "Đính chính: thông tin trước là sai, lương của Bùi Cường là 40 triệu.",
              at="2026-02-01T00:00:00+00:00")

    answer = answer_ever(
        os_, "Bùi Cường đã từng có lương là 18 triệu đúng không?", context=CTX)
    assert answer.verdict == "no", answer.history


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

def test_the_planner_uses_the_operator_and_says_so(os_):
    from bio_agent_os.cognitive.query_planner import plan

    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm.")
    result = plan(os_, "Trần Hà đã từng có chức vụ là trưởng nhóm đúng không?",
                  context=CTX)
    assert result.used == "ever_operator"
    assert result.verdict == "yes"
    assert result.evidence["subject"] == "Trần Hà"


def test_the_planner_leaves_other_questions_alone(os_):
    """This branch adds a route; it does not take questions away from the ones
    already there."""
    from bio_agent_os.cognitive.query_planner import plan

    _remember(os_, "Làm sai câu: quên chuẩn hoá dữ liệu.")
    result = plan(os_, "tôi từng làm sai câu nào", context=CTX)
    assert result.used != "ever_operator"
    assert not result.used.startswith("recall_after_ever")


def test_a_question_the_operator_declines_is_counted_not_hidden(os_):
    from bio_agent_os.cognitive.query_planner import plan

    _remember(os_, "Trần Hà sống ở Hà Nội.")
    result = plan(os_, "Phạm Vy đã từng có chức vụ là trưởng nhóm đúng không?",
                  context=CTX)
    assert result.used == "recall_after_ever_failed"
    assert result.unanswerable_reason.startswith("history:")


def test_the_operator_needs_no_model(os_):
    """CPU-first, LLM-last. The family that used to cost 40 model calls per
    run now costs none, and the prompt volume fell 34% because of it."""
    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm.")
    assert os_.retrieval.embedder is None
    answer = answer_ever(
        os_, "Trần Hà đã từng có chức vụ là trưởng nhóm đúng không?",
        context=CTX)
    assert answer.verdict == "yes"
