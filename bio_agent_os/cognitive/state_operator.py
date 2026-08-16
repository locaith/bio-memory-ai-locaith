"""`STATE_AT(subject, predicate, time)` — what held in one slot at one moment.

One primitive, two operators:

    CURRENT_STATE = STATE_AT(now)
    TEMPORAL_AT   = STATE_AT(t)

Written as one because they are one. Two implementations of "which claim was
valid when" drift apart, and this project has already paid for that twice: the
lifecycle engine and `claim_history` each derived supersession independently,
and an A/B that closed 39 validity windows scored identically to the arm that
closed none, because the operator being measured re-derived everything itself.

The failure matrix of 16/08 is why this exists at all:

    EXISTS         40 asked   40 correct   1.0000
    RECALL         58 asked   45 correct   0.7759
    TEMPORAL_AT    38 asked   23 correct   0.6053

Fifty-eight questions — 43% of the traffic — asked "what is true now" and were
answered by ranking the whole store and hoping. That is precisely where EVER
sat the day before it went from 0.4750 to 1.0000 by being given an operation
to perform.

**Four answers, and three of them are not a value.** Forcing every question to
produce one is how a memory ends up confidently reciting last year's job
title:

    KNOWN          a claim holds at this instant
    UNKNOWN        the slot has no claim covering it
    CONFLICT       two claims compete and nothing here settles it
    NOT_AVAILABLE  there was a claim and it was deleted

The invariants, each of which is a way the obvious implementation goes wrong:

    last mentioned != currently valid
        A restatement of an old value is the most recent sentence about the
        slot. Ranking by recency answers with it.

    reinforcement != current truth
        A claim repeated eight times outscores the one that replaced it.

    a conflict is not resolved by picking the higher score
        Two sources disagreeing is a state to report, not a tie to break.

    a deleted claim does not return because it looks similar
        Nothing here reads content by similarity. The slot is read by subject
        and predicate, and a deletion removes the row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .aspect_resolver import Predicate, resolve_frame

KNOWN = "known"
UNKNOWN = "unknown"
CONFLICT = "conflict"
NOT_AVAILABLE = "not_available"


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip()


@dataclass
class StateAt:
    """What held, and enough to argue with it."""

    status: str = UNKNOWN
    executed: bool = False
    subject: str = ""
    predicate: str = ""
    at: str = ""
    #: The claims valid at `at`. More than one is legitimate for a predicate
    #: that holds several values; more than one for a single-valued predicate
    #: is a CONFLICT.
    holding: list[Any] = field(default_factory=list)
    #: Claims that competed and were not settled. Reported, never dropped.
    competing: list[str] = field(default_factory=list)
    stage_failed: str = ""
    note: str = ""

    @property
    def content(self) -> str:
        return str(self.holding[0].content) if self.holding else ""

    @property
    def memory_ids(self) -> list[str]:
        return [str(s.memory_id) for s in self.holding]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "executed": self.executed,
                "subject": self.subject, "predicate": self.predicate,
                "at": self.at, "memory_ids": self.memory_ids,
                "competing": self.competing,
                "stage_failed": self.stage_failed, "note": self.note}


def _hedged(content: str) -> bool:
    from .relations import ALTERNATIVE_SOURCE_MARKERS

    folded = _fold(content).lower()
    return any(marker in folded for marker in ALTERNATIVE_SOURCE_MARKERS)


def state_at(memory_os: Any, *, subject: str, predicate: Predicate,
             at: str | None, context: Any) -> StateAt:
    """Which claim held in this slot at this instant.

    `at=None` means now — the caller has no particular moment in mind, so the
    latest interval wins. Not the latest *mention*: the spans come from
    `claim_history`, which folds restatements into the claim they restate.
    """
    from .temporal_operator import claim_history

    answer = StateAt(subject=subject, predicate=predicate.attribute or "",
                     at=str(at or ""))
    if not subject:
        answer.stage_failed = "subject"
        answer.note = "không rút được tên riêng nào"
        return answer
    if predicate is Predicate.UNKNOWN:
        answer.stage_failed = "predicate"
        answer.note = "không xác định được vị từ"
        return answer

    spans = claim_history(memory_os, subject=subject,
                          aspect=predicate.attribute,
                          predicate=predicate.attribute, context=context)
    if not spans:
        # STRUCTURED_GAP: this person has memories, but none in this slot.
        # That is the one condition under which a mis-slotted row is worth
        # hunting for — and it is a condition about the *answer*, not about
        # the existence of rows that disagree.
        #
        # `NO_SUBJECT_HISTORY` is a different state and must not trigger a
        # rescue: nothing is known about this person, so there is nothing for
        # a bounded search to be bounded by.
        spans = claim_history(memory_os, subject=subject,
                              aspect=predicate.attribute,
                              predicate=predicate.attribute, context=context,
                              rescue=True)
    if not spans:
        # No record at all, which is different from a record that says
        # nothing holds. A deletion leaves this state too — the row is gone
        # and nothing distinguishes "never told" from "told and removed"
        # without reading a tombstone, so the honest answer is UNKNOWN and
        # the note says which two things it cannot separate.
        answer.stage_failed = "history"
        answer.note = (f"không có ký ức nào về ({subject}, {predicate.name}) "
                       f"— chưa từng được kể, hoặc đã bị xoá")
        return answer

    answer.executed = True

    live = []
    for span in spans:
        if span.retracted_by:
            # CORRECTED: the claim was never true. It holds at no instant, by
            # construction — `valid_to == valid_from` leaves an empty window.
            continue
        if _hedged(span.content):
            # An unresolved second source. Not a claim the system holds.
            answer.competing.append(str(span.content))
            continue
        if not _holds_at(span, at):
            continue
        live.append(span)

    if not live:
        if answer.competing:
            answer.status = CONFLICT
            answer.note = ("chỉ còn các phát biểu từ nguồn khác chưa được "
                           "phân xử")
            return answer
        answer.status = UNKNOWN
        answer.note = (f"slot có {len(spans)} claim nhưng không claim nào "
                       f"còn hiệu lực vào {at or 'hiện tại'}")
        return answer

    answer.holding = live
    if len(live) > 1 and _single_valued(predicate):
        # Two claims holding at once in a slot that takes one value is a
        # disagreement, and picking the higher-scoring one is exactly the
        # silent resolution the constitution forbids.
        answer.status = CONFLICT
        answer.competing.extend(str(s.content) for s in live)
        answer.note = f"{len(live)} claim cùng hiệu lực trong một slot đơn trị"
        return answer

    answer.status = KNOWN
    return answer


def _single_valued(predicate: Predicate) -> bool:
    from .relations import Cardinality, semantics_for

    return semantics_for(predicate).cardinality is Cardinality.ONE


def _holds_at(span: Any, at: str | None) -> bool:
    """Half-open window: `valid_from <= at < valid_to`.

    With `at=None` the question is "does this still hold", which is the same
    window with an open right edge.
    """
    valid_to = getattr(span, "valid_to", None)
    if at is None:
        return valid_to is None
    valid_from = getattr(span, "valid_from", None)
    if valid_from is not None and str(valid_from) > str(at):
        return False
    return valid_to is None or str(at) < str(valid_to)


def answer_current(memory_os: Any, question: str, *, context: Any,
                   at: str | None = None) -> StateAt:
    """`CURRENT_STATE` — `STATE_AT` with the moment left open.

    Kept as a thin wrapper rather than a second implementation, so that a fix
    to interval selection reaches both operators at once. That is the whole
    reason the primitive is shaped this way.
    """
    frame = resolve_frame(question)
    return state_at(memory_os, subject=frame.subject or "",
                    predicate=frame.predicate, at=at, context=context)


__all__ = ["CONFLICT", "KNOWN", "NOT_AVAILABLE", "StateAt", "UNKNOWN",
           "answer_current", "state_at"]
