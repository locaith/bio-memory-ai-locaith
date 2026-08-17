"""A dated question is a lookup on a validity interval, not a search.

Measured on the first real lifetime run, 15/08/2026:

    29 of 55 failures were HISTORICAL
    22 of those returned nothing at all
    planner route, every one:  temporal -> recall

The classifier was right every single time. `_CLASSES_FOR` had no entry for
TEMPORAL, so the question fell through to vector search. A wiring bug, sitting
in the gap between knowing what to do and doing it.

"Vào ngày 2025-01-04, Phạm Vy đang giữ chức vụ gì?" is not answerable by
similarity: three memories mention that person's job, all of them relevant, and
the one that answers is chosen by *when*, not by *how alike*.

    parse constraint -> resolve subject + aspect -> claim history
    -> filter by interval -> apply lifecycle semantics -> answer + provenance

Every stage reports whether it succeeded, so `temporal_execution_accuracy` can
say *where* an answer was lost. Without that, the next investigation is another
twenty-nine traces read by hand.

INTERVALS ARE DERIVED AT READ TIME, on purpose. The store holds free text with
an `observed_at`, and nothing yet detects supersession at write time — that is
the next piece of work. Sorting one slot's claims by when they were observed
gives each an interval ending where the next begins, which is enough to answer
a dated question today and costs nothing to replace later.

TWO INVARIANTS, designed in rather than patched on:

**A restatement is not a new interval.** A fact from tick 100 repeated at tick
700 must not create a boundary at 700, or a question about tick 500 resolves
through a mention rather than through validity. That is precisely the mistake
`latest_mention` makes in the oracle table, where it keeps 0.933 on current
questions and drops to 0.533 on historical ones.

**A correction is not a supersession.** "sinh năm 1990" then "xin lỗi, 1991":

    world truth      -> 1991 always; 1990 was never true of the world
    belief history   -> the system did believe 1990 in between, and can say so

One answers about the world, the other about the system. Merging them answers a
question about the world with a belief the world never held, and it looks
exactly like working temporal reasoning.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

#: Markers that make a sentence a restatement of something already said.
_RESTATEMENT = re.compile(
    r"^\s*(nhắc lại|như đã nói|xin nhắc lại|lặp lại|again|as (i|we) said)\s*[,:.]?\s*",
    re.IGNORECASE)

#: Markers that make a sentence a retraction of what came before.
_CORRECTION = re.compile(
    r"(đính chính|xin lỗi[,;]?\s*(là|thông tin)?|nói lại|thực ra|thật ra"
    r"|thông tin trước (là )?sai|correction|actually|i was wrong)",
    re.IGNORECASE)

#: Asking what the *system* believed, as opposed to what was true.
_BELIEF_MODE = re.compile(
    r"(hệ thống (tin|nghĩ|cho rằng|ghi nhận)|em (tin|nghĩ)|lúc đó (bạn|em|hệ) "
    r"|what did you (think|believe)|you believed)", re.IGNORECASE)

_BEFORE = re.compile(r"(trước khi|trước lúc|trước đó thì|before\b)", re.IGNORECASE)
_AFTER = re.compile(r"(sau khi|sau lúc|after\b)", re.IGNORECASE)
#: What to probe the store with, once a predicate is known.
#:
#: Built from the words a *memory* would use, not the words a question uses.
#: "chức vụ" appears in questions; "đang giữ chức trưởng nhóm" is what is
#: stored, so the probe carries both sides of the vocabulary.
_ASPECT_PROBE: dict = {}


def _build_aspect_probes() -> None:
    from .aspect_resolver import ONTOLOGY, Predicate

    stored = {
        Predicate.EMPLOYER: ("làm việc tại công ty", "công ty"),
        Predicate.ROLE: ("đang giữ chức", "chức"),
        Predicate.LOCATION: ("sống ở", "sống"),
        Predicate.PHONE: ("số điện thoại của", "điện thoại"),
        Predicate.BIRTHDAY: ("sinh ngày", "sinh"),
        Predicate.PROJECT: ("phụ trách dự án", "dự án"),
        Predicate.SALARY: ("lương của", "lương"),
    }
    for predicate, phrases in ONTOLOGY.items():
        clean = tuple(p for p in phrases if "." not in p)
        _ASPECT_PROBE[predicate] = stored.get(predicate, ()) + clean[:4]


_HISTORY = re.compile(
    r"(những lần nào|mấy lần|bao nhiêu lần|đã (từng )?(đổi|thay đổi|chuyển)"
    r"|lịch sử|history of|how many times|list the changes)", re.IGNORECASE)
_CURRENT = re.compile(
    r"(hiện tại|bây giờ|hiện nay|đang|lúc này|currently|right now|now\b)",
    re.IGNORECASE)

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MY = re.compile(r"tháng\s*(\d{1,2})\s*[/-]?\s*(\d{4})", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")

_TITLES = {"anh", "chị", "em", "ông", "bà", "cô", "chú", "bác", "mr", "mrs", "ms"}
_STOP = {
    "vào", "ngày", "tháng", "năm", "hồi", "lúc", "khi", "thì", "là", "gì",
    "nào", "đâu", "ở", "của", "đang", "hiện", "tại", "bây", "giờ", "trước",
    "sau", "đã", "từng", "những", "lần", "bao", "nhiêu", "mấy", "và", "có",
    "hệ", "thống", "tin", "cho", "rằng", "sang", "rời", "chuyển", "đổi",
    "in", "on", "at", "the", "was", "were", "is", "did", "what", "where",
    "which", "who", "how", "many", "times", "currently", "now", "before",
    "after", "system", "believe", "believed", "think", "you",
}


class TemporalKind(str, Enum):
    POINT_IN_TIME = "point_in_time"
    BEFORE = "before"
    AFTER = "after"
    HISTORY = "history"
    CURRENT = "current"


@dataclass
class TemporalIntent:
    kind: TemporalKind = TemporalKind.CURRENT
    when: str | None = None
    #: The named event a "before"/"after" question hangs on, e.g. "Bình Minh".
    anchor: str | None = None
    subject: str | None = None
    #: The text used to probe for the right slot. Derived from `predicate`
    #: where one was identified, so it carries the predicate's whole
    #: vocabulary instead of whichever word survived stopword removal.
    aspect: str | None = None
    #: The normalised predicate, or "UNKNOWN". This is the part a store can be
    #: asked with; `aspect` is only its surface form.
    predicate: str = "UNKNOWN"
    mode: str = "world"           # "world" or "belief"


@dataclass
class ClaimSpan:
    memory_id: str
    content: str
    #: When this became true of the world. A correction reaches back and takes
    #: the validity of the claim it retracts, because the corrected value was
    #: never true and something has to hold that interval.
    valid_from: str
    #: When the system was told. Different from `valid_from` exactly where a
    #: correction is involved, and that difference is the whole belief/world
    #: distinction: the world was always 1991, the system believed 1990 until
    #: February. Answering "what did you think then" from `valid_from` returns
    #: the correction, which the system did not yet have.
    observed_at: str = ""
    valid_to: str | None = None
    kind: str = "asserted"        # asserted | corrected | superseded
    confirmations: int = 0
    #: Set when a later claim retracted this one rather than replacing it.
    retracted_by: str | None = None
    #: This claim arrived as somebody else's competing account — "theo một
    #: nguồn khác" — so it disputes rather than resolves.
    #:
    #: It is still a claim and still has a span: hiding it would pick the other
    #: side just as silently as closing the earlier claim against it did. What
    #: it may not do is act as an interval boundary, because nothing about a
    #: disagreement establishes that the earlier claim stopped holding.
    disputed: bool = False

    def holds_at(self, when: str) -> bool:
        if self.retracted_by:
            return False
        if when < self.valid_from:
            return False
        return self.valid_to is None or when < self.valid_to


@dataclass
class ConflictState:
    """Two accounts of a one-at-a-time fact, and nothing that decides between.

    This is a **result**, not a failure, and it is deliberately not a value.
    The invariant in `claim_history` established that an unresolved relation
    may not close an interval; the consequence is that both claims stay live,
    and something has to say what that means. Leaving it implicit produced an
    answer that read as a single value with a second sentence attached — right
    instinct, no contract, and nothing a caller could branch on.

    What it is not:

        not the older claim      choosing it is the same silent decision,
                                 pointed the other way
        not the newer claim      arrival order is not evidence
        not a refusal            "I don't know" throws away two facts the
                                 system does hold, and a refusal cannot be
                                 distinguished from having no memory at all

    `world_truth_known` is false by construction: the ledger may well hold a
    value, and the system cannot see which. That is the whole distinction —
    a system can be **oracle-world wrong and epistemically correct**, and the
    two must be scored separately or the second will be optimised away.
    """
    predicate: str = ""
    when: str = ""
    claims: list[ClaimSpan] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    #: Why these cannot both be true: the predicate holds one value at a time.
    reason: str = "single_valued_predicate_with_competing_claims"
    #: What would settle it. Recorded so the state is actionable rather than
    #: merely honest.
    resolvable_by: str = "a relation that resolves — SUPERSEDE or CORRECT"
    world_truth_known: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": "CONFLICT",
            "predicate": self.predicate,
            "when": self.when,
            "reason": self.reason,
            "resolvable_by": self.resolvable_by,
            "world_truth_known": self.world_truth_known,
            "claims": [{"memory_id": str(c.memory_id), "content": c.content,
                        "observed_at": c.observed_at,
                        "valid_from": c.valid_from, "valid_to": c.valid_to,
                        "disputed": c.disputed} for c in self.claims],
            "provenance": self.provenance,
        }


@dataclass
class TemporalAnswer:
    executed: bool = False
    answer_text: str = ""
    spans: list[ClaimSpan] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    #: Set when the operator reached CONFLICT instead of a value. `answer_text`
    #: is then rendered from this deterministically — no model involved, since
    #: the operator already knows exactly what it does and does not know.
    conflict: ConflictState | None = None
    intent: TemporalIntent = field(default_factory=TemporalIntent)
    mode: str = "world"
    stage_failed: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"executed": self.executed, "kind": self.intent.kind.value,
                "mode": self.mode, "answer": self.answer_text[:200],
                "spans": len(self.spans), "stage_failed": self.stage_failed,
                "note": self.note}


# --------------------------------------------------------------------------
# stage 1 — parse the constraint
# --------------------------------------------------------------------------

def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


def _extract_when(text: str) -> str | None:
    match = _ISO.search(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T00:00:00+00:00"
    match = _DMY.search(text)
    if match:
        day, month, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}T00:00:00+00:00"
    match = _MY.search(text)
    if match:
        month, year = match.groups()
        return f"{year}-{int(month):02d}-01T00:00:00+00:00"
    match = _YEAR.search(text)
    if match:
        return f"{match.group(1)}-01-01T00:00:00+00:00"
    return None


def _subject_of(text: str, exclude: str | None = None) -> str | None:
    """The longest run of capitalised words that is not a title.

    Vietnamese names are capitalised per syllable, so a run of them is a name.

    `exclude` drops a name already claimed by something else. "Trước khi sang
    Bình Minh thì Bùi Cường làm ở đâu" holds two two-word names, and taking the
    longest run picked the anchor — the question was then answered about the
    wrong person, or about a company as though it were one.
    """
    skip = {w.lower() for w in (exclude or "").split()}
    runs: list[list[str]] = []
    current: list[str] = []
    for token in re.findall(r"[\w]+", _fold(text), re.UNICODE):
        if token[:1].isupper() and token.lower() not in _TITLES \
                and token.lower() not in _STOP and token.lower() not in skip:
            current.append(token)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return " ".join(max(runs, key=len)) if runs else None


def _aspect_of(text: str, subject: str | None,
               anchor: str | None = None) -> str | None:
    """What the question is about, once the names are taken out.

    The anchor has to go too, not just the subject. "Trước khi sang Bình Minh
    thì Bùi Cường làm ở đâu" left an aspect of "bình minh làm", which then
    matched a birthday memory more closely than an employment one — the
    question was routed correctly, resolved to the right person, and still
    answered from the wrong slot.
    """
    drop = {w.lower() for w in (subject or "").split()}
    drop |= {w.lower() for w in (anchor or "").split()}
    words = [w for w in re.findall(r"[\w]+", _fold(text).lower(), re.UNICODE)
             if w not in _STOP and w not in drop
             and w not in _TITLES and not w.isdigit() and len(w) > 1]
    return " ".join(words) if words else None


def parse_temporal(question: str) -> TemporalIntent:
    """What kind of temporal question this is, and about what.

    Order matters: BEFORE/AFTER are checked before POINT_IN_TIME because
    "trước khi sang Bình Minh" carries no date but is still anchored in time,
    and HISTORY before CURRENT because "đã đổi ... những lần nào" contains
    "đã" and would otherwise read as a plain current-state question.
    """
    text = _fold(question)

    # The anchor is resolved first so it can be kept out of the subject. Both
    # are proper names, and picking the longest run without this reads "trước
    # khi sang Bình Minh thì Bùi Cường …" as a question about Bình Minh.
    anchor = None
    if _BEFORE.search(text):
        anchor = _anchor_after(text, _BEFORE)
    elif _AFTER.search(text):
        anchor = _anchor_after(text, _AFTER)

    subject = _subject_of(text, exclude=anchor)

    # The aspect comes from the resolver, as a normalised predicate.
    #
    # Stripping stopwords and keeping the leftovers produced single words like
    # "sống", and one common token cannot separate LOCATION from ROLE. That was
    # 14 of 34 failures — the largest group — every one of them the right
    # person answered from the wrong slot. The probe is now the predicate's own
    # vocabulary rather than whatever survived the question.
    from .aspect_resolver import Predicate, resolve_aspect

    if not _ASPECT_PROBE:
        _build_aspect_probes()
    resolved = resolve_aspect(text)
    if resolved.predicate is not Predicate.UNKNOWN:
        aspect = " ".join(_ASPECT_PROBE.get(resolved.predicate, ()))
    else:
        # No predicate identified: fall back to the old leftovers rather than
        # nothing, so a question the ontology does not cover still gets an
        # answer. `stage_failed` records which path was taken.
        aspect = _aspect_of(text, subject, anchor)

    intent = TemporalIntent(subject=subject, anchor=anchor, aspect=aspect)
    intent.predicate = resolved.predicate.name
    intent.mode = "belief" if _BELIEF_MODE.search(text) else "world"

    if _HISTORY.search(text):
        intent.kind = TemporalKind.HISTORY
        return intent
    if _BEFORE.search(text):
        intent.kind = TemporalKind.BEFORE
        return intent
    if _AFTER.search(text):
        intent.kind = TemporalKind.AFTER
        return intent

    when = _extract_when(text)
    if when:
        intent.kind = TemporalKind.POINT_IN_TIME
        intent.when = when
        return intent

    intent.kind = TemporalKind.CURRENT if _CURRENT.search(text) \
        else TemporalKind.POINT_IN_TIME
    return intent


def _anchor_after(text: str, marker: re.Pattern) -> str | None:
    """The named thing a before/after question hangs on: "trước khi sang
    **Bình Minh**"."""
    match = marker.search(text)
    if not match:
        return None
    tail = text[match.end():]
    return _subject_of(tail)


