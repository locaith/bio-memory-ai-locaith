from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .models import (
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    MemoryType,
    TrustTier,
    VerificationStatus,
)


@dataclass
class ExecutionTrace:
    goal: str
    steps: list[str]
    errors: list[str]
    retries: int
    result: str
    verified: bool
    metadata: dict[str, Any]
    success: bool = True


class ProceduralMemoryCompiler:
    """Deterministic compiler for verified execution traces.

    Failed verified traces count as counterexamples. LLM enrichment can be
    layered on later, but promotion safety must never rely on an LLM alone.
    """

    def compile(self, tenant_id: str, event_ids: list[str], traces: list[ExecutionTrace]) -> CognitiveMemory | None:
        checked = [t for t in traces if t.verified]
        successful = [t for t in checked if t.success]
        failures = [t for t in checked if not t.success]
        if len(successful) < 2:
            return None
        goals = Counter(t.goal.strip().lower() for t in successful)
        common_goal, support = goals.most_common(1)[0]
        relevant_failures = [t for t in failures if t.goal.strip().lower() == common_goal]
        failure_ratio = len(relevant_failures) / max(support + len(relevant_failures), 1)
        if failure_ratio > 0.25:
            return None
        step_patterns = Counter(tuple(t.steps) for t in successful if t.goal.strip().lower() == common_goal)
        best_steps, sequence_support = step_patterns.most_common(1)[0]
        if sequence_support < 2:
            return None
        representative = next(t for t in successful if tuple(t.steps) == best_steps)
        confidence = min(0.55 + sequence_support * 0.10 - failure_ratio * 0.30, 0.95)
        return CognitiveMemory(
            tenant_id=tenant_id,
            memory_type=MemoryType.PROCEDURAL,
            content=f"Procedure for {representative.goal}: " + " -> ".join(best_steps),
            structured_content={
                "goal": representative.goal,
                "steps": list(best_steps),
                "known_errors": sorted(set(e for t in successful for e in t.errors)),
                "validation": representative.result,
                "support_count": sequence_support,
                "counterexample_count": len(relevant_failures),
                "failure_ratio": failure_ratio,
            },
            source_event_ids=event_ids,
            confidence=confidence,
            importance=0.75,
            salience=0.70,
            utility=0.90,
            trust_tier=TrustTier.TRUSTED_SYSTEM,
            reinforcement_count=sequence_support,
            contradiction_count=len(relevant_failures),
            lifecycle_state=BeliefState.REINFORCED if sequence_support < 4 else BeliefState.STABLE,
            epistemic_status=EpistemicStatus.INFERRED,
            verification_status=VerificationStatus.MACHINE_CHECKED,
            metadata={"compiler": "procedural-v2", "verified_only": True, "counterexample_gate": True},
        )
