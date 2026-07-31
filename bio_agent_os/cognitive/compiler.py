from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .models import BeliefState, CognitiveMemory, MemoryType, TrustTier


@dataclass
class ExecutionTrace:
    goal: str
    steps: list[str]
    errors: list[str]
    retries: int
    result: str
    verified: bool
    metadata: dict[str, Any]


class ProceduralMemoryCompiler:
    """Deterministic compiler for verified execution traces.

    LLM enrichment can be layered on later, but promotion safety must not rely
    on an LLM alone.
    """

    def compile(self, tenant_id: str, event_ids: list[str], traces: list[ExecutionTrace]) -> CognitiveMemory | None:
        successful = [t for t in traces if t.verified]
        if len(successful) < 2:
            return None
        goals = Counter(t.goal.strip().lower() for t in successful)
        common_goal, support = goals.most_common(1)[0]
        representative = next(t for t in successful if t.goal.strip().lower() == common_goal)
        confidence = min(0.55 + support * 0.12, 0.95)
        return CognitiveMemory(
            tenant_id=tenant_id,
            memory_type=MemoryType.PROCEDURAL,
            content=f"Procedure for {representative.goal}: " + " -> ".join(representative.steps),
            structured_content={
                "goal": representative.goal,
                "steps": representative.steps,
                "known_errors": sorted(set(e for t in successful for e in t.errors)),
                "validation": representative.result,
                "support_count": support,
            },
            source_event_ids=event_ids,
            confidence=confidence,
            importance=0.75,
            salience=0.70,
            utility=0.90,
            trust_tier=TrustTier.TRUSTED_SYSTEM,
            reinforcement_count=support,
            lifecycle_state=BeliefState.REINFORCED if support < 4 else BeliefState.STABLE,
            metadata={"compiler": "procedural-v1", "verified_only": True},
        )
