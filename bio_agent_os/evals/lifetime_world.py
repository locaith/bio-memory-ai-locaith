"""A seeded world that changes, and a ledger of what was true when.

The lifetime benchmark asks a question no single-shot benchmark can: after a
thousand events, does the system still know what is true *now*, what was true
*then*, and what it was told to forget? LoCoMo measures recall over a fixed
transcript. This measures whether a memory survives being lived in.

Three design rules, each one a bug that was found before it was written:

**Ticks are the only unit.** One event per tick, so an event's index and its
tick are the same number by construction. A benchmark that feeds events by list
index while its ledger, probes and checkpoints speak in ticks will silently
score answers against the wrong moment, and every number it prints will look
reasonable.

**The world clock is not the wall clock.** Events carry `observed_at` derived
from their tick, never `datetime.now()`. Without this every memory in a
two-year simulated history lands aged zero, the staleness stratum measures
nothing, and re-running the same seed six months later changes every result.
`observe()` gained an `observed_at` parameter for exactly this reason.

**Two gates, not one.** An oracle answering from the ledger must score ~1.0, or
the rubric is unpassable. And a deliberately broken system must score *low*, or
the rubric is unfailable. The second gate is the one that matters: a benchmark
nobody can fail is a benchmark that tells you nothing, and this project has
already written two of them — a cross-course holdout that scored correct
answers as fabrications, and a stale case that scored an honest refusal as a
failure.

The distinctions the world is built to expose:

    SUPERSEDE  the old value WAS true, until a moment. History is preserved.
    CORRECT    the old value was NEVER true. History is repaired.
    REPEAT     an old value is mentioned again. Nothing changed.

REPEAT is the trap. A store that ranks by recency, or by "most recent mention
wins", reads a repeat as a change and reports a retired fact as current. That
is the single sharpest line between a memory with a lifecycle and a vector
database with timestamps, and it costs nothing to test.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable

#: One tick is one event. Two years of simulated history over a thousand events
#: puts roughly seventeen hours between them, which is close enough to a life.
HOURS_PER_TICK = 17

EPOCH = datetime(2024, 6, 1, 9, 0, 0, tzinfo=timezone.utc)


class EventKind(str, Enum):
    ASSERT = "assert"          # a fact enters the world
    SUPERSEDE = "supersede"    # a new value replaces the old from a moment on
    CORRECT = "correct"        # the old value was never true
    REPEAT = "repeat"          # an existing value is mentioned again
    CONTRADICT = "contradict"  # a second source disagrees, unresolved
    FORGET = "forget"          # the user asks for a subject to be forgotten
    NOISE = "noise"            # unrelated filler


class Volatility(str, Enum):
    """How fast an attribute's truth decays if nobody confirms it."""

    IMMUTABLE = "immutable"    # birthday
    SLOW = "slow"              # home city
    MEDIUM = "medium"          # employer
    FAST = "fast"              # job title, project status
    VOLATILE = "volatile"      # this month's revenue


@dataclass(frozen=True)
class Attribute:
    key: str
    question_vi: str
    volatility: Volatility
    values: tuple[str, ...]
    #: What a person calls this aspect out loud.
    #:
    #: Deletion requests used `key` at first — "Hãy quên thông tin về city của
    #: Bùi Cường" — and nobody says that. It made the request cross-lingual
    #: against a Vietnamese store and pushed every topic-scoped deletion below
    #: the similarity floor: 2 of 18 requests matched anything. The benchmark
    #: was testing whether the system could bridge its own internal identifier
    #: into another language, which is not a memory capability.
    label_vi: str = ""


#: Deliberately small and concrete. A world of plausible facts about twenty
#: people beats a world of lorem ipsum, because retrieval on lorem ipsum
#: measures string matching.
ATTRIBUTES: tuple[Attribute, ...] = (
    Attribute("job_title", "{name} đang giữ chức vụ gì?", Volatility.FAST,
              ("nhân viên kinh doanh", "trưởng nhóm", "trưởng phòng",
               "phó giám đốc", "giám đốc kỹ thuật"), "chức vụ"),
    Attribute("employer", "{name} đang làm ở công ty nào?", Volatility.MEDIUM,
              ("Locaith", "An Phát", "Bình Minh", "Đại Việt", "Hoà Bình"),
              "nơi làm việc"),
    Attribute("city", "{name} đang sống ở đâu?", Volatility.SLOW,
              ("Hà Nội", "Đà Nẵng", "Cần Thơ", "Hải Phòng", "Huế"),
              "nơi sống"),
    Attribute("phone", "Số điện thoại của {name} là gì?", Volatility.SLOW,
              ("0912345678", "0987654321", "0903111222", "0938777666",
               "0977123456"), "số điện thoại"),
    Attribute("birthday", "{name} sinh ngày nào?", Volatility.IMMUTABLE,
              ("12/03/1990", "05/07/1988", "21/11/1995", "30/01/1992",
               "17/09/1985"), "ngày sinh"),
    Attribute("project", "{name} đang phụ trách dự án nào?", Volatility.FAST,
              ("cổng thanh toán", "kho dữ liệu", "ứng dụng bán hàng",
               "hệ thống OCR", "trang thương mại"), "dự án phụ trách"),
    Attribute("salary", "Lương của {name} là bao nhiêu?", Volatility.VOLATILE,
              ("18 triệu", "25 triệu", "32 triệu", "40 triệu", "55 triệu"),
              "lương"),
)