# --------------------------------------------------------------------------
# stage 2 — the claim history for one slot
# --------------------------------------------------------------------------

def _core(content: str) -> str:
    """The sentence with restatement and correction markers stripped.

    Two memories saying the same thing must compare equal, or a restatement
    becomes a new claim and invents an interval boundary.
    """
    text = _RESTATEMENT.sub("", _fold(content))
    text = _CORRECTION.sub("", text)
    text = re.sub(r"^[\s,;:]+", "", text)
    return text.lower().rstrip(" .!?")


def _key_for(predicate_name: str | None) -> str | None:
    """`"EMPLOYER"` -> `"employer"`, or None for UNKNOWN and anything unknown."""
    if not predicate_name:
        return None
    from .aspect_resolver import Predicate

    member = getattr(Predicate, str(predicate_name), None)
    return member.attribute if member and member.attribute else None


def _attribute_keys() -> frozenset[str]:
    """Every attribute key the resolver can produce. Computed, not listed.

    Lets `claim_history` tell "a caller passed the key" from "a caller passed
    a probe sentence" without demanding every call site be updated at once.
    """
    from .aspect_resolver import Predicate

    return frozenset(p.attribute for p in Predicate if p.attribute)


_ATTRIBUTE_KEYS = _attribute_keys()


import os as _os

