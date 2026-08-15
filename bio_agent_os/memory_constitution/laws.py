"""The six laws, their executable invariants, and what each one is guarding.

Four of these were already true of the system before they were written down —
scattered across `claim_lifecycle`, `forgetting` and `models` as individual
decisions. That is exactly why they needed collecting: a rule that lives in one
function's implementation can be removed by a future patch that looks harmless,
and nobody would notice until a benchmark drifted.

Two are new (`SUCCESS != GOOD_LEARNING`, `CONFIDENCE != CERTAINTY`) and guard
mechanisms that do not exist yet. They are written now on purpose: a reflection
engine that learns "what worked" is the natural next step, and the moment to
state that succeeding is not the same as having learned something worth keeping
is *before* anything starts learning from outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class LawId(str, Enum):
    REPETITION_NOT_TRUTH = "RULE_001"
    MEMORY_NOT_EVIDENCE = "RULE_002"
    INFERENCE_NOT_OBSERVATION = "RULE_003"
    RECENT_NOT_CORRECT = "RULE_004"
    SUCCESS_NOT_GOOD_LEARNING = "RULE_005"
    CONFIDENCE_NOT_CERTAINTY = "RULE_006"


#: Bumped whenever a law is added, removed or its meaning changes.
#:
#: Stamped onto every decision. When RULE_007 arrives, a replay has to be able
#: to explain why the same input produced a different outcome last month, and
#: "which laws were in force" is the only honest answer.
CONSTITUTION_VERSION = "1.0.0"


class Outcome(str, Enum):
    """Four states, because two cannot tell silence from consent.

    `NOT_APPLICABLE` means the law has nothing to say about this operation.
    `NOT_EVALUABLE` means it does, and the evidence to decide was missing —
    a checker that errored, a field that was not supplied, a timeout. Folding
    the second into the first is how a law stops being enforced without anyone
    editing it.
    """

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    NOT_EVALUABLE = "not_evaluable"


@dataclass
class Verdict:
    law_id: str
    outcome: Outcome
    detail: str = ""
    constitution_version: str = CONSTITUTION_VERSION

    @property
    def holds(self) -> bool:
        return self.outcome is Outcome.PASS

    def __bool__(self) -> bool:
        """Only PASS is truthy.

        `NOT_EVALUABLE` is deliberately falsy. A caller writing `if verdict:`
        around a dangerous operation gets fail-closed behaviour by default,
        which is the direction an unchecked rule should fail in.
        """
        return self.outcome is Outcome.PASS

    def blocks(self) -> bool:
        """Should this stop the operation? FAIL and NOT_EVALUABLE both do."""
        return self.outcome in (Outcome.FAIL, Outcome.NOT_EVALUABLE)


@dataclass
class Law:
    law_id: LawId
    name: str
    statement: str
    why: str
    #: Takes whatever the law needs and returns a Verdict. Never raises for a
    #: violation — a violation is a result, and a law that crashes cannot be
    #: reported alongside the others.
    invariant: Callable[..., Verdict]
    #: What the system would look like if it broke this law. Used by the tests
    #: to prove the invariant can actually fail.
    mutant_describes: str = ""
    enforced_in: tuple[str, ...] = field(default_factory=tuple)


def _verdict(law_id: LawId, holds: bool, detail: str = "") -> Verdict:
    return Verdict(law_id.value, Outcome.PASS if holds else Outcome.FAIL, detail)


def _cannot_tell(law_id: LawId, detail: str) -> Verdict:
    """The law applies and the evidence to decide it was not there.

    Never `NOT_APPLICABLE`, which would read as "nothing to worry about here".
    """
    return Verdict(law_id.value, Outcome.NOT_EVALUABLE, detail)


# --------------------------------------------------------------------------
# RULE_001 — repetition is not truth
# --------------------------------------------------------------------------

def check_repetition_not_truth(before: Any, after: Any, *,
                               confirmations_before: int = 0,
                               confirmations_after: int = 0) -> Verdict:
    """Restating a claim may make it easier to find. It may not make it truer.

    Measured on this project's own oracle table: `latest_mention`, a system
    that ranks by most-recent mention, keeps 0.933 on current-truth questions
    and falls to 0.533 on historical ones. It is not failing through
    ignorance — it is failing because hearing something again made it believe
    the thing harder. That is the familiarity effect, and a brain has it.
    """
    law_id = LawId.REPETITION_NOT_TRUTH
    problems: list[str] = []

    if confirmations_after <= confirmations_before:
        problems.append("nhắc lại không được ghi nhận — không đo được gì")

    for field_name in ("valid_from", "valid_to"):
        old = getattr(before, field_name, None)
        new = getattr(after, field_name, None)
        if old != new:
            problems.append(
                f"cửa sổ hiệu lực đổi: {field_name} {old!r} -> {new!r}")

    old_confidence = float(getattr(before, "confidence", 0.0) or 0.0)
    new_confidence = float(getattr(after, "confidence", 0.0) or 0.0)
    if new_confidence > old_confidence:
        problems.append(
            f"độ tin cậy tăng chỉ vì được nhắc lại: "
            f"{old_confidence} -> {new_confidence}")

    old_status = getattr(before, "epistemic_status", None)
    new_status = getattr(after, "epistemic_status", None)
    if old_status != new_status:
        problems.append(f"trạng thái nhận thức đổi: {old_status} -> {new_status}")

    return _verdict(law_id, not problems, "; ".join(problems))


# --------------------------------------------------------------------------
# RULE_002 — a memory is not evidence
# --------------------------------------------------------------------------

def check_memory_not_evidence(report: Any) -> Verdict:
    """Holding a record of having checked is not the same as having checked.

    A deletion that reports `verified_clean` having run zero probes is
    asserting a fact about the store from its own memory of intending to look.
    Measured on 2026-08-14: `_probes()` produced nothing at all for four of six
    ordinary Vietnamese sentences, and the report came back clean every time.
    """
    law_id = LawId.MEMORY_NOT_EVIDENCE
    if not hasattr(report, "checks_run") or not hasattr(report, "verified_clean"):
        return _cannot_tell(
            law_id, "báo cáo không mang checks_run hoặc verified_clean — "
                    "không có gì để kiểm, và điều đó KHÔNG phải là đạt")
    checks = int(getattr(report, "checks_run", 0) or 0)
    clean = bool(getattr(report, "verified_clean", False))
    if checks <= 0 and clean:
        return _verdict(law_id, False,
                        "báo sạch trong khi chưa chạy phép kiểm nào")
    return _verdict(law_id, True, f"{checks} phép kiểm đã chạy")


# --------------------------------------------------------------------------
# RULE_003 — an inference is not an observation
# --------------------------------------------------------------------------

def check_inference_not_observation(memories: list[Any]) -> Verdict:
    """What the system worked out must stay distinguishable from what it saw.

    Once the two are stored alike, a chain of plausible reasoning becomes
    indistinguishable from a record of events, and there is no way back: the
    system cannot later ask "did I see this or did I conclude it".
    """
    law_id = LawId.INFERENCE_NOT_OBSERVATION
    missing = [getattr(m, "memory_id", "?") for m in memories
               if not getattr(m, "epistemic_status", None)]
    if missing:
        return _verdict(law_id, False,
                        f"{len(missing)} ký ức không mang trạng thái nhận thức")

    kinds = {str(getattr(m, "epistemic_status", "")) for m in memories}
    return _verdict(law_id, True, f"trạng thái có mặt: {sorted(kinds)}")


# --------------------------------------------------------------------------
# RULE_004 — recent is not correct
# --------------------------------------------------------------------------

def check_recent_not_correct(resolved: Any, *, newest: Any) -> Verdict:
    """What is current is decided by validity, not by what arrived last.

    The store orders by `observed_at` because that is cheap. Current truth is a
    question about a validity window, and the two disagree exactly when
    somebody mentions an old fact again — which is the common case, not the
    edge case.
    """
    law_id = LawId.RECENT_NOT_CORRECT
    if resolved is None:
        return Verdict(law_id.value, Outcome.NOT_APPLICABLE,
                       "không có câu trả lời nào — luật không áp dụng")
    if getattr(resolved, "memory_id", None) == getattr(newest, "memory_id", None):
        window = getattr(resolved, "valid_to", "unset")
        if window is not None and window != "unset":
            return _verdict(
                law_id, False,
                "câu trả lời hiện hành là bản ghi mới nhất, và cửa sổ hiệu lực "
                "của nó đã đóng — đang chọn theo độ mới, không theo hiệu lực")
    return _verdict(law_id, True, "chọn theo cửa sổ hiệu lực")


# --------------------------------------------------------------------------
# RULE_005 — succeeding is not learning something worth keeping
# --------------------------------------------------------------------------

def check_success_not_good_learning(outcome: Any, *,
                                    promoted: bool) -> Verdict:
    """Reaching the goal does not make the route worth repeating.

    Written before the mechanism it constrains exists, deliberately. A
    reflection engine that consolidates "what worked" will otherwise learn
    tricks that happened to succeed — including the dangerous ones, which
    succeed often enough to look like skill. Promotion needs the outcome *and*
    an independent verifier, not the outcome alone.
    """
    law_id = LawId.SUCCESS_NOT_GOOD_LEARNING
    if not hasattr(outcome, "verifier_passed"):
        return _cannot_tell(
            law_id, "kết quả không mang verifier_passed — không xác định được "
                    "có bộ kiểm độc lập nào hay không, nên KHÔNG được cho qua")

    success = bool(getattr(outcome, "success", False))
    verified = bool(getattr(outcome, "verifier_passed", False))
    errors = tuple(getattr(outcome, "errors", ()) or ())

    if not promoted:
        return Verdict(law_id.value, Outcome.NOT_APPLICABLE,
                       "không củng cố gì — luật này không có gì để nói")
    if promoted and not verified:
        return _verdict(law_id, False,
                        "củng cố một quy trình chỉ vì nó đạt mục tiêu, "
                        "chưa có bộ kiểm độc lập nào xác nhận")
    if promoted and errors:
        return _verdict(law_id, False,
                        f"củng cố dù có {len(errors)} lỗi trong quá trình")
    if promoted and not success:
        return _verdict(law_id, False, "củng cố một quy trình đã thất bại")
    return _verdict(law_id, True,
                    f"success={success} verified={verified} promoted={promoted}")


# --------------------------------------------------------------------------
# RULE_006 — being confident is not being certain
# --------------------------------------------------------------------------

def check_confidence_not_certainty(memory: Any) -> Verdict:
    """A high score is not a change of epistemic kind.

    Confidence says how strongly a claim is held. Epistemic status says where
    it came from. A hypothesis believed at 0.99 is still a hypothesis, and any
    code path that promotes one to an observation because the number got high
    has quietly turned a guess into a record of events.
    """
    law_id = LawId.CONFIDENCE_NOT_CERTAINTY
    confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
    status = str(getattr(memory, "epistemic_status", "") or "")
    status = status.split(".")[-1].lower()

    inferred = {"inferred", "hypothesized", "simulated", "reported"}
    observed = {"observed", "verified"}

    if confidence >= 0.9 and status in observed:
        return _verdict(law_id, True, "quan sát trực tiếp, tin cậy cao — hợp lệ")
    if confidence >= 0.9 and status in inferred:
        return _verdict(law_id, True,
                        f"tin cậy {confidence} nhưng vẫn giữ nguyên '{status}'")
    if status not in inferred | observed:
        return _verdict(law_id, False,
                        f"trạng thái nhận thức không nhận ra: {status!r}")
    return _verdict(law_id, True, f"{status} @ {confidence}")


LAWS: dict[LawId, Law] = {
    LawId.REPETITION_NOT_TRUTH: Law(
        LawId.REPETITION_NOT_TRUTH, "REPETITION != TRUTH",
        "Nhắc lại có thể làm ký ức dễ tìm hơn và tươi hơn. Nó KHÔNG được "
        "mở rộng khoảng hiệu lực, tăng độ tin cậy, hay đổi trạng thái nhận "
        "thức khi chưa có bằng chứng mới.",
        "Đo được, và chỉ nêu đúng thứ đo được: một hệ CHỌN THEO LẦN NHẮC GẦN "
        "NHẤT (latest_mention trong bảng oracle) giữ 0.933 ở câu hỏi hiện tại "
        "nhưng tụt còn 0.533 ở câu hỏi lịch sử. Tức là: chọn theo độ mới / "
        "theo số lần nhắc làm hỏng sự thật lịch sử. Cơ chế tâm lý phía sau "
        "(familiarity effect) là động cơ đặt ra luật này, KHÔNG phải thứ phép "
        "đo trên chứng minh.",
        check_repetition_not_truth,
        "reconfirm() cộng thêm vào confidence hoặc dời valid_to",
        ("cognitive/claim_lifecycle.py:reconfirm",)),

    LawId.MEMORY_NOT_EVIDENCE: Law(
        LawId.MEMORY_NOT_EVIDENCE, "MEMORY != EVIDENCE",
        "Một báo cáo chỉ được nói 'sạch' khi đã thực sự đọc lại kho. Không "
        "chạy phép kiểm nào thì không được báo sạch.",
        "Đo được 14/08: _probes() không sinh nổi probe nào cho 4/6 câu tiếng "
        "Việt thông thường, và báo cáo vẫn về 'sạch' mọi lần.",
        check_memory_not_evidence,
        "verified_clean là cờ gán tay thay vì tính từ checks_run",
        ("cognitive/forgetting.py:ForgetReport",
         "cognitive/forget_scope.py:ForgetResult")),

    LawId.INFERENCE_NOT_OBSERVATION: Law(
        LawId.INFERENCE_NOT_OBSERVATION, "INFERENCE != OBSERVATION",
        "Thứ hệ suy ra phải luôn phân biệt được với thứ hệ quan sát thấy.",
        "Khi hai loại lưu giống nhau, một chuỗi suy luận hợp lý trở thành "
        "không phân biệt được với bản ghi sự kiện, và không có đường quay lại.",
        check_inference_not_observation,
        "ghi ký ức mà bỏ trống epistemic_status",
        ("cognitive/models.py:EpistemicStatus",)),

    LawId.RECENT_NOT_CORRECT: Law(
        LawId.RECENT_NOT_CORRECT, "RECENT != CORRECT",
        "Cái gì đang đúng được quyết bởi cửa sổ hiệu lực, không bởi thứ đến "
        "sau cùng.",
        "Hai thứ đó chỉ khác nhau đúng lúc ai đó nhắc lại một sự thật cũ — "
        "và đó là trường hợp thường gặp, không phải trường hợp biên.",
        check_recent_not_correct,
        "trả về bản ghi có observed_at lớn nhất làm câu trả lời hiện hành",
        ("cognitive/temporal_operator.py:claim_history",
         "cognitive/memory_store.py:_valid_at")),

    LawId.SUCCESS_NOT_GOOD_LEARNING: Law(
        LawId.SUCCESS_NOT_GOOD_LEARNING, "SUCCESS != GOOD_LEARNING",
        "Đạt mục tiêu không tự động biến cách làm thành bài học đáng giữ. "
        "Củng cố cần kết quả CỘNG một bộ kiểm độc lập.",
        "Viết trước khi cơ chế nó ràng buộc tồn tại, có chủ đích. Một "
        "reflection engine học 'cái gì đã hiệu quả' sẽ học cả những mẹo nguy "
        "hiểm — chúng thành công đủ thường xuyên để trông như kỹ năng.",
        check_success_not_good_learning,
        "outcome.success == True là đủ để promote một procedure",
        ("(chưa có — ràng buộc Reflection Engine sau này)",)),

    LawId.CONFIDENCE_NOT_CERTAINTY: Law(
        LawId.CONFIDENCE_NOT_CERTAINTY, "CONFIDENCE != CERTAINTY",
        "Điểm tin cậy cao không đổi được LOẠI của tri thức. Một giả thuyết "
        "tin ở mức 0.99 vẫn là giả thuyết.",
        "Bất kỳ đường code nào nâng hypothesis thành observation vì con số đủ "
        "lớn đều đã lặng lẽ biến một phỏng đoán thành bản ghi sự kiện.",
        check_confidence_not_certainty,
        "confidence >= ngưỡng thì tự nâng epistemic_status lên OBSERVED",
        ("cognitive/models.py:EpistemicStatus",)),
}


def law(law_id: LawId) -> Law:
    return LAWS[law_id]


def check_all(**kwargs: Any) -> list[Verdict]:
    """Run whichever invariants have their arguments supplied.

    Deliberately partial: a law with nothing to check is skipped rather than
    passed, so a caller cannot get a clean sheet by supplying nothing.
    """
    import inspect

    out: list[Verdict] = []
    for entry in LAWS.values():
        signature = inspect.signature(entry.invariant)
        needed = {n for n, p in signature.parameters.items()
                  if p.default is inspect.Parameter.empty}
        if needed <= set(kwargs):
            out.append(entry.invariant(**{k: v for k, v in kwargs.items()
                                          if k in signature.parameters}))
    return out


__all__ = [
    "LAWS", "Law", "LawId", "Verdict",
    "check_all", "check_confidence_not_certainty",
    "check_inference_not_observation", "check_memory_not_evidence",
    "check_recent_not_correct", "check_repetition_not_truth",
    "check_success_not_good_learning", "law",
]