FIRST_NAMES = ("An", "Bình", "Cường", "Dũng", "Giang", "Hà", "Hùng", "Khanh",
               "Lan", "Minh", "Nam", "Oanh", "Phúc", "Quân", "Sơn", "Thảo",
               "Trang", "Tuấn", "Vy", "Yến")

FAMILY_NAMES = ("Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi")


def tick_to_iso(tick: int) -> str:
    """World time for a tick. Never reads the wall clock."""
    return (EPOCH + timedelta(hours=HOURS_PER_TICK * tick)).isoformat()


@dataclass(frozen=True)
class Subject:
    subject_id: str
    name: str

    def full(self) -> str:
        return self.name


@dataclass
class Claim:
    """One value of one attribute of one subject, and when it held.

    `until` is exclusive: a claim with `since=10, until=40` was true from tick
    10 up to but not including tick 40.
    """

    subject_id: str
    attribute: str
    value: str
    #: When this became true of the world. A correction reaches back, because
    #: the value it replaces was never true and something must hold that span.
    since: int
    #: When the world *said* it — the tick of the event that created this claim.
    #:
    #: Separate from `since` for exactly one reason, and it is not a detail. A
    #: correction at tick 400 backdates `since` to 1, and a question at tick 10
    #: then expects a value nobody had been told yet. Measured: the key wanted
    #: "Bình Minh" at checkpoint 10 while the world's only event so far was
    #: "Hoà Bình" — the system answered correctly and was marked wrong for not
    #: knowing the future.
    #:
    #: `since` answers "when was this true". `asserted_at` answers "when could
    #: anyone have known". A benchmark grades the second.
    asserted_at: int = 0
    until: int | None = None
    #: Set when a CORRECT event says this was never true. Distinct from
    #: superseded: a corrected claim has no historical window at all, and
    #: "what was it in March?" must not return it.
    retracted: bool = False
    forgotten_at: int | None = None
    #: Ticks at which the value was restated. A repeat is evidence of freshness
    #: and never a change of value.
    confirmed_at: list[int] = field(default_factory=list)

    def held_at(self, tick: int) -> bool:
        """Was this true of the world at `tick`. Says nothing about knowing.

        The knowability half lives in `current`/`at`, checked against the tick
        the question is *asked* rather than the tick it is asked about. Asking
        in August about January can use a correction made in March; asking in
        January cannot.
        """
        if self.retracted:
            return False
        if tick < self.since:
            return False
        return self.until is None or tick < self.until


@dataclass
class WorldEvent:
    tick: int
    kind: EventKind
    text: str
    subject_id: str | None = None
    attribute: str | None = None
    value: str | None = None

    @property
    def observed_at(self) -> str:
        return tick_to_iso(self.tick)