#: See `claim_history`. **On.** The three temporal failures that held it back
#: were one cause — `aspect_resolver@1` ran a name across a clause boundary and
#: stored "sai Bùi Cường" — fixed at the producer, and rows written by that
#: resolver are now read the old way rather than judged as a different person.
#: `SUBJECT_IDENTITY_READ=off` restores text matching.
_SUBJECT_IDENTITY_READ = _os.getenv("SUBJECT_IDENTITY_READ", "on").strip().lower()

_TRUSTED = "trusted"

#: What actually executed, counted where the decision is made.
#:
#: A suite of 1055 tests stayed green while `376c4ce` put cosine back on 37 of
#: 38 queries, because every test asked what the system *answered* and none
#: asked what it *did*. These five counters are the missing question.
#:
#:     structured_branch_taken  the structured predicate selection ran
#:     fallback_attempted       a STRUCTURED_GAP opened the rescue lane
#:     fallback_contributed     the rescue added at least one candidate
#:     unnecessary_fallback     the lane opened while structured evidence was
#:                              already sufficient — the bug, as a number
#:     structured_queries       denominator, printed alongside every rate
#:
#: `unnecessary_fallback` is the one that matters. A rescue is an exception
#: path justified by missing decision-relevant evidence, not a second
#: retrieval strategy running in parallel: **fallback requires a reason.**
EXECUTION: Counter = Counter()


