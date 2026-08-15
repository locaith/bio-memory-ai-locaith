"""Questions asked at checkpoints, and the answer key that outlives them.

The world in `lifetime_world` changes for a thousand events. This asks it
questions along the way, and grades the answers against the ledger rather than
against anything the system under test produced.

Four families, chosen because each one separates a memory with a lifecycle from
a vector store with timestamps:

    CURRENT     "Who is CTO now?"          -> the value holding at this tick
    HISTORICAL  "Who was CTO in January?"  -> the value that held back then
    EVER        "Was An ever CTO?"         -> yes, even after being replaced
    FORGOTTEN   asked after a delete       -> must decline, both tenses

The FORGOTTEN family is the only one where the right answer is a refusal, and
it is deliberately mixed in with the others so that a system cannot pass by
refusing everything. `always_declines` is one of the fake systems this module
is tested against, and it has to fail.

Grading is substring containment on a normalised string. That is crude and it
is honest about being crude: the values in this world are short, distinct
noun phrases, so an answer either contains "trưởng phòng" or it does not. What
it cannot do is judge a *reason*, which is why nothing here scores explanations.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .lifetime_world import ATTRIBUTES, Subject, TruthLedger, tick_to_iso


class Family(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    EVER = "ever"
    FORGOTTEN = "forgotten"


class Expect(str, Enum):
    VALUE = "value"        # the answer must contain this string
    DECLINE = "decline"    # the answer must admit it does not know
    YES = "yes"
    NO = "no"


#: Ways of saying "I do not know", in both languages this store sees.
_DECLINE = re.compile(
    r"(không biết|không rõ|không có (thông tin|ghi nhận|dữ liệu)|chưa có"
    r"|không tìm thấy|đã xoá|đã quên|không lưu"
    r"|do not know|don'?t know|no (information|record|data)|not found"
    r"|unknown|cannot answer)",
    re.IGNORECASE,
)

_YES = re.compile(r"(^|\W)(có|đúng|từng|yes|true)(\W|$)", re.IGNORECASE)
_NO = re.compile(r"(không|chưa từng|no|never|false)", re.IGNORECASE)


def _fold(text: str) -> str:
    """Lowercase and collapse whitespace, keeping Vietnamese diacritics.

    Stripping diacritics would fold distinct values together — this world has
    both "Hà Nội" and "Hải Phòng", and a comparison that cannot tell "có" from
    "cỏ" will eventually mark a wrong answer right.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip().lower()


@dataclass
class Question:
    family: Family
    tick: int
    text: str
    expect: Expect
    value: str = ""
    subject_id: str = ""
    attribute: str = ""
    #: Set when the answer changed at least once before this question was asked,
    #: which is what `supersession_accuracy` is measured over.
    after_supersession: bool = False
    #: A string that must not appear in the answer, whatever else does.
    #:
    #: Separating this from `value` is what made deletion measurable. The first
    #: version asked deletion questions only where a slot was *empty* after the
    #: delete, and measured 4 of 18 deletions — because in 14 cases the world
    #: went on to say something new about the same topic, which read as "not
    #: deleted". But new information is not a failure to delete. What must never
    #: come back is the specific value that was removed, and that is true
    #: whether or not something replaced it.
    forbidden: str = ""

    def grade(self, answer: str) -> bool:
        folded = _fold(answer)

        # Checked before anything else and for every family: an otherwise
        # correct answer that also recites deleted content has still leaked it.
        if self.forbidden and _fold(self.forbidden) in folded:
            return False

        if not folded:
            return self.expect is Expect.DECLINE

        if self.expect is Expect.DECLINE:
            # A decline that also names the value is not a decline. "I no longer
            # have that, it was 0912345678" leaks exactly what was deleted.
            if self.value and _fold(self.value) in folded:
                return False
            return bool(_DECLINE.search(folded))

        # "I don't know" is not an answer to a yes/no question, in either
        # direction. Measured before this check: `always_declines` scored 0.500
        # on the EVER family, because "Tôi không biết" contains "không" and was
        # read as "no, never". A grader that cannot tell ignorance from denial
        # awards half a family to a system that knows nothing.
        if self.expect in (Expect.YES, Expect.NO) and _DECLINE.search(folded):
            return False

        if self.expect is Expect.YES:
            return bool(_YES.search(folded)) and _fold("chưa từng") not in folded
        if self.expect is Expect.NO:
            return bool(_NO.search(folded))

        # VALUE. A declined answer is wrong here, even though it is honest —
        # the information was available and the system failed to find it.
        return _fold(self.value) in folded


