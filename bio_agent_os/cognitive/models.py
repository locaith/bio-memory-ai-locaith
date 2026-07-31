from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryType(str, Enum):
    SENSORY = "sensory"
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    BELIEF = "belief"
    IDENTITY = "identity"
    RELATIONAL = "relational"
    CAUSAL = "causal"
    COUNTERFACTUAL = "counterfactual"
    POLICY = "policy"
    EXCEPTION = "exception"


class BeliefState(str, Enum):
    PROPOSED = "proposed"
    REINFORCED = "reinforced"
    STABLE = "stable"
    CHALLENGED = "challenged"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class TrustTier(IntEnum):
    UNTRUSTED_EXTERNAL = 0
    UNVERIFIED_USER = 1
    AGENT_OBSERVATION = 2
    TRUSTED_SYSTEM = 3
    HUMAN_APPROVED = 4
    SIGNED_POLICY = 5


class SecurityLabel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class AccessContext:
    tenant_id: str
    agent_id: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    roles: tuple[str, ...] = ()
    purpose: str | None = None
    max_security_label: SecurityLabel = SecurityLabel.RESTRICTED


@dataclass(frozen=True)
class EventRecord:
    tenant_id: str
    actor: str
    source: str
    payload: dict[str, Any]
    workspace_id: str | None = None
    trust_tier: TrustTier = TrustTier.AGENT_OBSERVATION
    security_label: SecurityLabel = SecurityLabel.INTERNAL
    valid_from: str | None = None
    valid_to: str | None = None
    observed_at: str = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    checksum: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trust_tier"] = int(self.trust_tier)
        data["security_label"] = self.security_label.value
        return data


@dataclass
class CognitiveMemory:
    tenant_id: str
    memory_type: MemoryType
    content: str
    source_event_ids: list[str]
    workspace_id: str | None = None
    structured_content: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    importance: float = 0.5
    salience: float = 0.5
    utility: float = 0.5
    trust_tier: TrustTier = TrustTier.AGENT_OBSERVATION
    security_label: SecurityLabel = SecurityLabel.INTERNAL
    valid_from: str | None = None
    valid_to: str | None = None
    observed_at: str = field(default_factory=utc_now)
    superseded_at: str | None = None
    reinforcement_count: int = 0
    contradiction_count: int = 0
    retrieval_count: int = 0
    last_accessed_at: str | None = None
    lifecycle_state: BeliefState = BeliefState.PROPOSED
    governed_exception_for: str | None = None
    approved_by: str | None = None
    approval_expires_at: str | None = None
    allowed_agents: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    purpose_allowlist: list[str] = field(default_factory=list)
    version: int = 1
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_type"] = self.memory_type.value
        data["trust_tier"] = int(self.trust_tier)
        data["security_label"] = self.security_label.value
        data["lifecycle_state"] = self.lifecycle_state.value
        return data