def reset_execution() -> None:
    EXECUTION.clear()


def execution_report() -> dict[str, Any]:
    total = EXECUTION.get("structured_queries", 0)
    return {
        "structured_queries": total,
        "structured_branch_taken": EXECUTION.get("structured_branch_taken", 0),
        "fallback_attempted": EXECUTION.get("fallback_attempted", 0),
        "fallback_contributed": EXECUTION.get("fallback_contributed", 0),
        "unnecessary_fallback": EXECUTION.get("unnecessary_fallback", 0),
        "by_aspect_calls": EXECUTION.get("by_aspect_calls", 0),
        # The identity lane. `structured_identity_hit` alone reads as health
        # whatever happened to the rows that did not survive selection, so the
        # exclusions are reported beside it or the report is not a report.
        "structured_identity_hit":
            EXECUTION.get("structured_identity_hit", 0),
        "ambiguous_identity_abstain":
            EXECUTION.get("ambiguous_identity_abstain", 0),
        "identity_fallback_attempted":
            EXECUTION.get("identity_fallback_attempted", 0),
        "identity_excluded": EXECUTION.get("identity_excluded", 0),
        # Rows whose subject came from a superseded resolver and were therefore
        # read the old way. Non-zero means the store has not been migrated, so
        # identity separation is not yet in force for those rows — a fact the
        # operator must be able to state rather than imply.
        "identity_stale_resolver":
            EXECUTION.get("identity_stale_resolver", 0),
        # Rows the text says belong to this person and the stored identity
        # says do not. Non-zero means a chronology was cut on evidence that
        # contradicts the sentence itself — never normal, always worth a look.
        "identity_excluded_mentioned":
            EXECUTION.get("identity_excluded_mentioned", 0),
        # Epistemic state, reported beside world-truth score and never mixed
        # into it. A system can be oracle-world wrong and epistemically
        # correct; scoring only the first optimises the second away.
        #
        # `conflict_detection_accuracy` and `false_conflict_rate` need an
        # oracle and so belong to the eval harness, not here. These two are
        # what the runtime can honestly know about itself.
        "conflict_detected": EXECUTION.get("conflict_detected", 0),
        # **Target: zero.** A single value returned for a contested slot,
        # decided by nothing. This is the failure the interval invariant
        # closed, and the only counter that can show it reopening.
        "silent_conflict_resolution":
            EXECUTION.get("silent_conflict_resolution", 0),
        # Rates carry their denominator. A rate without one is a number
        # somebody will quote in isolation.
        "unnecessary_fallback_rate":
            round(EXECUTION.get("unnecessary_fallback", 0) / total, 4)
            if total else 0.0,
    }


def _status_of_row(structured_json: Any) -> str:
    from .slot_backfill import status_of

    return status_of(_loads_slot(structured_json))


def _positively_selected(memory_os: Any, candidates: list[tuple],
                         probe: str) -> list[tuple]:
    """Rows the aspect filter actively chose — never its escape hatch.

    `_by_aspect` returns `or candidates` when it cannot discriminate: with no
    embedder configured, an English needle matches no Vietnamese text, the
    filter empties, and everything comes back. That is a reasonable default
    for "I have nothing better", and it is exactly wrong here — a rescue path
    that returns everything rescues a birthday into an employer history, which
    is how the first attempt at this broke four tests.

    So the rescue demands positive evidence: the filter must return a strict
    subset, or nothing is rescued.
    """
    if not candidates or not probe:
        return []
    EXECUTION["by_aspect_calls"] += 1
    chosen = _by_aspect(memory_os, candidates, probe)
    if len(chosen) >= len(candidates):
        return []
    return chosen


def _loads_slot(structured_json: Any) -> dict:
    if not structured_json:
        return {}
    try:
        payload = (json.loads(structured_json)
                   if isinstance(structured_json, str) else structured_json)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _slot_of_row(structured_json: Any) -> str | None:
    """The attribute a row declares it belongs to, or None for a legacy row.

    None and "" are kept apart on purpose: a row that stored no slot must fall
    back to the text path, while a row that stored one is answered by equality
    and never by similarity.
    """
    if not structured_json:
        return None
    try:
        payload = (json.loads(structured_json)
                   if isinstance(structured_json, str) else structured_json)
    except (TypeError, ValueError):
        return None
    attribute = (payload or {}).get("attribute")
    return str(attribute) if attribute else None


