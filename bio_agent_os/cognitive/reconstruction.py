from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .event_store import SQLiteEventStore
from .memory_store import SQLiteMemoryStore
from .models import AccessContext, CognitiveMemory


@dataclass(frozen=True)
class CognitiveReconstruction:
    query: str
    timeline: tuple[dict[str, Any], ...]
    active_claims: tuple[dict[str, Any], ...]
    unresolved_conflicts: tuple[dict[str, Any], ...]
    source_event_ids: tuple[str, ...]


class CognitiveReconstructor:
    """Rebuilds the state surrounding a decision instead of returning top-k text."""

    def __init__(self, events: SQLiteEventStore, memories: SQLiteMemoryStore):
        self.events = events
        self.memories = memories

    def reconstruct(
        self,
        query: str,
        context: AccessContext,
        selected_memories: list[CognitiveMemory],
    ) -> CognitiveReconstruction:
        event_ids: list[str] = []
        active_claims: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        for memory in selected_memories:
            active_claims.append(
                {
                    "memory_id": memory.memory_id,
                    "type": memory.memory_type.value,
                    "claim": memory.content,
                    "epistemic_status": memory.epistemic_status.value,
                    "verification_status": memory.verification_status.value,
                    "valid_from": memory.valid_from,
                    "valid_to": memory.valid_to,
                    "confidence": memory.confidence,
                }
            )
            if memory.counterevidence_event_ids or memory.contradiction_count:
                conflicts.append(
                    {
                        "memory_id": memory.memory_id,
                        "counterevidence_event_ids": list(memory.counterevidence_event_ids),
                        "contradiction_count": memory.contradiction_count,
                    }
                )
            for event_id in [*memory.source_event_ids, *memory.counterevidence_event_ids]:
                if event_id in event_ids:
                    continue
                event = self.events.get(event_id, context.tenant_id)
                if event:
                    event_ids.append(event_id)
                    timeline.append(
                        {
                            "event_id": event.event_id,
                            "observed_at": event.observed_at,
                            "actor": event.actor,
                            "source": event.source,
                            "payload": event.payload,
                            "epistemic_status": event.epistemic_status.value,
                        }
                    )
        timeline.sort(key=lambda item: item["observed_at"])
        return CognitiveReconstruction(
            query=query,
            timeline=tuple(timeline),
            active_claims=tuple(active_claims),
            unresolved_conflicts=tuple(conflicts),
            source_event_ids=tuple(event_ids),
        )
