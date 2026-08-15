"""Each law twice: the system must satisfy it, a mutant must violate it.

A law that nothing can fail is not being enforced, and this project has
shipped two checks like that already — a deletion report that said "clean"
having probed nothing, and a benchmark family every fake system scored 30/30
on. So every law here is tested from both sides, and the mutant is the half
that matters.

The four laws that already held were scattered across `claim_lifecycle`,
`forgetting` and `models` as individual decisions. Collecting them is the
point: a rule living inside one function can be removed by a future patch that
looks harmless, and nobody notices until a benchmark drifts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import (
    CognitiveMemory,
    EpistemicStatus,
    ExecutionOutcome,
    MemoryType,
)
from bio_agent_os.memory_constitution import LAWS, LawId, check_all
from bio_agent_os.memory_constitution.laws import (
    check_confidence_not_certainty,
    check_inference_not_observation,
    check_memory_not_evidence,
    check_recent_not_correct,
    check_repetition_not_truth,
    check_success_not_good_learning,
)

JAN = "2026-01-05T00:00:00+00:00"
AUG = "2026-08-01T00:00:00+00:00"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "constitution.db")
    yield memory_os
    memory_os.close()


# --------------------------------------------------------------------------
# the shape of the layer itself
# --------------------------------------------------------------------------

def test_every_law_states_what_it_means_and_why():
    """A law nobody can read is a law nobody will keep."""
    for law_id, entry in LAWS.items():
        assert entry.statement.strip(), law_id
        assert entry.why.strip(), f"{law_id} không nói vì sao nó tồn tại"
        assert entry.mutant_describes.strip(), (
            f"{law_id} không mô tả cách phá nó — không kiểm được")
        assert callable(entry.invariant)


def test_all_six_laws_are_present():
    assert set(LAWS) == set(LawId), set(LawId) - set(LAWS)


def test_checking_nothing_does_not_produce_a_clean_sheet():
    """`check_all` with no arguments must return no verdicts, not six passes.

    The exact shape of RULE_002 applied to this module itself: a caller must
    not be able to claim compliance by supplying nothing to check.
    """
    assert check_all() == []


# --------------------------------------------------------------------------
# RULE_001 — repetition is not truth
# --------------------------------------------------------------------------

def test_reconfirming_a_claim_satisfies_rule_001(os_):
    from bio_agent_os.cognitive.claim_lifecycle import reconfirm

    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="CTO là An.", workspace_id="w1",
                        valid_from=JAN, observed_at=JAN)
    stored = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="CTO là An.", confidence=0.7)
    before = os_.memories.latest(stored.memory_id, "t1")

    reconfirm(os_, memory_id=stored.memory_id, at=AUG, actor="tuananh")
    after = os_.memories.latest(stored.memory_id, "t1")

    verdict = check_repetition_not_truth(
        before, after,
        confirmations_before=before.reinforcement_count,
        confirmations_after=after.reinforcement_count)
    assert verdict, verdict.detail


def test_a_mutant_that_believes_repetition_fails_rule_001():
    """The failure mode a brain has: hear it again, believe it harder."""
    before = CognitiveMemory(tenant_id="t1", memory_type=MemoryType.SEMANTIC,
                             content="CTO là An.", source_event_ids=[],
                             confidence=0.7, valid_from=JAN)
    louder = replace(before, confidence=0.9)

    verdict = check_repetition_not_truth(before, louder,
                                         confirmations_before=0,
                                         confirmations_after=1)
    assert not verdict, "luật không bắt được việc nhắc lại làm tăng tin cậy"
    assert "tin cậy tăng" in verdict.detail


def test_a_mutant_that_moves_the_validity_window_fails_rule_001():
    before = CognitiveMemory(tenant_id="t1", memory_type=MemoryType.SEMANTIC,
                             content="CTO là An.", source_event_ids=[],
                             confidence=0.7, valid_from=JAN, valid_to=AUG)
    revived = replace(before, valid_to=None)

    verdict = check_repetition_not_truth(before, revived,
                                         confirmations_before=0,
                                         confirmations_after=1)
    assert not verdict
    assert "cửa sổ hiệu lực" in verdict.detail


# --------------------------------------------------------------------------
# RULE_002 — a memory is not evidence
# --------------------------------------------------------------------------

def test_a_real_deletion_report_satisfies_rule_002(os_):
    from bio_agent_os.cognitive.forgetting import forget_derived

    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Mật khẩu wifi là 88888888.", workspace_id="w1")
    stored = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="Mật khẩu wifi là 88888888.", confidence=0.9)
    report = forget_derived(os_, memory_id=stored.memory_id)
    assert check_memory_not_evidence(report), "báo cáo thật lại vi phạm luật"


def test_a_mutant_that_claims_clean_without_checking_fails_rule_002():
    @dataclass
    class OverconfidentReport:
        checks_run: int = 0
        verified_clean: bool = True

    verdict = check_memory_not_evidence(OverconfidentReport())
    assert not verdict, "luật không bắt được việc báo sạch mà chưa kiểm gì"
    assert "chưa chạy phép kiểm nào" in verdict.detail


# --------------------------------------------------------------------------
# RULE_003 — an inference is not an observation
# --------------------------------------------------------------------------

def test_stored_memories_satisfy_rule_003(os_):
    for text, status in (("Máy chủ chạy ở cổng 8000.", EpistemicStatus.OBSERVED),
                         ("Có lẽ tải cao vào buổi tối.", EpistemicStatus.INFERRED)):
        event = os_.observe(tenant_id="t1", actor="a", source="u",
                            content=text, workspace_id="w1",
                            epistemic_status=status)
        os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                     content=text, confidence=0.8, epistemic_status=status)

    memories = os_.memories.active("t1", workspace_id="w1")
    assert check_inference_not_observation(memories), "ký ức mất dấu nguồn gốc"


def test_a_mutant_that_drops_the_epistemic_status_fails_rule_003():
    @dataclass
    class Untracked:
        memory_id: str = "m1"
        epistemic_status: str = ""

    verdict = check_inference_not_observation([Untracked()])
    assert not verdict, "luật không bắt được ký ức mất trạng thái nhận thức"


# --------------------------------------------------------------------------
# RULE_004 — recent is not correct
# --------------------------------------------------------------------------

def test_a_restated_old_claim_does_not_become_current(os_):
    """The end-to-end version, through the temporal operator."""
    from bio_agent_os.cognitive.temporal_operator import claim_history

    class Embedder:
        model = "fake"

        def embed(self, text):
            low = str(text).lower()
            return [float("công ty" in low), float("sinh" in low), 0.0]

    os_.retrieval.embedder = Embedder()
    for text, at in (("Bùi Cường làm việc tại công ty Locaith.", JAN),
                     ("Bùi Cường làm việc tại công ty Bình Minh.", AUG),
                     ("Nhắc lại, Bùi Cường làm việc tại công ty Locaith.",
                      "2026-09-01T00:00:00+00:00")):
        event = os_.observe(tenant_id="t1", actor="a", source="u",
                            content=text, workspace_id="w1", observed_at=at)
        os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                     content=text, confidence=0.8)

    spans = claim_history(os_, subject="Bùi Cường", aspect="công ty",
                          context=None)
    live = [s for s in spans if s.valid_to is None]
    assert live and "Bình Minh" in live[0].content, [s.content for s in spans]


def test_a_mutant_that_answers_with_the_newest_row_fails_rule_004():
    @dataclass
    class Span:
        memory_id: str
        valid_to: str | None

    retired = Span("m-old", AUG)          # window already closed
    verdict = check_recent_not_correct(retired, newest=retired)
    assert not verdict, "luật không bắt được việc chọn theo độ mới"
    assert "độ mới" in verdict.detail


def test_answering_from_an_open_window_satisfies_rule_004():
    @dataclass
    class Span:
        memory_id: str
        valid_to: str | None

    current = Span("m-new", None)
    assert check_recent_not_correct(current, newest=current)


# --------------------------------------------------------------------------
# RULE_005 — succeeding is not learning something worth keeping
# --------------------------------------------------------------------------

def test_promoting_a_verified_outcome_satisfies_rule_005():
    outcome = ExecutionOutcome(goal="deploy", steps=("build", "test", "ship"),
                               success=True, verifier_passed=True)
    assert check_success_not_good_learning(outcome, promoted=True)


def test_a_mutant_that_learns_from_bare_success_fails_rule_005():
    """The dangerous one, and the reason it is written before Reflection.

    A route that reached the goal without an independent check is exactly the
    kind that succeeds often enough to look like skill.
    """
    outcome = ExecutionOutcome(goal="deploy", steps=("force push",),
                               success=True, verifier_passed=False)
    verdict = check_success_not_good_learning(outcome, promoted=True)
    assert not verdict, "luật không bắt được việc học từ thành công trần"
    assert "bộ kiểm độc lập" in verdict.detail


def test_a_mutant_that_learns_from_a_run_with_errors_fails_rule_005():
    outcome = ExecutionOutcome(goal="migrate", steps=("run",), success=True,
                               verifier_passed=True,
                               errors=("mất 3 dòng dữ liệu",))
    verdict = check_success_not_good_learning(outcome, promoted=True)
    assert not verdict
    assert "lỗi trong quá trình" in verdict.detail


def test_not_promoting_is_always_allowed():
    outcome = ExecutionOutcome(goal="x", steps=(), success=True,
                               verifier_passed=False)
    assert check_success_not_good_learning(outcome, promoted=False)


# --------------------------------------------------------------------------
# RULE_006 — being confident is not being certain
# --------------------------------------------------------------------------

def test_a_strongly_held_hypothesis_stays_a_hypothesis():
    memory = CognitiveMemory(
        tenant_id="t1", memory_type=MemoryType.BELIEF,
        content="Có lẽ khách hàng sẽ gia hạn.", source_event_ids=[],
        confidence=0.99, epistemic_status=EpistemicStatus.HYPOTHESIZED)
    verdict = check_confidence_not_certainty(memory)
    assert verdict, verdict.detail
    assert "hypothesized" in verdict.detail


def test_a_mutant_that_upgrades_on_confidence_fails_rule_006():
    """What a promotion-on-confidence path would produce: a guess wearing the
    status of something the system saw."""
    @dataclass
    class Promoted:
        confidence: float = 0.99
        epistemic_status: str = "very_sure"      # not a real status

    verdict = check_confidence_not_certainty(Promoted())
    assert not verdict, "luật không bắt được trạng thái nhận thức bịa ra"


# --------------------------------------------------------------------------
# the laws are enforced where they say they are
# --------------------------------------------------------------------------

@pytest.mark.parametrize("law_id", list(LawId))
def test_each_law_names_where_it_is_enforced(law_id):
    entry = LAWS[law_id]
    assert entry.enforced_in, f"{law_id} không nói nó được thi hành ở đâu"


def test_the_files_named_by_the_laws_exist():
    """A law pointing at code that has moved is a law nobody is keeping."""
    root = Path(__file__).resolve().parents[1] / "bio_agent_os"
    for entry in LAWS.values():
        for reference in entry.enforced_in:
            if reference.startswith("("):
                continue                      # explicitly not implemented yet
            path = root / reference.split(":")[0]
            assert path.exists(), f"{entry.law_id} trỏ tới {path} không tồn tại"