def _by_subject(rows: list, subject: str) -> list[tuple]:
    """Whose records these are, by identity where one is stored.

    The asymmetry this closes: a predicate was resolved once at ingest,
    persisted, and read back by equality — while the subject was resolved
    once, persisted, and then re-derived from the text on every read by
    `_mentions`. Understand, discard, guess again: the same shape as the
    predicate defect, on the other axis.

    Four cases, and the third is the one that costs the most:

        stored identity == query identity   keep
        stored identity != query identity   **hard separation.** Nguyễn Quân
                                            and Nguyễn Dũng share a family
                                            name; text matching that lets one
                                            see the other's phone number is a
                                            cross-person leak, not a ranking
                                            imperfection.
        no stored identity                  bounded textual fallback, as
                                            before — legacy rows must not go
                                            missing
        ambiguous query surface             unresolved. "An" ends both "Vũ An"
                                            and "Trần An", and nothing ranks
                                            people.

    The stored identity is itself UNTRUSTED — it was inferred at ingest — so
    this is preference and separation, never a claim of certainty.
    """
    from .subject_identity import UNKNOWN, identify

    known = {e for e in (_entity_of_row(s) for *_, s in rows) if e}
    wanted = identify(subject, known=known)
    if wanted.status == UNKNOWN:
        # More than one established person ends in this surface. Answering
        # from either would be choosing a human being by similarity.
        EXECUTION["ambiguous_identity_abstain"] += 1
        return []

    # **Row order is preserved.** `rows` arrives `ORDER BY observed_at, rowid`
    # and everything downstream depends on it: a correction is recognised by
    # sitting immediately after the claim it retracts, and intervals close
    # against the next claim in sequence. The first version returned
    # `structured + legacy`, which interleaves a chronology by whether a row
    # happened to carry an identity — three temporal tests died, and they were
    # right to.
    kept, structured_seen, legacy_seen = [], False, False
    for memory_id, content, observed_at, _, _, structured_json in rows:
        entity = _entity_of_row(structured_json)
        if entity and not _current_resolver(structured_json):
            # Written by an older resolver, so the stored subject is not
            # evidence about *this* person — it is evidence about what a
            # superseded parser thought.
            #
            # `aspect_resolver@1` let a name run across a clause boundary and
            # stored "sai Bùi Cường" for a sentence about Bùi Cường. Judged as
            # an identity, that is a definite mismatch and the row leaves the
            # chronology; judged as provenance, it is simply not an answer yet.
            # `UNKNOWN > false identity merge` cuts both ways — the missing
            # half was that a surface which resolves to nobody real is UNKNOWN,
            # not somebody else, and `STRUCTURED_GAP` and `MISMATCH` are two
            # states this branch had merged into one.
            #
            # So a stale row falls to the same bounded textual path as a row
            # with no identity at all: no worse than the behaviour that ships
            # today, and no silent deletion from a store nobody has migrated.
            # `slot_backfill.backfill()` promotes these rows; until it is run,
            # they are read the old way and counted.
            EXECUTION["identity_stale_resolver"] += 1
            entity = None
        if entity:
            if identify(entity, known=known).subject_id == wanted.subject_id:
                kept.append((memory_id, content, observed_at))
                structured_seen = True
            else:
                # A definite mismatch is dropped here and never rescued by
                # text. Cross-person contamination is the one failure with no
                # benign reading.
                #
                # But a drop must leave a witness. This branch once discarded
                # half a slot's history — every correction and every
                # restatement, because the ingest resolver glued "Đính chính,"
                # and "Nhắc lại," onto the name — while `execution_report()`
                # returned counters byte-identical to a healthy structured
                # query. Three temporal tests failed three stages downstream
                # and two rounds of hypotheses went looking at row ordering,
                # because nothing here said a row had left the chronology.
                EXECUTION["identity_excluded"] += 1
                if _mentions(str(content), subject):
                    # The expensive one. The sentence names this person and the
                    # stored identity says otherwise, so exactly one of the two
                    # is wrong and the row is gone either way. Whether that is
                    # a corrupted subject span or a genuinely different person
                    # is not decidable here — which is the point: it is the
                    # signal that something upstream needs looking at, not a
                    # licence to rescue the row.
                    EXECUTION["identity_excluded_mentioned"] += 1
        elif _mentions(str(content), subject):
            kept.append((memory_id, content, observed_at))
            legacy_seen = True

    if structured_seen:
        EXECUTION["structured_identity_hit"] += 1
    if legacy_seen:
        EXECUTION["identity_fallback_attempted"] += 1
        if not structured_seen:
            EXECUTION["identity_fallback_contributed"] += 1
    return kept


def _closers(live: list[ClaimSpan]) -> list[ClaimSpan]:
    """Which claims are allowed to end another claim's interval.

    A named function for a one-line filter, because it is the whole invariant
    and an invariant that cannot be replaced by a mutant cannot be shown to be
    doing anything. `UNRESOLVED_CONFLICT_CLOSES_PREVIOUS_INTERVAL` is exactly
    this returning `live`.
    """
    return [s for s in live if not s.disputed]


def _is_disputed(content: str) -> bool:
    """Did this claim arrive as a competing account rather than a change?

    The markers come from `relations.ALTERNATIVE_SOURCE_MARKERS`, which is the
    same list `classify_relation` reads to return CONFLICT. Deliberately
    imported rather than restated: two copies of "what counts as a disputed
    claim" would drift, and the drift would show up as the read path and the
    write path disagreeing about whether a truth is settled — which is the
    exact class of defect this invariant exists to close.
    """
    from .relations import ALTERNATIVE_SOURCE_MARKERS

    lowered = _fold(content).lower()
    return any(marker in lowered for marker in ALTERNATIVE_SOURCE_MARKERS)


def _entity_of_row(structured_json: Any) -> str | None:
    entity = _loads_slot(structured_json).get("entity")
    return str(entity) if entity else None


def _current_resolver(structured_json: Any) -> bool:
    """Was this row's subject produced by the resolver running now?

    Read from the row rather than assumed, because the answer decides whether
    the stored subject is evidence or archaeology. A row with no recorded
    version is old by definition — the field was written from the first slot
    onward, so its absence dates the row rather than excusing it.
    """
    from .slot_backfill import RESOLVER_VERSION

    slot = _loads_slot(structured_json)
    return slot.get("resolver_version") == RESOLVER_VERSION


def _mentions(content: str, subject: str) -> bool:
    """Does this memory talk about that person?

    Word by word, never by substring. A one-syllable name is common enough to
    appear inside ordinary words: the subject "An" matched "đ**an**g" and
    answered a question about Vũ An with a sentence about Trần Thảo. Every
    syllable must appear as its own word — "Trần An" must not match a memory
    about An Phát either, or one person's question deletes into another's
    records.
    """
    words = set(re.findall(r"\w+", _fold(content).lower(), re.UNICODE))
    return all(part.lower() in words for part in subject.split())


