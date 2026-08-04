from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from bio_agent_os.cognitive.models import SecurityLabel, utc_now


class StorageTier(str, Enum):
    HOT = "hot"          # active prompt/prefix cache
    WARM = "warm"        # RAM/local cache
    COLD = "cold"        # NVMe/database
    ARCHIVE = "archive"  # durable, slow, rarely used
    QUARANTINE = "quarantine"


class ContextBlockKind(str, Enum):
    GOAL = "goal"
    IDENTITY = "identity"
    POLICY = "policy"
    FACT = "fact"
    EPISODE = "episode"
    PROCEDURE = "procedure"
    WORLD_STATE = "world_state"
    SELF_MODEL = "self_model"
    TOOL_STATE = "tool_state"
    REASONING_CHECKPOINT = "reasoning_checkpoint"
    SIMULATION = "simulation"
    EVIDENCE = "evidence"


@dataclass
class ContextBlock:
    tenant_id: str
    kind: ContextBlockKind
    content: str
    source_memory_ids: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    workspace_id: str | None = None
    block_id: str = field(default_factory=lambda: str(uuid4()))
    content_hash: str = ""
    version: int = 1
    token_count: int = 0
    trust_score: float = 0.5
    relevance_score: float = 0.0
    importance: float = 0.5
    latency_sensitivity: float = 0.5
    recomputation_cost: float = 0.5
    expected_reuse: float = 0.5
    tier: StorageTier = StorageTier.COLD
    security_label: SecurityLabel = SecurityLabel.INTERNAL
    expires_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    last_accessed_at: str | None = None
    access_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["tier"] = self.tier.value
        data["security_label"] = self.security_label.value
        return data


@dataclass(frozen=True)
class ContextPacket:
    tenant_id: str
    query: str
    goal: str | None
    blocks: tuple[ContextBlock, ...]
    packet_id: str = field(default_factory=lambda: str(uuid4()))
    token_budget: int = 4096
    estimated_tokens: int = 0
    omitted_block_ids: tuple[str, ...] = ()
    provenance_memory_ids: tuple[str, ...] = ()
    compiled_at: str = field(default_factory=utc_now)
    cache_key: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        parts: list[str] = []
        if self.goal:
            parts.append(f"# Active goal\n{self.goal}")
        grouped: dict[str, list[str]] = {}
        for block in self.blocks:
            grouped.setdefault(block.kind.value, []).append(block.content)
        for kind, values in grouped.items():
            parts.append(f"# {kind.replace('_', ' ').title()}\n" + "\n".join(f"- {v}" for v in values))
        return "\n\n".join(parts)


@dataclass(frozen=True)
class AgentCheckpoint:
    tenant_id: str
    agent_id: str
    workspace_id: str | None
    goal: str
    completed_steps: tuple[str, ...]
    pending_steps: tuple[str, ...]
    open_hypotheses: tuple[str, ...]
    uncertainties: tuple[str, ...]
    tool_state: dict[str, Any]
    world_state: dict[str, Any]
    self_state: dict[str, Any]
    active_memory_ids: tuple[str, ...]
    checkpoint_id: str = field(default_factory=lambda: str(uuid4()))
    parent_checkpoint_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrefetchPlan:
    query: str
    predicted_intent: str
    requested_memory_types: tuple[str, ...]
    context_state: dict[str, Any]
    limit: int = 8
    reason: str = ""
