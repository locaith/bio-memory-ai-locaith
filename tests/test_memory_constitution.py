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


def test_four_outcomes_are_distinguishable():
    """"Not applicable" and "could not tell" must never be the same value.

    The first means the law has nothing to say. The second means it does and
    the evidence was missing — which is how a rule stops being enforced without
    anyone editing it.
    """
    from bio_agent_os.memory_constitution import Outcome

    assert {o.value for o in Outcome} == {
        "pass", "fail", "not_applicable", "not_evaluable"}


def test_only_pass_is_truthy():
    """`if verdict:` around a dangerous operation must fail closed."""
    from bio_agent_os.memory_constitution import Outcome, Verdict

    assert bool(Verdict("R", Outcome.PASS))
    assert not bool(Verdict("R", Outcome.FAIL))
    assert not bool(Verdict("R", Outcome.NOT_EVALUABLE))
    assert not bool(Verdict("R", Outcome.NOT_APPLICABLE))


def test_not_evaluable_blocks_and_not_applicable_does_not():
    from bio_agent_os.memory_constitution import Outcome, Verdict

    assert Verdict("R", Outcome.NOT_EVALUABLE).blocks()
    assert Verdict("R", Outcome.FAIL).blocks()
    assert not Verdict("R", Outcome.NOT_APPLICABLE).blocks()
    assert not Verdict("R", Outcome.PASS).blocks()


def test_every_verdict_carries_the_constitution_version():
    """RULE_007 will one day change what the same input decides. Only the
    version explains that in a replay."""
    from bio_agent_os.memory_constitution import CONSTITUTION_VERSION

    report = type("R", (), {"checks_run": 2, "verified_clean": True})()
    verdict = check_memory_not_evidence(report)
    assert verdict.constitution_version == CONSTITUTION_VERSION
    assert CONSTITUTION_VERSION.count(".") == 2


def test_a_report_missing_the_fields_is_not_evaluable_not_a_pass():
    """The fail-closed case, on the law that guards deletion claims."""
    from bio_agent_os.memory_constitution import Outcome

    verdict = check_memory_not_evidence(object())
    assert verdict.outcome is Outcome.NOT_EVALUABLE, verdict
    assert verdict.blocks()


def test_an_outcome_missing_its_verifier_is_not_evaluable():
    from bio_agent_os.memory_constitution import Outcome

    verdict = check_success_not_good_learning(object(), promoted=True)
    assert verdict.outcome is Outcome.NOT_EVALUABLE
    assert verdict.blocks()


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


def test_not_promoting_is_not_applicable_rather_than_a_pass():
    """A law with nothing to say must not report approval.

    `NOT_APPLICABLE` is falsy on purpose, so `if verdict:` around a dangerous
    operation fails closed. What it must not do is *block* — the law genuinely
    has no opinion when nothing is being consolidated.
    """
    from bio_agent_os.memory_constitution import Outcome

    outcome = ExecutionOutcome(goal="x", steps=(), success=True,
                               verifier_passed=False)
    verdict = check_success_not_good_learning(outcome, promoted=False)
    assert verdict.outcome is Outcome.NOT_APPLICABLE
    assert not verdict.blocks()
    assert not bool(verdict), "không áp dụng mà vẫn đọc ra 'đạt'"


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

# --------------------------------------------------------------------------
# property: RULE_001 holds for every number of repetitions, not just one
# --------------------------------------------------------------------------

@pytest.mark.parametrize("times", [1, 2, 3, 5, 8, 13, 50, 200])
def test_rule_001_holds_for_any_number_of_repetitions(os_, times):
    """Stated as a property, because the interesting failure is a slope.

    An example test with one repetition cannot see a mechanism that adds a
    little confidence each time — the drift only becomes visible after enough
    of them, which is exactly the shape of a familiarity effect.
    """
    from bio_agent_os.cognitive.claim_lifecycle import reconfirm

    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="CTO là An.", workspace_id="w1",
                        valid_from=JAN, observed_at=JAN)
    stored = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="CTO là An.", confidence=0.7)
    before = os_.memories.latest(stored.memory_id, "t1")

    for index in range(times):
        reconfirm(os_, memory_id=stored.memory_id,
                  at=f"2026-08-{(index % 28) + 1:02d}T00:00:00+00:00",
                  actor="tuananh")

    after = os_.memories.latest(stored.memory_id, "t1")
    verdict = check_repetition_not_truth(
        before, after,
        confirmations_before=before.reinforcement_count,
        confirmations_after=after.reinforcement_count)

    assert verdict, f"sau {times} lần nhắc: {verdict.detail}"
    assert after.reinforcement_count == before.reinforcement_count + times
    assert after.confidence == before.confidence
    assert after.valid_from == before.valid_from
    assert after.valid_to == before.valid_to