@dataclass
class Scorecard:
    n: int = 0
    correct: int = 0
    by_family: dict[str, tuple[int, int]] = field(default_factory=dict)
    supersession_n: int = 0
    supersession_correct: int = 0
    leaked: int = 0
    leak_n: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def supersession_accuracy(self) -> float:
        return (self.supersession_correct / self.supersession_n
                if self.supersession_n else 0.0)

    @property
    def forgotten_leak_rate(self) -> float:
        """Share of delete requests whose content came back anyway. The one
        metric where a high number is a failure of a promise, not of quality."""
        return self.leaked / self.leak_n if self.leak_n else 0.0

    def family(self, name: Family) -> float:
        correct, total = self.by_family.get(name.value, (0, 0))
        return correct / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "accuracy": round(self.accuracy, 4),
            "current": round(self.family(Family.CURRENT), 4),
            "historical": round(self.family(Family.HISTORICAL), 4),
            "ever": round(self.family(Family.EVER), 4),
            "forgotten": round(self.family(Family.FORGOTTEN), 4),
            "supersession": round(self.supersession_accuracy, 4),
            "forgotten_leak_rate": round(self.forgotten_leak_rate, 4),
        }


_BY_KEY = {a.key: a for a in ATTRIBUTES}


def _phrase(attribute: str, name: str) -> str:
    return _BY_KEY[attribute].question_vi.format(name=name)