def claim_history(memory_os: Any, *, subject: str, aspect: str | None,
                  context: Any, hint: str | None = None,
                  predicate: str | None = None,
                  rescue: bool = False) -> list[ClaimSpan]:
    """Every claim about one (subject, aspect), in order, with intervals.

    Intervals come from observation order: each claim holds until the next one
    about the same slot. A restatement carries the same core sentence as an
    earlier claim and does not open a new span — it raises that span's
    confirmation count and nothing else.
    """
    conn = memory_os.memories.conn
    rows = conn.execute(
        "SELECT memory_id, content, observed_at, valid_from, valid_to, "
        "structured_json FROM cognitive_memories "
        "WHERE superseded_at IS NULL ORDER BY observed_at, rowid"
    ).fetchall()

    stored = {str(m): (vf, vt) for m, _, _, vf, vt, _ in rows
              if vt is not None}
    slots = {str(m): _slot_of_row(s) for m, _, _, _, _, s in rows}
    statuses = {str(m): _status_of_row(s) for m, _, _, _, _, s in rows}
    # P0-B, on by default. `SUBJECT_IDENTITY_READ=off` restores text matching.
    #
    # The three temporal tests that held this back turned out to be one cause,
    # and it was not in this function. `_names` tokenised without punctuation,
    # so a name ran backwards across a comma and "…là sai, Bùi Cường…" was
    # stored under the subject "sai Bùi Cường". Identity selection then did
    # exactly what it was told — a definite mismatch, dropped — and the rows it
    # dropped were the corrections and the restatements, because those are the
    # sentences with a clause before the name.
    #
    # Two hypotheses were wrong on the way here and both were wrong in the same
    # direction: they assumed this function chose badly. It did not. It was
    # given a corrupted fact and had no way to tell. The third, that a row
    # written by a superseded resolver is *unknown* rather than *somebody
    # else*, is what makes the flag safe to leave on against a store nobody has
    # migrated — see `_current_resolver`.
    if _SUBJECT_IDENTITY_READ == "on":
        candidates = _by_subject(rows, subject)
    else:
        candidates = [(m, c, o) for m, c, o, _, _, _ in rows
                      if _mentions(str(c), subject)]

    # `hint` is the anchor of a before/after question, and it identifies the
    # slot when the aspect alone cannot. "Trước khi sang Bình Minh thì Bùi
    # Cường làm ở đâu" reduces to the aspect "làm" — one weak word — and a
    # single word discriminates nothing, so the candidate set stayed at every
    # memory about the person and the walk stepped from an employment claim
    # onto a birthday. "Bình Minh" names the slot precisely, because the
    # question already told us which claim it is standing next to.
    # Structured first. A row that already says which slot it belongs to is
    # not a similarity problem, and treating it as one is the wrong-slot
    # defect: `state_operator` passed the attribute key "employer" into a
    # cosine comparison against Vietnamese sentences and got back a memory
    # about a job title — right subject, wrong slot, answered confidently.
    #
    # `aspect` here is the attribute key, which is exactly what must never
    # reach a text comparison. Against a stored slot it is an equality test.
    # `predicate` is the attribute key and `aspect` is a probe *string*, and
    # they are not interchangeable. The first version compared the stored slot
    # against `aspect`, which works for `state_operator` and `ever_operator`
    # (they pass the key) and never fires for `answer_temporal`, which passes
    # `intent.aspect` — a join of Vietnamese probe phrases. So the branch was
    # dead on the one caller with the most failures, and TEMPORAL_AT scoring
    # unchanged was read as "no coupling" when it meant "not reached".
    #
    # Two separate parameters now, and a caller that supplies neither gets the
    # old behaviour exactly.
    slot_key = predicate or (aspect if aspect in _ATTRIBUTE_KEYS else None)
    probe = " ".join(p for p in (aspect, hint) if p)

    structured = ([c for c in candidates if slots.get(str(c[0])) == slot_key]
                  if slot_key else [])
    if structured and slot_key:
        # Structured exact match, and **no rescue by default**.
        #
        # The previous version rescued every UNTRUSTED row whose slot merely
        # differed. That reads as safety and is the opposite: asking about
        # somebody's employer, their phone, birthday, salary and project rows
        # all "mismatch", so the rescue fired on nearly every query and
        # `_by_aspect` ran again — cosine back in exactly the place structured
        # execution had just removed it. Measured on the frozen 38: the
        # structured arm collapsed onto the legacy arm, 31/38 down to 25/38,
        # with 37 of 38 queries calling the aspect filter.
        #
        # A mismatched row is the ordinary state of a person's record. Its
        # existence is not evidence that its slot is wrong.
        #
        # The trigger for rescue is a *gap the operator cannot answer from*,
        # not a row that disagrees — see `rescue=True` below, which the
        # caller sets after trying the structured history and finding it
        # insufficient.
        EXECUTION["structured_queries"] += 1
        EXECUTION["structured_branch_taken"] += 1
        keep = {id(s) for s in structured}
        if rescue:
            EXECUTION["fallback_attempted"] += 1
            # Structured evidence exists and the lane opened anyway. By
            # construction the callers only set `rescue=True` after a first
            # pass returned nothing, so reaching here means somebody bypassed
            # the gap check — which is precisely what `376c4ce` did, and what
            # the EAGER_RESCUE mutant does on purpose.
            EXECUTION["unnecessary_fallback"] += 1
            rescuable = [c for c in candidates
                         if id(c) not in keep
                         and statuses.get(str(c[0])) != _TRUSTED]
            rescued = _positively_selected(memory_os, rescuable, probe)
            if rescued:
                EXECUTION["fallback_contributed"] += 1
            keep |= {id(c) for c in rescued}
        candidates = [c for c in candidates if id(c) in keep]
    elif structured:
        unslotted = [c for c in candidates if slots.get(str(c[0])) is None]
        rescued = (_by_aspect(memory_os, unslotted, probe)
                   if (unslotted and probe) else [])
        keep = {id(c) for c in structured} | {id(c) for c in rescued}
        candidates = [c for c in candidates if id(c) in keep]
    elif probe:
        candidates = _by_aspect(memory_os, candidates, probe)

    spans: list[ClaimSpan] = []
    by_core: dict[str, ClaimSpan] = {}
    for memory_id, content, observed_at in candidates:
        core = _core(str(content))
        existing = by_core.get(core)
        if existing is not None:
            # Same thing said again. Freshness, not a change.
            existing.confirmations += 1
            continue
        span = ClaimSpan(memory_id=memory_id, content=str(content),
                         valid_from=str(observed_at),
                         observed_at=str(observed_at),
                         disputed=_is_disputed(str(content)))
        if _CORRECTION.search(str(content)) and spans:
            # A retraction: the previous claim was never true, so it is marked
            # rather than closed. `holds_at` refuses it at every instant.
            spans[-1].kind = "corrected"
            spans[-1].retracted_by = memory_id
            span.valid_from = spans[-1].valid_from
        spans.append(span)
        by_core[core] = span

    live = [s for s in spans if not s.retracted_by]
    # **An unresolved relation may not close the interval of any claim.**
    #
    # Four of the seven remaining `historical` failures were one shape, and in
    # every one of them three components already agreed that the truth was in
    # dispute: `classify_relation` returns CONFLICT for "theo một nguồn khác",
    # `lifecycle_runtime` has no CONFLICT branch and says so deliberately, and
    # the database holds `valid_to = NULL` because nothing wrote a window.
    #
    # This loop was the fourth component. Deriving intervals from arrival order
    # alone, it took the moment the rumour arrived as the boundary — and so
    # quietly picked the rumour's side, which is precisely the decision the
    # other three declined to make.
    #
    # Ownership, stated once: the write and lifecycle layers **own** validity
    # transitions; this layer **consumes** them; it must never **invent** a
    # closure. `Relation.resolves` is the predicate for it and has existed all
    # along — nothing here had ever asked.
    #
    # This says nothing about who wins. The disputed claim keeps its span, the
    # dispute keeps its own, and how a query is answered inside a contested
    # region is a separate decision that is deliberately not taken here.
    closers = _closers(live)
    for earlier, later in zip(closers, closers[1:]):
        earlier.valid_to = later.valid_from
        earlier.kind = "superseded" if earlier.kind == "asserted" else earlier.kind

    def _audit_invariant() -> None:
        """Count violations on the **result**, not on the reasoning.

        The first version of this counter inferred a violation from which span
        got selected, and fired on a lone "theo một nguồn khác" claim that had
        nothing to disagree with — a target-zero metric reporting one, for an
        event that never happened. A metric that cries wolf is dismissed the
        first time, and is then unavailable on the day it is right.

        So it checks the property directly: no interval may end exactly where
        a disputed claim begins. That holds whoever wrote the window — this
        function, or the lifecycle override below, which is the path this
        loop cannot see and the one most likely to reintroduce it.
        """
        starts = {s.valid_from for s in spans if s.disputed}
        if not starts:
            return
        for span in spans:
            if span.valid_to is not None and span.valid_to in starts:
                EXECUTION["silent_conflict_resolution"] += 1

    # A window written by the lifecycle wins over the one derived here.
    #
    # Both are implementations of the same semantics, and this function was the
    # only one the benchmark measured — which is why an A/B that closed 39
    # windows and emptied 17 scored identically to the arm that closed none.
    # The two do not always agree, and where they disagree the stored one is
    # the better answer: it came from `classify_relation`, which reads the
    # predicate's declared shape and abstains where the evidence does not
    # reach a verdict. The rule here derives supersession from arrival order
    # alone and correction from a single regex.
    #
    # Nothing is written by that path unless the lifecycle flag is on, so a
    # store that never ran it has an empty `stored` and this loop does nothing.
    for span in spans:
        window = stored.get(str(span.memory_id))
        if window is None:
            continue
        valid_from, valid_to = window
        if valid_from is not None:
            span.valid_from = str(valid_from)
        span.valid_to = str(valid_to)
        if str(valid_to) == str(span.valid_from):
            # An empty window is how a correction is recorded: no instant
            # satisfies `valid_from <= as_of < valid_to`. Marked here too so
            # `holds_at` refuses it for the same reason it refuses the ones
            # this function retracted itself.
            span.kind = "corrected"
            span.retracted_by = span.retracted_by or "lifecycle"
        elif span.kind == "asserted":
            span.kind = "superseded"

    _audit_invariant()
    return spans