@pytest.mark.parametrize("times", [1, 5, 40])
def test_a_mutant_that_creeps_upward_is_caught_at_every_scale(times):
    """A mechanism adding 0.005 per mention is invisible once and obvious at
    forty. The law has to catch it at both."""
    before = CognitiveMemory(tenant_id="t1", memory_type=MemoryType.SEMANTIC,
                             content="x", source_event_ids=[], confidence=0.7,
                             valid_from=JAN)
    crept = replace(before, confidence=min(0.7 + 0.005 * times, 1.0))

    verdict = check_repetition_not_truth(before, crept,
                                         confirmations_before=0,
                                         confirmations_after=times)
    assert not verdict, f"trôi {0.005 * times:.3f} sau {times} lần mà không bắt được"


# --------------------------------------------------------------------------
# the decision log
# --------------------------------------------------------------------------

def test_a_gated_operation_is_recorded_with_who_why_and_which_version(os_):
    from bio_agent_os.memory_constitution import decisions, gate

    conn = os_.memories.conn
    report = type("R", (), {"checks_run": 3, "verified_clean": True})()
    gate(conn, [check_memory_not_evidence(report)],
         operation="forget_derived", actor="tuananh", input_refs=["m-1"])

    logged = decisions(conn)
    assert len(logged) == 1
    entry = logged[0]
    assert entry["rule_id"] == LawId.MEMORY_NOT_EVIDENCE.value
    assert entry["operation"] == "forget_derived"
    assert entry["decision"] == "pass"
    assert entry["actor"] == "tuananh"
    assert entry["constitution_version"]
    assert entry["input_refs"] == ["m-1"]
    assert entry["at"]


def test_a_refused_operation_raises_and_is_recorded(os_):
    from bio_agent_os.memory_constitution import (
        ConstitutionViolation, decisions, gate,
    )

    conn = os_.memories.conn
    liar = type("R", (), {"checks_run": 0, "verified_clean": True})()
    with pytest.raises(ConstitutionViolation):
        gate(conn, [check_memory_not_evidence(liar)],
             operation="forget_derived", actor="tuananh")

    logged = decisions(conn, rule_id=LawId.MEMORY_NOT_EVIDENCE.value)
    assert logged and logged[-1]["decision"] == "fail"
    assert logged[-1]["reason"], "chặn mà không ghi lý do"


def test_an_unevaluable_law_blocks_the_operation(os_):
    """Fail-closed. A checker that could not decide has not approved anything."""
    from bio_agent_os.memory_constitution import ConstitutionViolation, gate

    with pytest.raises(ConstitutionViolation):
        gate(os_.memories.conn, [check_memory_not_evidence(object())],
             operation="forget_derived", actor="tuananh")


def test_a_not_applicable_law_does_not_block(os_):
    from bio_agent_os.memory_constitution import gate

    outcome = ExecutionOutcome(goal="x", steps=(), success=True,
                               verifier_passed=False)
    gate(os_.memories.conn,
         [check_success_not_good_learning(outcome, promoted=False)],
         operation="reflect", actor="tuananh")


def test_the_decision_log_holds_no_memory_content(os_):
    """Written on every gated operation, so anything quoted in it is one more
    place a deletion has to reach."""
    from bio_agent_os.memory_constitution import decisions, gate

    conn = os_.memories.conn
    report = type("R", (), {"checks_run": 1, "verified_clean": True})()
    gate(conn, [check_memory_not_evidence(report)],
         operation="forget_derived", actor="tuananh",
         input_refs=["mem-abc123"])

    blob = str(decisions(conn))
    for secret in ("88888888", "Mật khẩu", "CTO là An"):
        assert secret not in blob


def test_recording_a_decision_needs_an_actor(os_):
    from bio_agent_os.memory_constitution import record

    report = type("R", (), {"checks_run": 1, "verified_clean": True})()
    with pytest.raises(ValueError):
        record(os_.memories.conn, check_memory_not_evidence(report),
               operation="forget_derived", actor="")


def test_the_real_deletion_path_goes_through_the_gate(os_):
    """A constitution that only exists in its own test file constrains nothing.

    The rule has to run where the operation runs, or it holds only until
    somebody edits that function.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.memory_constitution import decisions

    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Khách hàng An Phát có số 0912345678.",
                        workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                 content="Khách hàng An Phát có số 0912345678.",
                 confidence=0.9)

    forget_scoped(os_, "xoá mọi thứ về An Phát", actor="tuananh")

    logged = decisions(os_.memories.conn, operation="forget_scoped")
    assert logged, "đường xoá thật không đi qua constitution gate"
    assert logged[-1]["rule_id"] == LawId.MEMORY_NOT_EVIDENCE.value
    assert logged[-1]["actor"] == "tuananh"


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
