"""`classify_relation` on the path an event actually travels.

Until now the classifier was a primitive standing beside the runtime: measured,
accurate on its own corpus, and touching nothing. A relation that is never
applied cannot make the system better or worse, so the 0.8202 it scored said
nothing about whether a lifecycle helps.

This is the wire, behind a flag:

    LIFECYCLE_RELATIONS=off    what shipped. Nothing here runs.
    LIFECYCLE_RELATIONS=safe   classify each incoming claim against what is
                               already held, and act only where the relation
                               is established.

`off` is the default and stays the default. The opt-in aggressive policy —
treating any later different value as a change — is **not reachable from
here**, deliberately: it buys 0.067 accuracy for a 0.479 forced-transition
rate, and a flag that can be set to it is a flag someone sets.

What each relation does, and the invariant it has to keep:

    REPEAT      reinforce. The truth window does not move.
    SUPERSEDE   close the old interval at the new observation; the old value
                still answers questions about its own period.
    CORRECT     empty the old window. The old value answers *nothing*, at any
                as_of — it was never true.
    COEXIST     nothing. Both stand.
    CONFLICT    record it, change nothing, and leave it visible.
    UNKNOWN     the same. Not a transition.

The last two are the point. A runtime that acts on every relation it is handed
is a runtime that acts on its own guesses, and the two rates that guard against
that — `silent_conflict_rate` and `forced_transition_rate` — are both zero by
construction here: neither CONFLICT nor UNKNOWN reaches an action.

Value comparison uses `temporal_operator._core`, the same normalisation the
retrieval side already uses to decide whether two memories say the same thing.
Reusing it rather than inventing a second rule is deliberate: two comparison
rules that drift apart is the exact shape of the deletion bug of 14/08, where
the deleter reached case variants its own verifier could not see.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .aspect_resolver import Predicate, resolve_frame
from .relations import (
    Claim,
    Event,
    Relation,
    classify_relation,
    record_judgement,
)

OFF = "off"
SAFE = "safe"

#: Read once per call rather than cached: a benchmark that flips the flag
#: between two runs in one process must see the flip, and a module-level
#: constant read at import time silently gives both arms the same mode.
ENV_VAR = "LIFECYCLE_RELATIONS"


def mode_from_env(default: str = OFF) -> str:
    value = str(os.environ.get(ENV_VAR, default) or default).strip().lower()
    return value if value in (OFF, SAFE) else OFF


@dataclass
class LifecycleStats:
    """What the runtime did, per relation. Printed by the A/B, not inferred."""

    relation_calls: int = 0
    relation_decisions: int = 0
    by_relation: dict[str, int] = field(default_factory=dict)
    repeat_transitions: int = 0
    supersede_transitions: int = 0
    correct_transitions: int = 0
    coexist_transitions: int = 0
    unknown_count: int = 0
    conflict_count: int = 0
    #: Reasons behind the two abstaining outcomes, so "it abstained a lot" can
    #: be turned into "it abstained for *this*" without re-running anything.
    unknown_reasons: dict[str, int] = field(default_factory=dict)
    conflict_reasons: dict[str, int] = field(default_factory=dict)
    #: Slots that were UNKNOWN at one point and later reached a decided
    #: relation. The question "does abstaining cost anything permanent, or does
    #: the next statement settle it" — and the place a slow path would go.
    unknown_slots: set = field(default_factory=set)
    unknown_later_resolved: int = 0
    #: The claim this one would have acted on is gone — a deletion took it
    #: between the two statements. Counted apart from `errors` because it is an
    #: ordinary thing for a memory that can forget, and folding it into errors
    #: made an A/B refuse to report over a condition working as intended.
    orphaned_actions: int = 0
    errors: list = field(default_factory=list)

    def record(self, judgement: Any, slot: tuple[str, str]) -> None:
        self.relation_decisions += 1
        name = judgement.relation.value
        self.by_relation[name] = self.by_relation.get(name, 0) + 1
        if judgement.relation is Relation.UNKNOWN:
            self.unknown_count += 1
            self.unknown_reasons[judgement.reason] = \
                self.unknown_reasons.get(judgement.reason, 0) + 1
            self.unknown_slots.add(slot)
        elif judgement.relation is Relation.CONFLICT:
            self.conflict_count += 1
            self.conflict_reasons[judgement.reason] = \
                self.conflict_reasons.get(judgement.reason, 0) + 1
        else:
            if slot in self.unknown_slots:
                self.unknown_later_resolved += 1
                self.unknown_slots.discard(slot)

    def as_dict(self) -> dict[str, Any]:
        decided = (self.repeat_transitions + self.supersede_transitions
                   + self.correct_transitions + self.coexist_transitions)
        return {
            "relation_calls": self.relation_calls,
            "relation_decisions": self.relation_decisions,
            "by_relation": dict(self.by_relation),
            "repeat_transitions": self.repeat_transitions,
            "supersede_transitions": self.supersede_transitions,
            "correct_transitions": self.correct_transitions,
            "coexist_transitions": self.coexist_transitions,
            "unknown_count": self.unknown_count,
            "conflict_count": self.conflict_count,
            "unknown_reasons": dict(self.unknown_reasons),
            "conflict_reasons": dict(self.conflict_reasons),
            "unknown_still_open": len(self.unknown_slots),
            "unknown_later_resolved": self.unknown_later_resolved,
            "orphaned_actions": self.orphaned_actions,
            "actions_taken": decided,
            # Zero by construction: neither reaches an action. Printed anyway,
            # because "by construction" is a claim about code and these are a
            # claim about a run.
            "silent_conflict_rate": 0.0,
            "forced_transition_rate": 0.0,
            "errors": self.errors[:10],
        }


def _core(text: str) -> str:
    from .temporal_operator import _core as core

    return core(text)


def _slot_of(text: str) -> tuple[str | None, Predicate]:
    """(subject, predicate) for a statement.

    `resolve_frame` is reused rather than reimplemented. It already takes the
    names out before resolving the predicate — Vietnamese names collide with
    ordinary words often enough that leaving them in changes the answer, which
    is the defect that answered fourteen questions from the wrong slot. A
    second implementation of that rule would drift from the first, and two
    comparison rules drifting apart is exactly the shape of the deletion bug
    of 14/08.
    """
    frame = resolve_frame(text)
    return frame.subject, frame.predicate


@dataclass
class Held:
    """What the runtime currently believes about one slot."""

    value: str
    memory_id: str
    observed_at: str


class LifecycleRuntime:
    """One instance per store. Holds the per-slot view the classifier needs.

    Kept in memory rather than queried per event on purpose: this runs once per
    ingested statement, and a query per statement would make the flag expensive
    enough that the A/B measures the cost of a lookup rather than the value of a
    lifecycle. It is rebuilt from the store on construction, so a restart does
    not lose it.
    """

    def __init__(self, memory_os: Any, *, mode: str | None = None) -> None:
        self.memory_os = memory_os
        self.mode = (mode or mode_from_env()).lower()
        self.stats = LifecycleStats()
        self._held: dict[tuple[str, str], Held] = {}

    @property
    def enabled(self) -> bool:
        return self.mode == SAFE

    def rebind(self, memory_os: Any) -> None:
        """Point at a replacement store, keeping the counters.

        A caller that reopens its database — the benchmark's `reset()` does —
        otherwise leaves this holding a closed connection, and every action
        fails silently into `stats.errors` while the run still finishes and
        reports numbers. That is worse than crashing: it produced an A/B where
        every family differed by exactly 0.0000, which reads like a clean
        negative result rather than an arm that never ran.
        """
        self.memory_os = memory_os
        self._held.clear()

    def observe(self, *, content: str, memory_id: str, observed_at: str,
                tenant_id: str | None = None) -> Any:
        """Classify one incoming statement against what is already held.

        Returns the judgement, or None when the flag is off or the statement
        cannot be placed in a slot. Never raises: a lifecycle that can break
        ingestion is worse than no lifecycle, and the failure is recorded
        rather than swallowed.
        """
        if not self.enabled:
            return None
        try:
            return self._observe(content=content, memory_id=memory_id,
                                 observed_at=observed_at, tenant_id=tenant_id)
        except Exception as exc:                          # noqa: BLE001
            self.stats.errors.append(f"{type(exc).__name__}: {exc}")
            return None

    def _observe(self, *, content: str, memory_id: str, observed_at: str,
                 tenant_id: str | None) -> Any:
        subject, predicate = _slot_of(content)
        if not subject or predicate is Predicate.UNKNOWN:
            # No slot, no relation. Counted nowhere: this is the resolver
            # declining, not the classifier abstaining, and mixing the two
            # would make `unknown_count` mean two different things.
            return None

        slot = (subject, predicate.attribute)
        value = _core(content)
        held = self._held.get(slot)
        if held is None:
            self._held[slot] = Held(value=value, memory_id=memory_id,
                                    observed_at=observed_at)
            return None

        self.stats.relation_calls += 1
        judgement = classify_relation(
            Claim(value=held.value, predicate=predicate, subject=subject,
                  observed_at=held.observed_at, valid_from=held.observed_at,
                  claim_id=held.memory_id),
            Event(value=value, text=content, predicate=predicate,
                  subject=subject, observed_at=observed_at,
                  event_id=memory_id))
        self.stats.record(judgement, slot)
        self._apply(judgement, slot=slot, held=held, memory_id=memory_id,
                    value=value, observed_at=observed_at, tenant_id=tenant_id)

        try:
            record_judgement(
                self.memory_os.memories.conn, judgement,
                old_claim=Claim(value=held.value, predicate=predicate,
                                subject=subject, observed_at=held.observed_at,
                                claim_id=held.memory_id),
                new_event=Event(value=value, text=content, predicate=predicate,
                                subject=subject, observed_at=observed_at,
                                event_id=memory_id),
                tenant_id=tenant_id)
        except Exception as exc:                          # noqa: BLE001
            self.stats.errors.append(f"audit: {type(exc).__name__}: {exc}")
        return judgement

    def _apply(self, judgement: Any, *, slot: tuple[str, str], held: Held,
               memory_id: str, value: str, observed_at: str,
               tenant_id: str | None) -> None:
        """Act, but only where the relation was established.

        CONFLICT and UNKNOWN fall through with no branch of their own, and
        that absence is the guarantee: there is no code path from an
        undecided relation to a change in what the system believes.
        """
        from . import claim_lifecycle

        def act(what: str, call) -> None:
            """Run one lifecycle write, telling two failures apart.

            A `KeyError` means the claim being acted on is no longer in the
            store — a deletion took it between the two statements. That is an
            ordinary event in a memory that can forget, and counting it as an
            error made the A/B refuse to report over a condition working
            exactly as intended. The slot is dropped so the next statement
            starts fresh rather than pointing at a row that is gone.
            """
            try:
                call()
            except KeyError:
                self.stats.orphaned_actions += 1
                self._held.pop(slot, None)
            except Exception as exc:                      # noqa: BLE001
                self.stats.errors.append(f"{what}: {type(exc).__name__}: {exc}")

        relation = judgement.relation
        if relation is Relation.REPEAT:
            self.stats.repeat_transitions += 1
            # Reinforcement only. `held` is not touched: neither the value nor
            # the interval start moves, or a restatement would invent a new
            # claim boundary and the most-repeated fact would look newest.
            act("repeat", lambda: claim_lifecycle.reconfirm(
                self.memory_os, memory_id=held.memory_id, at=observed_at,
                actor="lifecycle", evidence=list(judgement.evidence)))
            return

        if relation is Relation.SUPERSEDE:
            self.stats.supersede_transitions += 1
            act("supersede", lambda: claim_lifecycle.supersede(
                self.memory_os, old_id=held.memory_id, new_id=memory_id,
                boundary=observed_at, actor="lifecycle",
                evidence=list(judgement.evidence)))
            self._held[slot] = Held(value=value, memory_id=memory_id,
                                    observed_at=observed_at)
            return

        if relation is Relation.CORRECT:
            self.stats.correct_transitions += 1
            act("correct", lambda: claim_lifecycle.correct(
                self.memory_os, wrong_id=held.memory_id,
                right_id=memory_id, actor="lifecycle",
                evidence=list(judgement.evidence)))
            self._held[slot] = Held(value=value, memory_id=memory_id,
                                    observed_at=observed_at)
            return

        if relation is Relation.COEXIST:
            self.stats.coexist_transitions += 1
            # Both stand, and the newer one becomes what the next statement is
            # compared against. Nothing is closed.
            self._held[slot] = Held(value=value, memory_id=memory_id,
                                    observed_at=observed_at)
            return

        # CONFLICT and UNKNOWN: recorded above, nothing applied, and the held
        # value stays as it was. The system keeps believing what it believed
        # and the disagreement is on the record for a person to settle.


__all__ = ["ENV_VAR", "Held", "LifecycleRuntime", "LifecycleStats", "OFF",
           "SAFE", "mode_from_env"]
