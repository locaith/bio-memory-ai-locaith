from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .models import (
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    ExecutionOutcome,
    MemoryType,
    TrustTier,
    VerificationStatus,
)


@dataclass(frozen=True)
class DreamReport:
    candidate_count: int
    rejected_patterns: tuple[dict[str, Any], ...]
    candidates: tuple[CognitiveMemory, ...]


class DreamEngine:
    """Offline consolidation with counterexample gates.

    The engine never promotes from narrative similarity alone. A procedure
    needs repeated verifier-passed success, a bounded failure ratio and a
    stable step sequence.
    """

    def consolidate_procedures(
        self,
        tenant_id: str,
        outcomes: list[ExecutionOutcome],
        source_event_ids: list[str],
        *,
        minimum_support: int = 3,
        maximum_failure_ratio: float = 0.25,
    ) -> DreamReport:
        groups: dict[str, list[ExecutionOutcome]] = defaultdict(list)
        for outcome in outcomes:
            if outcome.verifier_passed:
                groups[outcome.goal.strip().lower()].append(outcome)

        candidates: list[CognitiveMemory] = []
        rejected: list[dict[str, Any]] = []
        for normalized_goal, group in groups.items():
            successful = [item for item in group if item.success]
            failed = [item for item in group if not item.success]
            total = len(group)
            ratio = len(failed) / total if total else 1.0
            step_variants: dict[tuple[str, ...], int] = defaultdict(int)
            for item in successful:
                step_variants[item.steps] += 1
            best_steps, best_support = max(step_variants.items(), key=lambda x: x[1], default=((), 0))
            if best_support < minimum_support or ratio > maximum_failure_ratio:
                rejected.append(
                    {
                        "goal": normalized_goal,
                        "support": best_support,
                        "failure_ratio": round(ratio, 6),
                        "reason": "insufficient_support_or_counterexamples",
                    }
                )
                continue
            representative = next(item for item in successful if item.steps == best_steps)
            confidence = min(0.55 + best_support * 0.08 - ratio * 0.30, 0.97)
            candidates.append(
                CognitiveMemory(
                    tenant_id=tenant_id,
                    memory_type=MemoryType.PROCEDURAL,
                    content=f"Verified procedure for {representative.goal}: " + " -> ".join(best_steps),
                    source_event_ids=list(source_event_ids),
                    structured_content={
                        "goal": representative.goal,
                        "steps": list(best_steps),
                        "support_count": best_support,
                        "verified_failures": len(failed),
                        "failure_ratio": ratio,
                        "success_check": representative.result_summary,
                    },
                    confidence=confidence,
                    importance=0.80,
                    salience=0.72,
                    utility=0.92,
                    trust_tier=TrustTier.TRUSTED_SYSTEM,
                    reinforcement_count=best_support,
                    contradiction_count=len(failed),
                    lifecycle_state=BeliefState.STABLE if best_support >= 5 and not failed else BeliefState.REINFORCED,
                    epistemic_status=EpistemicStatus.INFERRED,
                    verification_status=VerificationStatus.MACHINE_CHECKED,
                    metadata={"compiler": "dream-procedure-v2", "counterexample_gate": True},
                )
            )
        return DreamReport(candidate_count=len(candidates), rejected_patterns=tuple(rejected), candidates=tuple(candidates))