def _by_aspect(memory_os: Any, candidates: list[tuple], aspect: str) -> list[tuple]:
    """Keep the claims about this aspect, by relative similarity.

    Relative, for the reason measured while fixing deletion scope: an absolute
    floor borrowed from retrieval calibration is a threshold from a different
    scoring regime, and it rejected everything. "Which of this person's
    memories are about X" is a comparative question.
    """
    from .forget_scope import _topic_members
    from .semantic_index import cosine, load_vectors

    embedder = getattr(getattr(memory_os, "retrieval", None), "embedder", None)
    if embedder is None:
        needles = [w for w in aspect.split() if len(w) > 2]
        return [c for c in candidates
                if any(n in _fold(c[1]).lower() for n in needles)] or candidates

    vector = embedder.embed(aspect)
    vectors = load_vectors(memory_os.memories.conn, [c[0] for c in candidates],
                           dims=len(vector) or None)
    scores = {c[0]: cosine(vector, vectors[c[0]])
              for c in candidates if c[0] in vectors}
    members = _topic_members(scores)
    return [c for c in candidates if c[0] in members] or candidates


# --------------------------------------------------------------------------
# stage 3 — answer
# --------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provenance(spans: list[ClaimSpan]) -> list[dict[str, Any]]:
    return [{"memory_id": s.memory_id, "valid_from": s.valid_from,
             "valid_to": s.valid_to, "kind": s.kind,
             "confirmations": s.confirmations} for s in spans]


