"""«Đã từng ... chưa?» — an existence test over a claim history.

    "Phạm Vy đã từng có chức vụ là nhân viên kinh doanh đúng không?"

is not a similarity question. It is:

    EXISTS claim WHERE subject = Phạm Vy
                   AND predicate = ROLE
                   AND value = 'nhân viên kinh doanh'
                   AND asserted_at <= now
                   AND NOT retracted

Answering it by ranking the whole store and hoping the right sentence lands in
the top six is what the system did until now, and the measurement says what
that costs: the EVER family answered 19 of 40 with a model attached, against
32 of 40 for present-tense questions on the same store.

The failure mode is specific and was watched happening. On the answer-layer
A/B of 16/08, one arm returned "Nhắc lại, phạm Giang làm việc tại công ty
Locaith" — the *only* evidence for the question — and the other did not,
because 232 reconfirmations had each added up to 0.25 to other memories'
ranking scores and pushed it out of the window. The evidence was in the store
in both arms. Only one of them handed it over.

This is the same lesson the temporal operator taught in August: **classifying
a question is not answering it.** `_EVER` was added to the query planner and
408 model calls later the numbers had not moved by a single character,
because recognising the intent and then falling back to generic top-k is
still generic top-k.

Three answers, and the third is not a failure:

    YES      a claim with this value existed and was not retracted
    NO       the slot has a history and this value is not in it
    UNKNOWN  no history for this slot, or the value could not be read out

UNKNOWN over NO where nothing is known. "I have no record of that" and "that
never happened" are different sentences, and a memory that cannot tell them
apart will eventually deny something true with confidence.

A retracted claim answers NO on purpose. `CORRECT` means the system was
misinformed — the value was never true — so "was it ever?" is answered by the
correction, not by the sentence it corrected. A superseded claim answers YES:
it *was* true, and that is exactly what the question asks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .aspect_resolver import Predicate, resolve_frame

#: The shapes a yes/no history question takes, and where the value sits.
#:
#: Deliberately narrow. A pattern that matches loosely will claim questions it
#: cannot answer, and this operator's whole value is that it either executes or
#: says it did not — a wrong confident YES is worse than the generic search it
#: replaces.
_VALUE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\blà\s+(?P<value>.+?)\s*(?:đúng không|phải không|không)?\s*[?.]?$",
               re.IGNORECASE),
    re.compile(r"\b(?:từng|đã từng)\s+(?:là|làm|ở|sống ở|giữ chức)\s+"
               r"(?P<value>.+?)\s*(?:đúng không|phải không|không)?\s*[?.]?$",
               re.IGNORECASE),
    re.compile(r"\bever\s+(?:been|had|worked at|lived in)\s+(?P<value>.+?)\s*\??$",
               re.IGNORECASE),
)

#: Words that end a value and are never part of one.
_TRAILING = re.compile(r"\s*(đúng không|phải không|không|chưa|hay chưa)\s*$",
                       re.IGNORECASE)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip()


def _hedged(folded_content: str) -> bool:
    """Is this claim attributed to a competing, unresolved source?

    Uses `relations.ALTERNATIVE_SOURCE_MARKERS` rather than a second list.
    Two marker tables meaning the same thing drift, and the drift shows up as
    one module treating a rumour as a fact while another does not.
    """
    from .relations import ALTERNATIVE_SOURCE_MARKERS

    return any(marker in folded_content
               for marker in ALTERNATIVE_SOURCE_MARKERS)


@dataclass
class EverAnswer:
    """What the operator concluded, and enough to argue with it."""

    verdict: str = "unknown"          # "yes" | "no" | "unknown"
    executed: bool = False
    subject: str = ""
    predicate: str = ""
    value: str = ""
    #: The claim that settles it, when one does.
    evidence_memory_id: str = ""
    evidence_content: str = ""
    #: Every value the slot has ever held, so a NO can be checked.
    history: list[str] = field(default_factory=list)
    stage_failed: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "executed": self.executed,
                "subject": self.subject, "predicate": self.predicate,
                "value": self.value,
                "evidence_memory_id": self.evidence_memory_id,
                "history": self.history, "stage_failed": self.stage_failed,
                "note": self.note}


def parse_ever(question: str) -> tuple[str | None, Predicate, str]:
    """(subject, predicate, value) from an existence question.

    Reuses `resolve_frame` for the first two rather than reimplementing name
    and predicate extraction. Two implementations of the same rule drift, and
    the drift is invisible until a question is answered from the wrong slot.
    """
    frame = resolve_frame(question)
    text = _fold(question)

    value = ""
    for pattern in _VALUE_PATTERNS:
        match = pattern.search(text)
        if match:
            value = _TRAILING.sub("", _fold(match.group("value")))
            break

    # The subject's own name is never the value. "Trần Hà đã từng là Trần Hà"
    # is not a question anyone asks, but a greedy pattern will produce it.
    if value and frame.subject and _fold(value).lower() == _fold(frame.subject).lower():
        value = ""
    return frame.subject, frame.predicate, value


def answer_ever(memory_os: Any, question: str, *, context: Any,
                as_of: str | None = None) -> EverAnswer:
    """Run the existence test, or say which stage stopped it.

    Every early return names a stage. A question this could not answer must be
    distinguishable from one it answered NO, or the fallback rate cannot be
    measured and the operator's coverage is a guess.
    """
    from .temporal_operator import claim_history

    answer = EverAnswer()
    subject, predicate, value = parse_ever(question)
    answer.subject = subject or ""
    answer.predicate = predicate.attribute or ""
    answer.value = value

    if not subject:
        answer.stage_failed = "subject"
        answer.note = "không rút được tên riêng nào từ câu hỏi"
        return answer
    if predicate is Predicate.UNKNOWN:
        answer.stage_failed = "predicate"
        answer.note = "không xác định được vị từ — một từ thông dụng đơn lẻ "
        return answer
    if not value:
        answer.stage_failed = "value"
        answer.note = "không rút được giá trị cần kiểm tra"
        return answer

    # `hint` is the value being asked about. It names the slot precisely where
    # the predicate alone is weak — the same job the anchor does for a
    # before/after question, and the reason `claim_history` takes one.
    spans = claim_history(memory_os, subject=subject,
                          aspect=predicate.attribute, context=context,
                          hint=value)
    if not spans:
        answer.stage_failed = "history"
        answer.note = f"không có ký ức nào về ({subject}, {predicate.name})"
        return answer

    answer.executed = True
    wanted = _fold(value).lower()
    for span in spans:
        content = _fold(span.content).lower()
        if wanted not in content:
            continue
        answer.history.append(span.content)
        if _hedged(content):
            # "Theo một nguồn khác, phạm Nam phụ trách dự án cổng thanh toán"
            # is a second source disagreeing, not a record that something was
            # once the case. Measured: one of four wrong answers was a YES
            # built entirely on a sentence the world emitted as an unresolved
            # contradiction — the store had never held that claim as true.
            #
            # The same taxonomy the relation classifier uses, imported rather
            # than restated: one marker list, one meaning.
            continue
        if span.retracted_by:
            # The system was told this was never true. "Was it ever?" is
            # answered by the correction, not by what it corrected.
            continue
        if as_of and str(span.observed_at) > str(as_of):
            # Not knowable yet at the moment asked. Answering YES here grades
            # the system on information it did not have — the shape of defect
            # that produced three rounds of unanswerable benchmark questions.
            continue
        answer.verdict = "yes"
        answer.evidence_memory_id = span.memory_id
        answer.evidence_content = span.content
        return answer

    if answer.history:
        answer.verdict = "no"
        answer.note = ("giá trị này chỉ xuất hiện trong các claim đã bị đính "
                       "chính, hoặc chưa biết được vào lúc hỏi")
        return answer

    answer.verdict = "no"
    answer.note = (f"slot ({subject}, {predicate.name}) có {len(spans)} claim "
                   f"và không claim nào mang giá trị {value!r}")
    return answer


__all__ = ["EverAnswer", "answer_ever", "parse_ever"]
