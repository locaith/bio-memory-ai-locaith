"""What a new statement does to a claim already held.

Six answers, and the sixth is the one that makes the other five safe:

    REPEAT     the same thing said again
    CORRECT    the old claim was never true
    SUPERSEDE  the old claim was true, and stopped being true
    COEXIST    both are true at once
    CONFLICT   both cannot be true, and nothing here says which
    UNKNOWN    not enough to tell

`old != new -> supersede` is the rule this file exists to replace. It is right
often enough to look like it works and wrong in the two places that matter:

    "Tôi sinh 1990" / "Xin lỗi, 1991"     nobody was born twice. 1990 must
                                          answer *nothing*, at any as_of —
                                          supersession would make it the true
                                          answer for every question about 1990
    "Thích Python"  / "Thích Rust"        both, and killing the first because
                                          it shares a predicate loses a fact
                                          nobody retracted

So the decision is driven by what kind of predicate it is, declared once in
`SEMANTICS`, rather than by comparing two strings:

    cardinality          one value at a time, or many
    temporal_behavior    can the world-truth change, or is it fixed
    coexistence_policy   may two values stand together
    correction_policy    what an unmarked disagreement means
    volatility           how fast it goes stale — sets confidence, not the verdict

**UNKNOWN and CONFLICT are results, not failures.** A classifier that always
picks a winner has not resolved anything; it has moved its errors to where
nobody counts them. Two measurements exist for exactly this and both target
zero: `silent_conflict_rate` (a real disagreement quietly decided) and
`forced_transition_rate` (a truth changed on insufficient evidence). For a
memory meant to last years, not knowing is safer than knowing wrong.

The classifier reads the *text* of the new statement for markers — "đính
chính", "từ hôm nay", "theo một nguồn khác". That is evidence a person
supplied, and using it is the capability. It never reads the benchmark's event
kind, which would be the answer key.

No model and no embedder. Every verdict is a function of two claims, one
declaration table and a handful of phrases, so it is the same verdict next
Tuesday and it can be explained in an audit a year from now.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .aspect_resolver import Predicate

#: Bumped whenever a verdict could change for the same input. Stored on every
#: judgement, because "why did it say that" is unanswerable a year later if the
#: classifier that said it cannot be identified.
CLASSIFIER_VERSION = "1.0.0"


class Relation(str, Enum):
    REPEAT = "repeat"
    CORRECT = "correct"
    SUPERSEDE = "supersede"
    COEXIST = "coexist"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"

    @property
    def changes_truth(self) -> bool:
        """Does acting on this rewrite what the system believes is true?

        `forced_transition_rate` counts these against cases where the evidence
        did not support one. REPEAT and COEXIST add; CONFLICT and UNKNOWN
        record and wait; only these two take something away.
        """
        return self in (Relation.SUPERSEDE, Relation.CORRECT)

    @property
    def resolves(self) -> bool:
        """Does this pick a winner? `silent_conflict_rate` counts these
        against cases that were genuinely undecidable."""
        return self not in (Relation.CONFLICT, Relation.UNKNOWN)


class Cardinality(str, Enum):
    ONE = "one"      # one value holds at a time
    MANY = "many"    # several hold together


class TemporalBehavior(str, Enum):
    IMMUTABLE = "immutable"   # the world-truth cannot change: a birth date
    REVISABLE = "revisable"   # it can: an employer, a city, a salary


class CoexistencePolicy(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class CorrectionPolicy(str, Enum):
    #: An unmarked disagreement is not a correction. Somebody has to say so.
    MARKED_ONLY = "marked_only"
    #: An unmarked disagreement corrects the old value. Not used by any real
    #: predicate here; it exists so a mutant can adopt it and be caught.
    LATEST_CORRECTS = "latest_corrects"


class Volatility(str, Enum):
    IMMUTABLE = "immutable"
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"
    VOLATILE = "volatile"


@dataclass(frozen=True)
class PredicateSemantics:
    """What kind of thing this predicate is. Declared, never inferred."""

    cardinality: Cardinality
    temporal_behavior: TemporalBehavior
    coexistence_policy: CoexistencePolicy
    correction_policy: CorrectionPolicy
    volatility: Volatility

    def as_dict(self) -> dict[str, str]:
        return {"cardinality": self.cardinality.value,
                "temporal_behavior": self.temporal_behavior.value,
                "coexistence_policy": self.coexistence_policy.value,
                "correction_policy": self.correction_policy.value,
                "volatility": self.volatility.value}


def _one(temporal: TemporalBehavior, volatility: Volatility) -> PredicateSemantics:
    return PredicateSemantics(Cardinality.ONE, temporal, CoexistencePolicy.DENY,
                              CorrectionPolicy.MARKED_ONLY, volatility)


#: One row per predicate, and the rows are the argument.
#:
#: `BIRTHDAY` is the one that is not `REVISABLE`, and it is the reason the table
#: exists: every other predicate here can legitimately change, so a rule tuned
#: on them alone reads a corrected birth date as a person being born twice.
#:
#: `PREFERENCE` is the one that is not `ONE`. Liking Rust does not stop you
#: liking Python, and a classifier that has never met a multi-valued predicate
#: will quietly delete one of them.
SEMANTICS: dict[Predicate, PredicateSemantics] = {
    Predicate.EMPLOYER: _one(TemporalBehavior.REVISABLE, Volatility.MEDIUM),
    Predicate.ROLE: _one(TemporalBehavior.REVISABLE, Volatility.FAST),
    Predicate.LOCATION: _one(TemporalBehavior.REVISABLE, Volatility.SLOW),
    Predicate.PHONE: _one(TemporalBehavior.REVISABLE, Volatility.SLOW),
    Predicate.PROJECT: _one(TemporalBehavior.REVISABLE, Volatility.FAST),
    Predicate.SALARY: _one(TemporalBehavior.REVISABLE, Volatility.VOLATILE),
    Predicate.BIRTHDAY: _one(TemporalBehavior.IMMUTABLE, Volatility.IMMUTABLE),
    Predicate.PREFERENCE: PredicateSemantics(
        Cardinality.MANY, TemporalBehavior.REVISABLE, CoexistencePolicy.ALLOW,
        CorrectionPolicy.MARKED_ONLY, Volatility.SLOW),
}

#: What an unlisted predicate gets. Deliberately the most cautious row
#: available: unknown shape means the classifier declines rather than assumes.
#: A predicate added to the enum and forgotten here produces UNKNOWN verdicts,
#: which is visible in the metrics — the alternative is silent wrong ones.
UNDECLARED = PredicateSemantics(
    Cardinality.ONE, TemporalBehavior.IMMUTABLE, CoexistencePolicy.DENY,
    CorrectionPolicy.MARKED_ONLY, Volatility.SLOW)


def semantics_for(predicate: Predicate | str) -> PredicateSemantics:
    if isinstance(predicate, str):
        predicate = next((p for p in Predicate if p.attribute == predicate),
                         Predicate.UNKNOWN)
    return SEMANTICS.get(predicate, UNDECLARED)


# --------------------------------------------------------------------------
# what a person said, as evidence
# --------------------------------------------------------------------------

#: "The earlier statement was wrong." Not "things changed".
CORRECTION_MARKERS: tuple[str, ...] = (
    "đính chính", "xin lỗi", "nhầm", "thông tin trước là sai", "trước là sai",
    "sai rồi", "thực ra", "thật ra", "không phải", "sửa lại",
    "correction", "actually", "i was wrong", "sorry", "my mistake",
)

#: "It changed, from a moment." Not "the earlier statement was wrong".
CHANGE_MARKERS: tuple[str, ...] = (
    "từ hôm nay", "từ nay", "từ tháng", "từ ngày", "bắt đầu từ", "kể từ",
    "chuyển sang", "chuyển đến", "chuyển về", "vừa đổi", "đã đổi", "mới đổi",
    "bây giờ", "hiện tại", "từ giờ",
    "from today", "as of", "starting", "moved to", "switched to", "now works",
)

#: "Somebody else says otherwise." A competing source, not a change.
ALTERNATIVE_SOURCE_MARKERS: tuple[str, ...] = (
    "theo một nguồn khác", "nguồn khác", "có nguồn nói", "nghe nói",
    "một số người nói", "có người bảo", "chưa rõ", "không chắc",
    "another source", "someone said", "unconfirmed", "reportedly",
)

#: "Same as before." Only strengthens what is already held.
REPEAT_MARKERS: tuple[str, ...] = (
    "nhắc lại", "vẫn", "như đã nói", "như đã báo", "xác nhận lại",
    "again", "still", "as mentioned", "confirming",
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip().lower()


def _markers(text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    folded = _fold(text)
    return tuple(p for p in phrases if p in folded)


# --------------------------------------------------------------------------
# the inputs
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    """A claim already held. Only what a verdict can legitimately depend on."""

    value: str
    predicate: Predicate | str = Predicate.UNKNOWN
    subject: str = ""
    #: When it became true of the world.
    valid_from: str | None = None
    #: When the system learned it. Distinct from `valid_from` on purpose: a
    #: correction made today about a fact from January is later in one and
    #: earlier in the other, and confusing them is how a benchmark ends up
    #: asking questions nobody could answer.
    observed_at: str | None = None
    claim_id: str = ""


@dataclass(frozen=True)
class Event:
    """A new statement, as it arrived."""

    value: str
    text: str = ""
    predicate: Predicate | str = Predicate.UNKNOWN
    subject: str = ""
    observed_at: str | None = None
    valid_from: str | None = None
    event_id: str = ""
    source: str = ""


@dataclass(frozen=True)
class Judgement:
    relation: Relation
    reason: str
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0
    classifier_version: str = CLASSIFIER_VERSION
    predicate: str = ""
    semantics: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"relation": self.relation.value, "reason": self.reason,
                "evidence": list(self.evidence), "confidence": self.confidence,
                "classifier_version": self.classifier_version,
                "predicate": self.predicate, "semantics": self.semantics}


def _predicate_of(thing: Predicate | str) -> Predicate:
    if isinstance(thing, Predicate):
        return thing
    return next((p for p in Predicate if p.attribute == thing),
                Predicate.UNKNOWN)


def _same_value(a: str, b: str) -> bool:
    return _fold(a) == _fold(b)


def _confidence(rule: str, semantics: PredicateSemantics) -> float:
    """How much to trust this verdict, given what kind of predicate it is.

    Volatility earns its place here rather than in the verdict. A supersession
    on a salary is ordinary; the same on a birth date would be extraordinary,
    and the verdict for that case is not SUPERSEDE at all. Confidence never
    turns one relation into another — it says how much weight downstream should
    give a relation already decided on other grounds.
    """
    base = {
        "same_value": 0.95,
        "explicit_correction": 0.9,
        "explicit_change": 0.9,
        "alternative_source": 0.85,
        "immutable_disagreement": 0.8,
        "coexistence_allowed": 0.85,
        "not_later": 0.75,
        "revisable_single_valued": 0.6,
        # The four ways of not knowing. Zero, so that a caller ranking by
        # confidence can never prefer an absent verdict to a weak one.
        "no_rule_applies": 0.0,
        "different_predicate": 0.0,
        "different_subject": 0.0,
        "undeclared_predicate": 0.0,
        "no_observation_times": 0.0,
        "unmarked_value_change": 0.0,
    }.get(rule, 0.5)
    if rule == "revisable_single_valued":
        # A later different value is a likelier change the faster the attribute
        # moves. Still never certain, which is why it tops out below the marked
        # cases: somebody saying "từ hôm nay" is better evidence than a guess
        # about how often job titles change.
        base += {Volatility.VOLATILE: 0.15, Volatility.FAST: 0.1,
                 Volatility.MEDIUM: 0.05}.get(semantics.volatility, 0.0)
    return round(min(base, 1.0), 3)


def classify_relation(old_claim: Claim, new_event: Event,
                      context: dict[str, Any] | None = None) -> Judgement:
    """What does `new_event` do to `old_claim`?

    The order below is the argument, and each step is a thing that was
    *established*, not a default:

         1. not comparable                     -> UNKNOWN
         2. the same value                     -> REPEAT
         3. somebody said it was wrong         -> CORRECT
         4. somebody else says otherwise       -> CONFLICT
         5. the predicate's shape is undeclared-> UNKNOWN
         6. the truth cannot change            -> CONFLICT
         7. several values may hold            -> COEXIST
         8. neither statement can be ordered   -> UNKNOWN
         9. the new one is not later           -> CONFLICT
        10. one value, and it can change       -> SUPERSEDE
        11. nothing above applies              -> UNKNOWN

    UNKNOWN and CONFLICT are kept apart deliberately, and steps 5 and 8 are why
    the distinction is worth the extra branches:

        CONFLICT  the two cannot both be true, and nothing says which wins
        UNKNOWN   it is not even established that they disagree

    Two values with no observation times might be one fact stated twice at
    different moments; calling that a conflict asserts an incompatibility
    nobody demonstrated. A predicate whose shape was never declared might be
    multi-valued, in which case there is no disagreement at all. Both are
    honest gaps, and both are counted.
    """
    context = context or {}
    predicate = _predicate_of(old_claim.predicate)
    new_predicate = _predicate_of(new_event.predicate)
    rules = semantics_for(predicate)
    common = {"predicate": predicate.attribute or "unknown",
              "semantics": rules.as_dict()}

    def verdict(relation: Relation, rule: str, *evidence: str) -> Judgement:
        return Judgement(relation=relation, reason=rule, evidence=tuple(evidence),
                         confidence=_confidence(rule, rules), **common)

    # 1. Two statements about different things say nothing about each other.
    if new_predicate is not Predicate.UNKNOWN and new_predicate is not predicate:
        return verdict(Relation.UNKNOWN, "different_predicate",
                       f"cũ={predicate.name}", f"mới={new_predicate.name}")
    if old_claim.subject and new_event.subject and \
            _fold(old_claim.subject) != _fold(new_event.subject):
        return verdict(Relation.UNKNOWN, "different_subject",
                       f"cũ={old_claim.subject}", f"mới={new_event.subject}")

    correction = _markers(new_event.text, CORRECTION_MARKERS)
    change = _markers(new_event.text, CHANGE_MARKERS)
    alternative = _markers(new_event.text, ALTERNATIVE_SOURCE_MARKERS)

    # 2. Said again. True whatever the markers say, and whatever the predicate
    #    is — a repeated value is not a second value.
    if _same_value(old_claim.value, new_event.value):
        said_again = _markers(new_event.text, REPEAT_MARKERS)
        return verdict(Relation.REPEAT, "same_value",
                       f"giá trị không đổi: {old_claim.value!r}",
                       *(f"dấu hiệu nhắc lại: {m!r}" for m in said_again))

    # 3. Somebody said the earlier statement was wrong. This outranks every
    #    rule below, including immutability — a corrected birth date is the
    #    textbook CORRECT, not a conflict.
    if correction:
        return verdict(Relation.CORRECT, "explicit_correction",
                       *(f"dấu hiệu đính chính: {m!r}" for m in correction))

    # 4. A competing source, unresolved. Checked before the change marker
    #    because "theo một nguồn khác, bây giờ anh ấy ở Huế" is a rumour about
    #    a change, not a change.
    if alternative:
        return verdict(Relation.CONFLICT, "alternative_source",
                       *(f"nguồn khác: {m!r}" for m in alternative))

    # 5. Nobody declared what shape this predicate is. It might hold many
    #    values, in which case there is no disagreement to resolve. Saying
    #    UNKNOWN keeps the gap visible in the metrics; borrowing the cautious
    #    default and calling it CONFLICT would report an incompatibility that
    #    was never established.
    if _predicate_undeclared(predicate):
        return verdict(Relation.UNKNOWN, "undeclared_predicate",
                       f"không có dòng nào khai báo {predicate.name}")

    # 6. The world-truth cannot change, and the two disagree. One of them is
    #    wrong and nothing here says which.
    if rules.temporal_behavior is TemporalBehavior.IMMUTABLE:
        if rules.correction_policy is CorrectionPolicy.LATEST_CORRECTS:
            return verdict(Relation.CORRECT, "explicit_correction",
                           "chính sách: giá trị mới sửa giá trị cũ")
        return verdict(Relation.CONFLICT, "immutable_disagreement",
                       f"{predicate.name} không thể đổi theo thời gian",
                       f"cũ={old_claim.value!r}", f"mới={new_event.value!r}")

    # 7. Several values may hold at once. Nobody retracted the first.
    if rules.cardinality is Cardinality.MANY and \
            rules.coexistence_policy is CoexistencePolicy.ALLOW:
        return verdict(Relation.COEXIST, "coexistence_allowed",
                       f"{predicate.name} nhận nhiều giá trị cùng lúc")

    # 8. Neither can be placed after the other. Two values for one slot look
    #    incompatible, but without an order they may equally be one fact
    #    restated — the disagreement is not established, so it is not claimed.
    order = _ordering(old_claim, new_event)
    if order == "unorderable":
        return verdict(Relation.UNKNOWN, "no_observation_times",
                       f"cũ quan sát lúc {old_claim.observed_at!r}",
                       f"mới quan sát lúc {new_event.observed_at!r}")

    # 9. A change happens *after* what it changes. Ordered and not later, the
    #    two are simply disagreeing.
    if order == "not_later":
        return verdict(Relation.CONFLICT, "not_later",
                       f"cũ quan sát lúc {old_claim.observed_at!r}",
                       f"mới quan sát lúc {new_event.observed_at!r}")

    # 10. One value at a time, it can change, the new one is later — and
    #     somebody said it changed.
    #
    #     The marker is required, and measurement is why. An earlier version
    #     treated "later and different" as sufficient. On wording held out of
    #     the marker tables that produced, out of 312 cases:
    #
    #         silent_conflict_rate     1.000   rumours became truth
    #         forced_transition_rate   1.000   corrections became supersessions
    #
    #     "Lúc nãy tôi ghi lộn: …" and "Có tin đồn là …" and "Từ hôm nay …"
    #     all arrive as a later, different value. Reading the third meaning
    #     into all three is not a fallback, it is a guess with a 47% error
    #     rate — and the two it gets wrong both write a value into history
    #     that nobody asserted.
    #
    #     The obvious repair — adding those phrases to the tables — is tuning
    #     to the test set, and the next unseen phrase fails identically.
    if rules.cardinality is Cardinality.ONE and \
            rules.temporal_behavior is TemporalBehavior.REVISABLE:
        if change:
            return verdict(
                Relation.SUPERSEDE, "explicit_change",
                *(f"dấu hiệu thay đổi: {m!r}" for m in change),
                f"{predicate.name} đổi được và giữ một giá trị")
        if context.get("unmarked_change") == "supersede":
            # A caller may decide that a plain later assertion means a change.
            # It is a policy about a domain, not a fact about a sentence, so it
            # is opt-in and it is recorded in the reason.
            return verdict(
                Relation.SUPERSEDE, "revisable_single_valued",
                f"{predicate.name} đổi được, giữ một giá trị, cái mới muộn hơn",
                "chính sách của caller: coi lời khẳng định muộn hơn là thay đổi")
        return verdict(
            Relation.UNKNOWN, "unmarked_value_change",
            f"cũ={old_claim.value!r}", f"mới={new_event.value!r}",
            "muộn hơn và khác, nhưng không có gì nói đây là thay đổi, đính "
            "chính hay tin đồn")

    # 11. Nothing established a relation.
    return verdict(Relation.UNKNOWN, "no_rule_applies",
                   f"cũ={old_claim.value!r}", f"mới={new_event.value!r}")


def _predicate_undeclared(predicate: Predicate) -> bool:
    return predicate is Predicate.UNKNOWN or predicate not in SEMANTICS


def _ordering(old_claim: Claim, new_event: Event) -> str:
    """`later`, `not_later`, or `unorderable`.

    Compares observation times — when each was *learned* — because that is what
    orders statements. `valid_from` orders the world, and a correction made
    today about January is earlier in world time while being later in belief
    time. Ordering on the wrong one is how a backdated correction came to look
    like a supersession twice on this project.

    Three outcomes rather than a boolean, because "not after" and "cannot tell"
    lead to different verdicts and collapsing them turns every untimed pair
    into a conflict that was never demonstrated.
    """
    old_at, new_at = old_claim.observed_at, new_event.observed_at
    if old_at is None or new_at is None:
        return "unorderable"
    return "later" if str(new_at) > str(old_at) else "not_later"


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

JUDGEMENTS_TABLE = "claim_relation_judgements"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {JUDGEMENTS_TABLE} (
    judgement_id       TEXT PRIMARY KEY,
    tenant_id          TEXT,
    claim_a            TEXT NOT NULL,
    claim_b            TEXT NOT NULL,
    predicate          TEXT NOT NULL,
    relation           TEXT NOT NULL,
    reason             TEXT NOT NULL,
    evidence_json      TEXT NOT NULL,
    semantics_json     TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    confidence         REAL NOT NULL,
    observed_at        TEXT,
    valid_from         TEXT,
    valid_to           TEXT,
    constitution_version TEXT,
    recorded_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{JUDGEMENTS_TABLE}_claims
    ON {JUDGEMENTS_TABLE}(claim_a, claim_b);
CREATE INDEX IF NOT EXISTS idx_{JUDGEMENTS_TABLE}_relation
    ON {JUDGEMENTS_TABLE}(relation, recorded_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def record_judgement(conn: sqlite3.Connection, judgement: Judgement, *,
                     old_claim: Claim, new_event: Event,
                     tenant_id: str | None = None,
                     valid_to: str | None = None) -> str:
    """Write down a verdict and everything needed to argue with it later.

    Ids and enum values only, plus the two values being compared. Content that
    a deletion would have to reach does not belong in an audit table — the
    lifecycle transition log learned this and `DERIVED_TABLES` in `forgetting`
    names it for that reason. Values are the exception and are stored on
    purpose: without them the row cannot be re-judged, and an audit nobody can
    re-run is a receipt rather than a record.
    """
    from bio_agent_os.memory_constitution.laws import CONSTITUTION_VERSION

    ensure_schema(conn)
    judgement_id = uuid4().hex
    conn.execute(
        f"""INSERT INTO {JUDGEMENTS_TABLE} (
                judgement_id, tenant_id, claim_a, claim_b, predicate, relation,
                reason, evidence_json, semantics_json, classifier_version,
                confidence, observed_at, valid_from, valid_to,
                constitution_version, recorded_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (judgement_id, tenant_id,
         old_claim.claim_id or old_claim.value,
         new_event.event_id or new_event.value,
         judgement.predicate, judgement.relation.value, judgement.reason,
         json.dumps(list(judgement.evidence), ensure_ascii=False),
         json.dumps(judgement.semantics, ensure_ascii=False),
         judgement.classifier_version, judgement.confidence,
         new_event.observed_at, new_event.valid_from or old_claim.valid_from,
         valid_to, CONSTITUTION_VERSION,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return judgement_id


def judgements(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict[str, Any]]:
    ensure_schema(conn)
    cursor = conn.execute(
        f"SELECT * FROM {JUDGEMENTS_TABLE} ORDER BY recorded_at DESC LIMIT ?",
        (limit,))
    names = [c[0] for c in cursor.description]
    out = []
    for row in cursor.fetchall():
        record = dict(zip(names, row))
        record["evidence"] = json.loads(record.pop("evidence_json") or "[]")
        record["semantics"] = json.loads(record.pop("semantics_json") or "{}")
        out.append(record)
    return out


__all__ = [
    "CLASSIFIER_VERSION",
    "Cardinality",
    "Claim",
    "CoexistencePolicy",
    "CorrectionPolicy",
    "Event",
    "Judgement",
    "PredicateSemantics",
    "Relation",
    "SEMANTICS",
    "TemporalBehavior",
    "Volatility",
    "classify_relation",
    "ensure_schema",
    "judgements",
    "record_judgement",
    "semantics_for",
]