class TruthLedger:
    """What was true when, independent of anything the memory system did.

    Ground truth lives here and nowhere else. A benchmark that asks the system
    under test to supply its own answer key cannot fail that system.
    """

    def __init__(self) -> None:
        self.claims: list[Claim] = []

    # -- writing -----------------------------------------------------------

    def assert_(self, subject_id: str, attribute: str, value: str,
                tick: int) -> Claim:
        claim = Claim(subject_id, attribute, value, since=tick,
                      asserted_at=tick)
        self.claims.append(claim)
        return claim

    def supersede(self, subject_id: str, attribute: str, value: str,
                  tick: int) -> Claim:
        """The old value stops being current and stays historically true."""
        for claim in self._slot(subject_id, attribute):
            if claim.until is None and not claim.retracted:
                claim.until = tick
        return self.assert_(subject_id, attribute, value, tick)

    def correct(self, subject_id: str, attribute: str, value: str,
                tick: int) -> Claim:
        """The old value was never true — it is removed from history, not
        closed off. "Who was CTO in January?" must not return it."""
        replaced_since = tick
        for claim in self._slot(subject_id, attribute):
            if claim.until is None and not claim.retracted:
                claim.retracted = True
                replaced_since = min(replaced_since, claim.since)
        # `since` reaches back to cover the span the wrong value occupied;
        # `asserted_at` stays at the moment the correction was actually made,
        # so no question before that tick expects this value.
        claim = Claim(subject_id, attribute, value, since=replaced_since,
                      asserted_at=tick)
        self.claims.append(claim)
        return claim

    def repeat(self, subject_id: str, attribute: str, tick: int) -> None:
        current = self.current(subject_id, attribute, tick)
        if current is not None:
            current.confirmed_at.append(tick)

    def forget(self, subject_id: str, attribute: str, tick: int) -> None:
        for claim in self._slot(subject_id, attribute):
            claim.forgotten_at = tick

    # -- reading -----------------------------------------------------------

    def _slot(self, subject_id: str, attribute: str) -> list[Claim]:
        return [c for c in self.claims
                if c.subject_id == subject_id and c.attribute == attribute]

    def current(self, subject_id: str, attribute: str,
                tick: int) -> Claim | None:
        """What is true at `tick`. None when nothing is, or when forgotten."""
        live = [c for c in self._slot(subject_id, attribute)
                if c.held_at(tick) and c.asserted_at <= tick
                and not (c.forgotten_at is not None and c.forgotten_at <= tick)]
        return max(live, key=lambda c: c.since) if live else None

    def at(self, subject_id: str, attribute: str, when: int,
           asked_at: int) -> Claim | None:
        """What was true at `when`, asked at `asked_at`.

        A forget request erases history too: once forgotten, the historical
        answer is also unavailable. Anything else would make "forget my old
        address" a promise the system quietly declines to keep.
        """
        live = [c for c in self._slot(subject_id, attribute)
                if c.held_at(when) and c.asserted_at <= asked_at
                and not (c.forgotten_at is not None
                         and c.forgotten_at <= asked_at)]
        return max(live, key=lambda c: c.since) if live else None

    def ever(self, subject_id: str, attribute: str, value: str,
             asked_at: int) -> bool:
        return any(
            c.value == value and not c.retracted
            and c.asserted_at <= asked_at
            and not (c.forgotten_at is not None and c.forgotten_at <= asked_at)
            for c in self._slot(subject_id, attribute)
        )

    def is_forgotten(self, subject_id: str, attribute: str,
                     asked_at: int) -> bool:
        slot = self._slot(subject_id, attribute)
        return bool(slot) and all(
            c.forgotten_at is not None and c.forgotten_at <= asked_at
            for c in slot
        )

    def last_confirmed(self, subject_id: str, attribute: str,
                       tick: int) -> int | None:
        claim = self.current(subject_id, attribute, tick)
        if claim is None:
            return None
        return max([claim.since, *claim.confirmed_at])


# --------------------------------------------------------------------------
# generating the world
# --------------------------------------------------------------------------

def make_subjects(count: int, rng: random.Random) -> list[Subject]:
    names = list(FIRST_NAMES)
    rng.shuffle(names)
    out: list[Subject] = []
    for index in range(count):
        given = names[index % len(names)]
        family = FAMILY_NAMES[index % len(FAMILY_NAMES)]
        out.append(Subject(f"S{index:02d}", f"{family} {given}"))
    return out


def _sentence(kind: EventKind, subject: Subject, attribute: Attribute,
              value: str) -> str:
    templates = {
        "job_title": "{n} đang giữ chức {v}.",
        "employer": "{n} làm việc tại công ty {v}.",
        "city": "{n} sống ở {v}.",
        "phone": "Số điện thoại của {n} là {v}.",
        "birthday": "{n} sinh ngày {v}.",
        "project": "{n} phụ trách dự án {v}.",
        "salary": "Lương của {n} là {v}.",
    }
    base = templates[attribute.key].format(n=subject.name, v=value)
    if kind is EventKind.SUPERSEDE:
        return f"Từ hôm nay, {base[0].lower()}{base[1:]}"
    if kind is EventKind.CORRECT:
        return f"Đính chính: thông tin trước là sai, {base[0].lower()}{base[1:]}"
    if kind is EventKind.REPEAT:
        return f"Nhắc lại, {base[0].lower()}{base[1:]}"
    if kind is EventKind.CONTRADICT:
        return f"Theo một nguồn khác, {base[0].lower()}{base[1:]}"
    return base


