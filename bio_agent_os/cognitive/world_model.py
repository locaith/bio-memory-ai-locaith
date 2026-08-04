from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import CognitiveMemory, EpistemicStatus, MemoryType


@dataclass(frozen=True)
class WorldFact:
    entity: str
    attribute: str
    value: Any
    memory_id: str
    confidence: float
    valid_from: str | None = None
    valid_to: str | None = None
    epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED


@dataclass
class WorldSnapshot:
    as_of: str
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    provenance: dict[str, dict[str, str]] = field(default_factory=dict)


class WorldModel:
    """Small deterministic world-state projection.

    This is intentionally not an LLM. It keeps facts, validity windows and
    provenance separate from language generation so later planners can reason
    over a stable machine-readable state.
    """

    SUPPORTED_TYPES = {
        MemoryType.WORLD_STATE,
        MemoryType.SEMANTIC,
        MemoryType.RELATIONAL,
        MemoryType.SPATIAL,
        MemoryType.SOCIAL,
    }

    def __init__(self):
        self._facts: list[WorldFact] = []

    def ingest(self, memory: CognitiveMemory) -> bool:
        if memory.memory_type not in self.SUPPORTED_TYPES:
            return False
        sc = memory.structured_content
        entity = str(sc.get("entity", "")).strip()
        attribute = str(sc.get("attribute", "")).strip()
        if not entity or not attribute or "value" not in sc:
            return False
        self._facts.append(
            WorldFact(
                entity=entity,
                attribute=attribute,
                value=sc["value"],
                memory_id=memory.memory_id,
                confidence=memory.confidence,
                valid_from=memory.valid_from,
                valid_to=memory.valid_to,
                epistemic_status=memory.epistemic_status,
            )
        )
        return True

    def snapshot(self, as_of: str | None = None, include_simulated: bool = False) -> WorldSnapshot:
        when = as_of or datetime.now(timezone.utc).isoformat()
        selected: dict[tuple[str, str], WorldFact] = {}
        for fact in self._facts:
            if fact.epistemic_status == EpistemicStatus.SIMULATED and not include_simulated:
                continue
            if fact.valid_from and fact.valid_from > when:
                continue
            if fact.valid_to and when >= fact.valid_to:
                continue
            key = (fact.entity, fact.attribute)
            existing = selected.get(key)
            if existing is None or self._rank(fact) > self._rank(existing):
                selected[key] = fact
        snapshot = WorldSnapshot(as_of=when)
        for (entity, attribute), fact in selected.items():
            snapshot.entities.setdefault(entity, {})[attribute] = fact.value
            snapshot.provenance.setdefault(entity, {})[attribute] = fact.memory_id
        return snapshot

    @staticmethod
    def _rank(fact: WorldFact) -> tuple[str, float]:
        return (fact.valid_from or "", fact.confidence)
