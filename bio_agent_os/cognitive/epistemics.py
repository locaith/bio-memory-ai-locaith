from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .event_store import SQLiteEventStore
from .models import CognitiveMemory, EpistemicStatus, TrustTier, VerificationStatus


@dataclass(frozen=True)
class EpistemicAssessment:
    confidence: float
    support_weight: float
    counterevidence_weight: float
    independent_sources: int
    high_risk_eligible: bool
    reasons: tuple[str, ...]


class EvidenceLedger:
    """Scores a claim using provenance, trust and counterevidence.

    It deliberately separates *remembering* a statement from being allowed to
    act on it. A vivid untrusted claim can be recalled, but it cannot silently
    drive a high-risk action.
    """

    def __init__(self, events: SQLiteEventStore):
        self.events = events

    def assess(self, memory: CognitiveMemory) -> EpistemicAssessment:
        support_weight = 0.0
        sources: set[str] = set()
        reasons: list[str] = []
        for event_id in memory.source_event_ids:
            event = self.events.get(event_id, memory.tenant_id)
            if not event:
                reasons.append(f"missing_source:{event_id}")
                continue
            sources.add(event.source)
            support_weight += (int(event.trust_tier) + 1) / (int(TrustTier.SIGNED_POLICY) + 1)

        counter_weight = 0.0
        for event_id in memory.counterevidence_event_ids:
            event = self.events.get(event_id, memory.tenant_id)
            if event:
                counter_weight += (int(event.trust_tier) + 1) / (int(TrustTier.SIGNED_POLICY) + 1)

        corroboration = min(max(len(sources) - 1, 0) * 0.08, 0.24)
        verification_bonus = {
            VerificationStatus.UNVERIFIED: 0.0,
            VerificationStatus.MACHINE_CHECKED: 0.12,
            VerificationStatus.HUMAN_APPROVED: 0.22,
            VerificationStatus.REJECTED: -0.75,
        }[memory.verification_status]
        epistemic_bonus = {
            EpistemicStatus.OBSERVED: 0.08,
            EpistemicStatus.REPORTED: 0.0,
            EpistemicStatus.INFERRED: -0.04,
            EpistemicStatus.HYPOTHESIZED: -0.12,
            EpistemicStatus.SIMULATED: -0.22,
            EpistemicStatus.VERIFIED: 0.18,
        }[memory.epistemic_status]

        evidence_ratio = support_weight / max(support_weight + counter_weight, 1.0)
        score = 0.30 * memory.confidence + 0.40 * evidence_ratio + corroboration + verification_bonus + epistemic_bonus
        score = max(0.0, min(score, 1.0))

        high_risk = (
            memory.epistemic_status != EpistemicStatus.SIMULATED
            and memory.verification_status in {VerificationStatus.MACHINE_CHECKED, VerificationStatus.HUMAN_APPROVED}
            and memory.trust_tier >= TrustTier.TRUSTED_SYSTEM
            and score >= 0.70
            and counter_weight < support_weight
        )
        if memory.epistemic_status == EpistemicStatus.SIMULATED:
            reasons.append("simulated_memory_cannot_drive_high_risk_action")
        if memory.verification_status == VerificationStatus.REJECTED:
            reasons.append("claim_rejected")
        if counter_weight >= support_weight and counter_weight > 0:
            reasons.append("counterevidence_not_resolved")
        if len(sources) < 2:
            reasons.append("single_source")

        return EpistemicAssessment(
            confidence=round(score, 6),
            support_weight=round(support_weight, 6),
            counterevidence_weight=round(counter_weight, 6),
            independent_sources=len(sources),
            high_risk_eligible=high_risk,
            reasons=tuple(sorted(set(reasons))),
        )

    def claim_packet(self, memory: CognitiveMemory) -> dict[str, Any]:
        assessment = self.assess(memory)
        return {
            "memory_id": memory.memory_id,
            "claim": memory.content,
            "epistemic_status": memory.epistemic_status.value,
            "verification_status": memory.verification_status.value,
            "confidence": assessment.confidence,
            "support_event_ids": list(memory.source_event_ids),
            "counterevidence_event_ids": list(memory.counterevidence_event_ids),
            "independent_sources": assessment.independent_sources,
            "high_risk_eligible": assessment.high_risk_eligible,
            "reasons": list(assessment.reasons),
        }