NOISE_LINES = (
    "Hôm nay trời mưa to.",
    "Cuộc họp dời sang chiều mai.",
    "Máy in ở tầng 2 đang hỏng.",
    "Quán cà phê dưới nhà mở cửa lúc 7 giờ.",
    "Đã gửi báo cáo tuần cho cả nhóm.",
)


def generate(*, ticks: int = 1000, subjects: int = 20,
             seed: int = 20260814) -> tuple[list[WorldEvent], TruthLedger,
                                            list[Subject]]:
    """Build a world and the ledger that knows what happened in it.

    Deterministic for a given seed: the same seed produces byte-identical
    events, and the world clock comes from tick arithmetic rather than the
    wall clock, so a run today and a run next year agree.
    """
    rng = random.Random(seed)
    people = make_subjects(subjects, rng)
    ledger = TruthLedger()
    events: list[WorldEvent] = []
    by_key = {a.key: a for a in ATTRIBUTES}

    #: Slots that have a value already, so later events can act on them.
    seeded: list[tuple[str, str]] = []

    for tick in range(1, ticks + 1):
        subject = rng.choice(people)
        attribute = rng.choice(ATTRIBUTES)
        slot = (subject.subject_id, attribute.key)
        has_value = ledger.current(*slot, tick) is not None

        kind = _pick_kind(rng, tick, ticks, has_value, attribute)

        if kind is EventKind.NOISE:
            events.append(WorldEvent(tick, kind, rng.choice(NOISE_LINES)))
            continue

        if kind is EventKind.FORGET:
            ledger.forget(*slot, tick)
            events.append(WorldEvent(
                tick, kind,
                f"Hãy quên {attribute.label_vi} của {subject.name}.",
                subject.subject_id, attribute.key, None))
            continue

        if kind is EventKind.REPEAT:
            claim = ledger.current(*slot, tick)
            value = claim.value if claim else rng.choice(attribute.values)
            ledger.repeat(*slot, tick)
            events.append(WorldEvent(
                tick, kind, _sentence(kind, subject, attribute, value),
                subject.subject_id, attribute.key, value))
            continue

        current = ledger.current(*slot, tick)
        choices = [v for v in attribute.values
                   if current is None or v != current.value]
        value = rng.choice(choices)

        if kind is EventKind.ASSERT:
            ledger.assert_(*slot, value, tick)
            seeded.append(slot)
        elif kind is EventKind.SUPERSEDE:
            ledger.supersede(*slot, value, tick)
        elif kind is EventKind.CORRECT:
            ledger.correct(*slot, value, tick)
        # CONTRADICT deliberately does not touch the ledger: the world state is
        # unchanged and the truth is now disputed. A system that silently picks
        # one side is doing something the world never justified.

        events.append(WorldEvent(
            tick, kind, _sentence(kind, subject, attribute, value),
            subject.subject_id, attribute.key, value))

    del by_key, seeded
    return events, ledger, people


def _pick_kind(rng: random.Random, tick: int, ticks: int, has_value: bool,
               attribute: Attribute) -> EventKind:
    """What sort of thing happens next.

    Early ticks skew towards establishing facts; later ticks towards changing,
    repeating and forgetting them, because that is the shape of a life and it
    is also what makes the degradation curve mean anything.
    """
    if not has_value:
        return EventKind.ASSERT if rng.random() < 0.85 else EventKind.NOISE

    progress = tick / max(ticks, 1)
    roll = rng.random()

    if attribute.volatility is Volatility.IMMUTABLE:
        # A birthday does not change. It gets repeated, and occasionally
        # someone got it wrong and says so.
        if roll < 0.70:
            return EventKind.REPEAT
        if roll < 0.85:
            return EventKind.CORRECT
        return EventKind.NOISE

    if roll < 0.28:
        return EventKind.REPEAT
    if roll < 0.28 + 0.26 * progress:
        return EventKind.SUPERSEDE
    if roll < 0.62:
        return EventKind.NOISE
    if roll < 0.72:
        return EventKind.CONTRADICT
    if roll < 0.80:
        return EventKind.CORRECT
    if roll < 0.84 and progress > 0.25:
        return EventKind.FORGET
    return EventKind.ASSERT if not has_value else EventKind.REPEAT


def summarise(events: Iterable[WorldEvent]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.kind.value] = counts.get(event.kind.value, 0) + 1
    return counts


__all__ = [
    "ATTRIBUTES",
    "Attribute",
    "Claim",
    "EPOCH",
    "EventKind",
    "HOURS_PER_TICK",
    "Subject",
    "TruthLedger",
    "Volatility",
    "WorldEvent",
    "generate",
    "make_subjects",
    "summarise",
    "tick_to_iso",
]
