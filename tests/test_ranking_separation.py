"""REINFORCEMENT != RELEVANCE, as a selection rule.

A memory mentioned two hundred times may well be stronger. It has not thereby
become the answer to this question.

`reinforcement` adds up to 0.25 to a score. In a store where one claim has
been reconfirmed eight times and the memory that actually answers a question
has not, that is enough to swap them in a six-slot window — and the window is
what reaches the model. `relevance_first` keeps the score formula exactly as
it is and changes only who gets in: **selection by query relevance, ordering
by total score.**

Deliberately not "drop reinforcement from the score". Frequency is the signal
for "what do I keep getting wrong", a question this system is meant to answer.
It keeps its weight and loses only its vote on admission.

Measured on the lifetime world with the lifecycle running (nine distinct
reinforcement levels, highest eight):

    rank_churn                        3/136 = 0.0221
    evidence lost / gained            0 / 0
    sole_evidence_displacement_rate   0.0000

The rate was already zero under the old rule on this benchmark, because the
EVER operator now answers the family where the churn was doing damage without
touching retrieval at all. So this is a guard against a failure that no longer
occurs here — kept as an invariant, not claimed as an improvement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")
QUESTION = "Trần Hà đang giữ chức vụ gì?"
ANSWER = "Trần Hà đang giữ chức trưởng nhóm."


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "rank.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, reinforcement: int = 0) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    memory = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.8)
    if reinforcement:
        memory_os.memories.conn.execute(
            "UPDATE cognitive_memories SET reinforcement_count = ? "
            "WHERE memory_id = ?", (reinforcement, memory.memory_id))
        memory_os.memories.conn.commit()
    return memory.memory_id


def _crowd(memory_os: MemoryOS, n: int = 12) -> None:
    """Loud neighbours: same topic, wrong subject, heavily reconfirmed."""
    for i in range(n):
        _remember(memory_os, f"Người thứ {i} đang giữ chức trưởng phòng.",
                  reinforcement=8)


def test_the_switch_is_off_by_default(os_):
    """Existing callers keep the ranking they had."""
    assert os_.retrieval.ranking == "legacy"


def test_relevance_first_changes_only_who_gets_in(os_):
    """The score formula is untouched — the same memories carry the same
    scores, and only the window's membership can differ."""
    _remember(os_, ANSWER)
    _crowd(os_)

    os_.retrieval.ranking = "legacy"
    legacy = os_.recall(query=QUESTION, context=CTX, limit=6)
    os_.retrieval.ranking = "relevance_first"
    relevance = os_.recall(query=QUESTION, context=CTX, limit=6)

    by_id = {r.memory.memory_id: r.score for r in legacy}
    for result in relevance:
        if result.memory.memory_id in by_id:
            assert result.score == by_id[result.memory.memory_id]


def test_repetition_cannot_evict_the_only_memory_that_answers(os_):
    """The invariant. One memory answers; a dozen reconfirmed neighbours are
    about the same topic and the wrong person."""
    answer_id = _remember(os_, ANSWER)
    _crowd(os_)

    os_.retrieval.ranking = "relevance_first"
    got = os_.recall(query=QUESTION, context=CTX, limit=6)
    assert answer_id in {r.memory.memory_id for r in got}, (
        "ký ức duy nhất trả lời được câu hỏi bị đẩy khỏi cửa sổ bởi những "
        "ký ức chỉ hơn ở chỗ được nhắc nhiều hơn")


def test_strength_still_orders_what_is_inside_the_window(os_):
    """Selection by relevance, ordering by total. Reinforcement keeps its
    weight where it does no harm."""
    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm.", reinforcement=8)
    _remember(os_, "Trần Hà đang giữ chức trưởng nhóm theo ghi chép cũ.")

    os_.retrieval.ranking = "relevance_first"
    got = os_.recall(query=QUESTION, context=CTX, limit=6)
    assert len(got) >= 2
    assert got == sorted(got, key=lambda r: -r.score)


def test_a_small_result_set_is_untouched_by_either_rule(os_):
    """Below the limit there is nothing to select, so the two rules must
    return the same thing — otherwise the switch is doing something it does
    not claim to."""
    _remember(os_, ANSWER)
    _remember(os_, "Trần Hà sống ở Hà Nội.")

    os_.retrieval.ranking = "legacy"
    legacy = [r.memory.memory_id for r in
              os_.recall(query=QUESTION, context=CTX, limit=6)]
    os_.retrieval.ranking = "relevance_first"
    relevance = [r.memory.memory_id for r in
                 os_.recall(query=QUESTION, context=CTX, limit=6)]
    assert legacy == relevance


def test_the_strength_list_names_every_query_independent_part(os_):
    """A component added to the score and forgotten here would keep its vote
    on admission, which is the whole thing this prevents."""
    from bio_agent_os.cognitive.retrieval import HybridRetrievalEngine

    _remember(os_, ANSWER)
    _crowd(os_, n=8)
    (result,) = os_.recall(query=QUESTION, context=CTX, limit=1)
    parts = set(result.explanation["score_components"])

    query_dependent = {"embedding", "semantic", "lexical", "temporal",
                       "query_type", "state_match", "context_match"}
    unclassified = parts - query_dependent - set(HybridRetrievalEngine.STRENGTH_PARTS)
    assert not unclassified, (
        f"thành phần điểm chưa được xếp là 'liên quan tới câu hỏi' hay "
        f"'độ mạnh của ký ức': {sorted(unclassified)}")
