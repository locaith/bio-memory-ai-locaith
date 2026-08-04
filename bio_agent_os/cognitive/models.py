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
    AUTOBIOGRAPHICAL = "autobiographical"
    PROSPECTIVE = "prospective"
    SPATIAL = "spatial"
    SOCIAL = "social"
    AFFECTIVE = "affective"
    WORLD_STATE = "world_state"
    SELF_MODEL = "self_model"


class BeliefState(str, Enum):
    PROPOSED = "proposed"
    REINFORCED = "reinforced"
    STABLE = "stable"
    CHALLENGED = "challenged"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class EpistemicStatus(str, Enum):
    """How a memory claim entered the system.

    OBSERVED and VERIFIED are the only states allowed to silently influence
    high-risk actions. SIMULATED memories are useful for planning but can
    never masquerade as historical fact.
    """

    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    HYPOTHESIZED = "hypothesized"
    SIMULATED = "simulated"
    VERIFIED = "verified"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    MACHINE_CHECKED = "machine_checked"
    HUMAN_APPROVED = "human_approved"
    REJECTED = "rejected"


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    SCREEN = "screen"
    CODE = "code"
    SENSOR = "sensor"
    STRUCTURED = "structured"


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
    modality: Modality = Modality.TEXT
    epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trust_tier"] = int(self.trust_tier)
        data["security_label"] = self.security_label.value
        data["modality"] = self.modality.value
        data["epistemic_status"] = self.epistemic_status.value
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
    epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    counterevidence_event_ids: list[str] = field(default_factory=list)
    applicable_context: dict[str, Any] = field(default_factory=dict)
    modality: Modality = Modality.TEXT
    simulation_id: str | None = None
    reversible_forget_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["memory_type"] = self.memory_type.value
        data["trust_tier"] = int(self.trust_tier)
        data["security_label"] = self.security_label.value
        data["lifecycle_state"] = self.lifecycle_state.value
        data["epistemic_status"] = self.epistemic_status.value
        data["verification_status"] = self.verification_status.value
        data["modality"] = self.modality.value
        return data


@dataclass(frozen=True)
class ExecutionOutcome:
    goal: str
    steps: tuple[str, ...]
    success: bool
    verifier_passed: bool
    environment: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    result_summary: str = ""
    trace_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ProspectiveTrigger:
    trigger_id: str
    condition: dict[str, Any]
    action: str
    priority: float = 0.5
    expires_at: str | None = None
    requires_approval: bool = False
    source_memory_id: str | None = None


@dataclass(frozen=True)
class SimulationTrace:
    simulation_id: str
    premise: str
    interventions: dict[str, Any]
    predicted_states: tuple[dict[str, Any], ...]
    confidence: float
    source_memory_ids: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
