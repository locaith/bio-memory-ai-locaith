from __future__ import annotations

from pathlib import Path
from typing import Any

from .event_store import SQLiteEventStore
from .governance import GovernanceEngine
from .memory_store import SQLiteMemoryStore
from .models import AccessContext, BeliefState, CognitiveMemory, EventRecord, MemoryType, SecurityLabel, TrustTier
from .retrieval import HybridRetrievalEngine, RetrievalResult
from .security import MemorySecurityScanner, SecurityAssessment


class MemoryOS:
    """High-level facade for the foundation cognitive memory kernel."""

    def __init__(self, db_path: str | Path = ":memory:"):
        path = str(db_path)
        self.events = SQLiteEventStore(path)
        self.memories = SQLiteMemoryStore(path)
        self.governance = GovernanceEngine()
        self.security = MemorySecurityScanner()
        self.retrieval = HybridRetrievalEngine(self.memories, self.governance)
        self.quarantine: list[dict[str, Any]] = []

    def observe(
        self,
        *, tenant_id: str,
        actor: str,
        source: str,
        content: str,
        workspace_id: str | None = None,
        trust_tier: TrustTier = TrustTier.AGENT_OBSERVATION,
        security_label: SecurityLabel = SecurityLabel.INTERNAL,
        valid_from: str | None = None,
        valid_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EventRecord:
        assessment = self.security.assess(content, persistent=False)
        event = EventRecord(
            tenant_id=tenant_id,
            actor=actor,
            source=source,
            payload={"content": assessment.redacted_content or content},
            workspace_id=workspace_id,
            trust_tier=trust_tier,
            security_label=security_label,
            valid_from=valid_from,
            valid_to=valid_to,
            metadata={**(metadata or {}), "security_reasons": assessment.reasons},
        )
        return self.events.append(event)

    def remember(
        self,
        *,
        event: EventRecord,
        memory_type: MemoryType,
        content: str,
        confidence: float = 0.6,
        importance: float = 0.5,
        salience: float = 0.5,
        utility: float = 0.5,
        lifecycle_state: BeliefState = BeliefState.PROPOSED,
        approved_by: str | None = None,
        governed_exception_for: str | None = None,
        approval_expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        allowed_agents: list[str] | None = None,
        allowed_roles: list[str] | None = None,
        purpose_allowlist: list[str] | None = None,
    ) -> CognitiveMemory | SecurityAssessment:
        persistent = memory_type in {
            MemoryType.PROCEDURAL, MemoryType.BELIEF, MemoryType.IDENTITY,
            MemoryType.POLICY, MemoryType.EXCEPTION,
        }
        assessment = self.security.assess(content, persistent=persistent)
        if assessment.quarantined:
            self.quarantine.append({"event_id": event.event_id, "content": content, "reasons": assessment.reasons})
            return assessment
        memory = CognitiveMemory(
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            memory_type=memory_type,
            content=assessment.redacted_content or content,
            source_event_ids=[event.event_id],
            confidence=max(0.0, min(confidence, 1.0)),
            importance=max(0.0, min(importance, 1.0)),
            salience=max(0.0, min(salience, 1.0)),
            utility=max(0.0, min(utility, 1.0)),
            trust_tier=event.trust_tier,
            security_label=event.security_label,
            valid_from=event.valid_from,
            valid_to=event.valid_to,
            lifecycle_state=lifecycle_state,
            approved_by=approved_by,
            governed_exception_for=governed_exception_for,
            approval_expires_at=approval_expires_at,
            metadata=metadata or {},
            allowed_agents=allowed_agents or [],
            allowed_roles=allowed_roles or [],
            purpose_allowlist=purpose_allowlist or [],
        )
        valid, reasons = self.governance.validate_promotion(memory)
        if not valid and lifecycle_state == BeliefState.STABLE:
            memory.lifecycle_state = BeliefState.PROPOSED
            memory.metadata["promotion_blocked"] = reasons
        return self.memories.put(memory)

    def recall(
        self,
        query: str,
        *,
        context: AccessContext,
        state: dict[str, Any] | None = None,
        as_of: str | None = None,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        return self.retrieval.recall(query, context, state=state, as_of=as_of, limit=limit)
