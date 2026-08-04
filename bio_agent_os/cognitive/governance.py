from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    AccessContext,
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    MemoryType,
    SecurityLabel,
    TrustTier,
    VerificationStatus,
)


_SECURITY_ORDER = {
    SecurityLabel.PUBLIC: 0,
    SecurityLabel.INTERNAL: 1,
    SecurityLabel.CONFIDENTIAL: 2,
    SecurityLabel.RESTRICTED: 3,
}


class AccessDenied(PermissionError):
    pass


class GovernanceEngine:
    def can_read(self, memory: CognitiveMemory, ctx: AccessContext) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if memory.tenant_id != ctx.tenant_id:
            reasons.append("tenant_mismatch")
        if memory.workspace_id and ctx.workspace_id and memory.workspace_id != ctx.workspace_id:
            reasons.append("workspace_mismatch")
        if _SECURITY_ORDER[memory.security_label] > _SECURITY_ORDER[ctx.max_security_label]:
            reasons.append("security_label_exceeds_context")
        if memory.allowed_agents and (ctx.agent_id not in memory.allowed_agents):
            reasons.append("agent_not_allowed")
        if memory.allowed_roles and not set(ctx.roles).intersection(memory.allowed_roles):
            reasons.append("role_not_allowed")
        if memory.purpose_allowlist and ctx.purpose not in memory.purpose_allowlist:
            reasons.append("purpose_not_allowed")
        return not reasons, reasons

    def validate_promotion(self, memory: CognitiveMemory) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        authoritative = {MemoryType.IDENTITY, MemoryType.POLICY, MemoryType.EXCEPTION, MemoryType.SELF_MODEL}
        if memory.memory_type in authoritative:
            if memory.trust_tier < TrustTier.HUMAN_APPROVED:
                reasons.append("authoritative_memory_requires_human_approval")
            if not memory.approved_by:
                reasons.append("missing_approver")
            if memory.epistemic_status == EpistemicStatus.SIMULATED:
                reasons.append("simulated_memory_cannot_be_authoritative")
        if not memory.source_event_ids:
            reasons.append("missing_provenance")
        if memory.lifecycle_state == BeliefState.STABLE and memory.confidence < 0.75:
            reasons.append("stable_memory_confidence_too_low")
        if memory.lifecycle_state == BeliefState.STABLE and memory.verification_status == VerificationStatus.REJECTED:
            reasons.append("rejected_memory_cannot_be_stable")
        if memory.governed_exception_for and not memory.approved_by:
            reasons.append("governed_exception_requires_approval")
        return not reasons, reasons

    @staticmethod
    def exception_is_active(memory: CognitiveMemory, now: str | None = None) -> bool:
        if not memory.governed_exception_for:
            return False
        if not memory.approved_by:
            return False
        if memory.epistemic_status == EpistemicStatus.SIMULATED:
            return False
        current = now or datetime.now(timezone.utc).isoformat()
        return memory.approval_expires_at is None or current < memory.approval_expires_at
