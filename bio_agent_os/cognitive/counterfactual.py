from __future__ import annotations

from uuid import uuid4
from typing import Any

from .causal import CausalMemoryEngine
from .models import SimulationTrace


class CounterfactualSimulator:
    """Generates explicitly-labelled possible futures from causal memory."""

    def __init__(self, causal: CausalMemoryEngine):
        self.causal = causal

    def simulate(self, premise: str, interventions: dict[str, Any], source_memory_ids: list[str] | None = None) -> SimulationTrace:
        predicted: list[dict[str, Any]] = []
        confidences: list[float] = []
        for cause, enabled in interventions.items():
            if not enabled:
                continue
            for hypothesis in self.causal.strongest_effects(cause):
                predicted.append(
                    {
                        "cause": hypothesis.cause,
                        "effect": hypothesis.effect,
                        "confidence": hypothesis.confidence,
                        "epistemic_status": "simulated",
                    }
                )
                confidences.append(hypothesis.confidence)
        overall = min(confidences) if confidences else 0.0
        return SimulationTrace(
            simulation_id=str(uuid4()),
            premise=premise,
            interventions=dict(interventions),
            predicted_states=tuple(predicted),
            confidence=round(overall, 6),
            source_memory_ids=tuple(source_memory_ids or []),
        )