def questions_at(ledger: TruthLedger, people: list[Subject], tick: int, *,
                 rng: random.Random, per_family: int = 6) -> list[Question]:
    """A checkpoint's worth of questions about the world as it stands at `tick`."""
    by_id = {p.subject_id: p for p in people}
    slots = sorted({(c.subject_id, c.attribute) for c in ledger.claims})
    rng.shuffle(slots)

    out: list[Question] = []
    counts = {family: 0 for family in Family}

    # Deleted slots are scanned first, on their own pass.
    #
    # They are rare — a thousand ticks produce eighteen deletions — so in a
    # single shuffled pass the loop filled the other three families and stopped
    # before reaching most of them. The claim this product rests hardest on
    # would have had the smallest sample in its own benchmark.
    #
    # Every slot with a deletion is asked, not only the ones left empty. Where
    # the world went on to say something new, the right answer is the new value
    # *and* the deleted one must still be absent; where it did not, the right
    # answer is a refusal. Both are the same promise, and asking only the second
    # measured 4 deletions out of 18.
    deleted = [s for s in slots
               if any(c.forgotten_at is not None and c.forgotten_at <= tick
                      for c in ledger.claims
                      if c.subject_id == s[0] and c.attribute == s[1])]
    for subject_id, attribute in deleted:
        if counts[Family.FORGOTTEN] >= per_family:
            break
        subject = by_id.get(subject_id)
        if subject is None:
            continue
        removed = [c for c in ledger.claims
                   if c.subject_id == subject_id and c.attribute == attribute
                   and c.forgotten_at is not None and c.forgotten_at <= tick]
        if not removed:
            continue
        gone = max(removed, key=lambda c: c.since).value
        survivor = ledger.current(subject_id, attribute, tick)
        if survivor is not None and survivor.value != gone:
            question = Question(
                Family.FORGOTTEN, tick, _phrase(attribute, subject.name),
                Expect.VALUE, survivor.value, subject_id, attribute)
        else:
            question = Question(
                Family.FORGOTTEN, tick, _phrase(attribute, subject.name),
                Expect.DECLINE, gone, subject_id, attribute)
        question.forbidden = gone
        out.append(question)
        counts[Family.FORGOTTEN] += 1

    for subject_id, attribute in slots:
        if all(counts[f] >= per_family for f in Family):
            break
        subject = by_id.get(subject_id)
        if subject is None:
            continue
        history = [c for c in ledger.claims
                   if c.subject_id == subject_id and c.attribute == attribute]
        changed = len([c for c in history if not c.retracted]) > 1

        # A slot with a deletion is already asked above, and asking it again in
        # another family would require the same fact in one question that
        # another forbids — a perfect score becomes impossible and the benchmark
        # reads as a hard problem rather than a broken one.
        if (subject_id, attribute) in set(deleted):
            continue

        current = ledger.current(subject_id, attribute, tick)
        if current is None:
            continue

        if counts[Family.CURRENT] < per_family:
            out.append(Question(
                Family.CURRENT, tick, _phrase(attribute, subject.name),
                Expect.VALUE, current.value, subject_id, attribute,
                after_supersession=changed))
            counts[Family.CURRENT] += 1

        # HISTORICAL — only where a *superseded* value exists, since that is the
        # distinction being tested. A corrected value was never true and must
        # not be asked about; `ledger.at` already refuses to return one.
        if counts[Family.HISTORICAL] < per_family:
            older = [c for c in history
                     if c.until is not None and not c.retracted and c.since < tick]
            if older:
                past = max(older, key=lambda c: c.since)
                when = max(past.since, (past.since + (past.until or tick)) // 2)
                truth = ledger.at(subject_id, attribute, when, tick)
                if truth is not None and truth.value == past.value:
                    date = tick_to_iso(when)[:10]
                    # The phrase is not lowercased at all, because it begins
                    # with the person's name.
                    #
                    # `.lower()` on the whole sentence took the resolver's only
                    # signal away and stopped the operator at stage one on 28
                    # of 28 questions. Lowering just the first letter looked
                    # like the fix and was half of one: the template starts
                    # with the name, so it produced "phạm Vy" and the resolver
                    # recovered only the second syllable — then a one-syllable
                    # subject matched other people's sentences through the
                    # substring "an" inside "đang".
                    #
                    # Vietnamese keeps a proper noun capitalised after a comma.
                    # Leaving it alone is both correct and natural.
                    out.append(Question(
                        Family.HISTORICAL, tick,
                        f"Vào ngày {date}, {_phrase(attribute, subject.name)}",
                        Expect.VALUE, past.value, subject_id, attribute,
                        after_supersession=True))
                    counts[Family.HISTORICAL] += 1

        # EVER — a replaced value is still something that happened.
        #
        # Half of these expect NO, and that is not decoration. When every EVER
        # question expected YES, a fake that answered "Có" to everything scored
        # 30/30 on the family; measured, before this. A family that cannot tell
        # knowledge from agreement is not testing knowledge.
        if counts[Family.EVER] < per_family:
            past_values = [c.value for c in history
                           if c.until is not None and not c.retracted]
            held = {c.value for c in history if not c.retracted}
            never = [v for v in _BY_KEY[attribute].values if v not in held]
            want_no = counts[Family.EVER] % 2 == 1

            if want_no and never:
                value = never[0]
                out.append(Question(
                    Family.EVER, tick,
                    f"{subject.name} đã từng có {attribute} là {value} đúng không?",
                    Expect.NO, value, subject_id, attribute))
                counts[Family.EVER] += 1
            elif past_values:
                value = past_values[0]
                out.append(Question(
                    Family.EVER, tick,
                    f"{subject.name} đã từng có {attribute} là {value} đúng không?",
                    Expect.YES, value, subject_id, attribute,
                    after_supersession=True))
                counts[Family.EVER] += 1

    return out


def score(questions: list[Question],
          answer_fn: Callable[[Question], str]) -> Scorecard:
    card = Scorecard()
    tally: dict[str, list[int]] = {}

    for question in questions:
        answer = answer_fn(question)
        ok = question.grade(answer)

        card.n += 1
        card.correct += int(ok)
        bucket = tally.setdefault(question.family.value, [0, 0])
        bucket[0] += int(ok)
        bucket[1] += 1

        if question.after_supersession:
            card.supersession_n += 1
            card.supersession_correct += int(ok)

        if question.forbidden:
            card.leak_n += 1
            if _fold(question.forbidden) in _fold(answer):
                card.leaked += 1

    card.by_family = {k: (v[0], v[1]) for k, v in tally.items()}
    return card


__all__ = [
    "Expect",
    "Family",
    "Question",
    "Scorecard",
    "questions_at",
    "score",
]
