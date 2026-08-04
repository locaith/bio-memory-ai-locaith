from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class CausalHypothesis:
    cause: str
    effect: str
    support: int = 0
    counterexamples: int = 0
    interventions: int = 0
    contexts: list[dict[str, Any]] = field(default_factory=list)
    hypothesis_id: str = field(default_factory=lambda: str(uuid4()))

    @property
    def confidence(self) -> float:
        # Beta(1,1) posterior with a small intervention bonus.
        base = (self.support + 1) / (self.support + self.counterexamples + 2)
        return round(min(base + min(self.interventions * 0.04, 0.16), 0.99), 6)


class CausalMemoryEngine:
    """Evidence tracker for causal hypotheses.

    It does not claim causal truth from temporal ordering alone. Confidence
    grows through repeated support, explicit interventions and absence of
    counterexamples.
    """

    def __init__(self):
        self._hypotheses: dict[tuple[str, str], CausalHypothesis] = {}

    def observe(
        self,
        cause: str,
        effect: str,
        *,
        effect_observed: bool,
        intervention: bool = False,
        context: dict[str, Any] | None = None,
    ) -> CausalHypothesis:
        key = (cause.strip().lower(), effect.strip().lower())
        hypothesis = self._hypotheses.setdefault(key, CausalHypothesis(cause=cause, effect=effect))
        if effect_observed:
            hypothesis.support += 1
        else:
            hypothesis.counterexamples += 1
        if intervention:
            hypothesis.interventions += 1
        if context:
            hypothesis.contexts.append(dict(context))
        return hypothesis

    def get(self, cause: str, effect: str) -> CausalHypothesis | None:
        return self._hypotheses.get((cause.strip().lower(), effect.strip().lower()))

    def strongest_effects(self, cause: str, minimum_confidence: float = 0.55) -> list[CausalHypothesis]:
        normalized = cause.strip().lower()
        matches = [h for (c, _), h in self._hypotheses.items() if c == normalized and h.confidence >= minimum_confidence]
        return sorted(matches, key=lambda h: (-h.confidence, -h.support, h.effect))