def answer_temporal(memory_os: Any, question: str, *, context: Any,
                    mode: str | None = None) -> TemporalAnswer:
    """Run the pipeline and say which stage gave up if one did."""
    intent = parse_temporal(question)
    if mode:
        intent.mode = mode
    result = TemporalAnswer(intent=intent, mode=intent.mode)

    if not intent.subject:
        result.stage_failed = "resolve_subject"
        result.note = "không rút được chủ thể nào từ câu hỏi"
        return result

    spans = claim_history(
        memory_os, subject=intent.subject, aspect=intent.aspect,
        context=context,
        # The predicate the parser already resolved, passed rather than
        # discarded. `intent.predicate` holds the enum *name* ("EMPLOYER");
        # the slot stores the attribute key ("employer"), so it is translated
        # here rather than storing two spellings.
        predicate=_key_for(intent.predicate),
        hint=intent.anchor if intent.kind in (TemporalKind.BEFORE,
                                              TemporalKind.AFTER) else None)
    if not spans and _key_for(intent.predicate):
        # STRUCTURED_GAP, same rule as `state_at`: the slot is empty for this
        # person, so a row mis-slotted elsewhere is worth looking for. A
        # history that exists needs no rescue, and rescuing it anyway is what
        # put cosine back on 37 of 38 queries.
        spans = claim_history(
            memory_os, subject=intent.subject, aspect=intent.aspect,
            context=context, predicate=_key_for(intent.predicate),
            hint=intent.anchor if intent.kind in (TemporalKind.BEFORE,
                                                  TemporalKind.AFTER) else None,
            rescue=True)
    if not spans:
        result.stage_failed = "claim_history"
        result.note = (f"không có ký ức nào về '{intent.subject}'"
                       + (f" thuộc khía cạnh '{intent.aspect}'" if intent.aspect else ""))
        return result
    result.spans = spans

    # Belief history answers about the system, so a retracted claim is exactly
    # what is being asked for. World truth refuses it at every instant.
    usable = spans if intent.mode == "belief" else [s for s in spans if not s.retracted_by]

    chosen: list[ClaimSpan] = []
    if intent.kind is TemporalKind.HISTORY:
        chosen = [s for s in spans if not s.retracted_by]
    elif intent.kind is TemporalKind.CURRENT:
        chosen = [s for s in usable if s.valid_to is None][-1:] or usable[-1:]
    elif intent.kind in (TemporalKind.BEFORE, TemporalKind.AFTER):
        chosen = _around_anchor(usable, intent)
    else:
        when = intent.when or _now()
        if intent.mode == "belief":
            # What the system had been told by then — `observed_at`, not
            # `valid_from`. A correction backdates its validity to cover the
            # interval the wrong claim occupied, so filtering on `valid_from`
            # returns a correction the system did not yet have and answers
            # "what did you think in February" with March's knowledge.
            held = [s for s in spans if (s.observed_at or s.valid_from) <= when]
            chosen = held[-1:] if held else []
        else:
            chosen = [s for s in usable if s.holds_at(when)]
            if not chosen:
                earlier = [s for s in usable if s.valid_from <= when]
                chosen = earlier[-1:]

    if not chosen:
        result.stage_failed = "interval_selection"
        result.note = (f"có {len(spans)} mốc nhưng không mốc nào phủ thời điểm "
                       f"{intent.when or 'hiện tại'}")
        return result

    result.executed = True
    conflict = _conflict_state(
        chosen, predicate=_key_for(intent.predicate),
        when=intent.when or "hiện tại", mode=intent.mode)
    if conflict is not None:
        EXECUTION["conflict_detected"] += 1
        result.conflict = conflict
        result.answer_text = _render_conflict(conflict)
        result.provenance = conflict.provenance
        return result
    result.answer_text = " ".join(s.content for s in chosen)
    result.provenance = _provenance(chosen)
    return result


def _single_valued(predicate: str | None) -> bool:
    from .relations import Cardinality, semantics_for

    if not predicate:
        return False
    return semantics_for(predicate).cardinality is Cardinality.ONE


def _conflict_state(chosen: list[ClaimSpan], *, predicate: str | None,
                    when: str, mode: str) -> ConflictState | None:
    """Did the operator reach a disagreement rather than a value?

    Three conditions, and each rules out a case that merely looks like one:

        the predicate holds one value at a time   `city` can be disputed;
                                                  `project` legitimately has
                                                  several at once, and calling
                                                  that a conflict is a false
                                                  positive
        two or more claims survive selection      one claim is never disputed
        their propositions differ                 a restatement is agreement,
                                                  which `_core` already knows

    Belief questions are exempt: "what did you think in February" asks what the
    system held, and it held one thing at a time by construction.
    """
    if mode == "belief" or len(chosen) < 2 or not _single_valued(predicate):
        return None
    if len({_core(s.content) for s in chosen}) < 2:
        return None                      # same claim, said twice
    return ConflictState(predicate=str(predicate), when=str(when),
                         claims=list(chosen), provenance=_provenance(chosen))


def _render_conflict(conflict: ConflictState) -> str:
    """Deterministic. The operator already knows exactly what it knows.

    Handing this to a model to phrase would put a synthesis step between a
    known epistemic state and the sentence describing it, and every failure
    that step could introduce would be a failure invented after the answer was
    already correct.
    """
    lines = [f"Có {len(conflict.claims)} nguồn mâu thuẫn về "
             f"'{conflict.predicate}' tại {conflict.when}, "
             f"chưa đủ căn cứ kết luận một giá trị duy nhất:"]
    for claim in conflict.claims:
        mark = " (nguồn khác)" if claim.disputed else ""
        lines.append(f"— {claim.content}{mark}")
    lines.append("Cần một bằng chứng giải quyết được mâu thuẫn "
                 "(đính chính hoặc thay đổi có mốc thời gian) mới kết luận.")
    return "\n".join(lines)


def _around_anchor(spans: list[ClaimSpan], intent: TemporalIntent) -> list[ClaimSpan]:
    if not intent.anchor:
        return spans[:1] if intent.kind is TemporalKind.BEFORE else spans[-1:]
    anchor = intent.anchor.lower()
    for index, span in enumerate(spans):
        if anchor in _fold(span.content).lower():
            if intent.kind is TemporalKind.BEFORE:
                return spans[index - 1:index] if index else []
            return spans[index + 1:index + 2]
    return []


__all__ = [
    "ClaimSpan",
    "TemporalAnswer",
    "TemporalIntent",
    "TemporalKind",
    "answer_temporal",
    "claim_history",
    "parse_temporal",
]
