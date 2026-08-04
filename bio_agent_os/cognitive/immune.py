from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import EpistemicStatus, MemoryType, TrustTier
from .security import MemorySecurityScanner


@dataclass(frozen=True)
class ImmuneDecision:
    accepted: bool
    quarantined: bool
    risk_score: float
    reasons: tuple[str, ...] = ()
    redacted_content: str | None = None
    required_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryImmuneSystem:
    """Defence-in-depth for persistent memory writes."""

    AUTHORITY_PATTERNS = [
        r"(?:make|set|promote).{0,30}(?:system|identity|policy|admin)",
        r"(?:always|never).{0,40}(?:without|ignore).{0,20}(?:approval|permission)",
        r"(?:remember|store).{0,30}(?:as truth|as verified|as policy)",
        r"pretend.{0,20}(?:observed|verified|approved)",
    ]
    TENANT_PATTERNS = [
        r"tenant[_ -]?id\s*[:=]",
        r"switch\s+tenant",
        r"copy\s+(?:all\s+)?memory\s+from",
    ]

    def __init__(self):
        self.scanner = MemorySecurityScanner()

    def inspect(
        self,
        content: str,
        *,
        memory_type: MemoryType,
        trust_tier: TrustTier,
        epistemic_status: EpistemicStatus,
        source: str,
        persistent: bool,
    ) -> ImmuneDecision:
        base = self.scanner.assess(content, persistent=persistent)
        reasons = list(base.reasons)
        risk = 0.0
        lowered = content.lower()

        if reasons:
            risk += 0.55
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self.AUTHORITY_PATTERNS):
            reasons.append("authority_escalation_language")
            risk += 0.35
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self.TENANT_PATTERNS):
            reasons.append("cross_tenant_manipulation_language")
            risk += 0.45
        if epistemic_status == EpistemicStatus.SIMULATED and memory_type in {
            MemoryType.POLICY,
            MemoryType.IDENTITY,
            MemoryType.BELIEF,
        }:
            reasons.append("simulated_claim_cannot_be_authoritative")
            risk += 0.60
        if trust_tier <= TrustTier.UNVERIFIED_USER and memory_type in {
            MemoryType.POLICY,
            MemoryType.IDENTITY,
            MemoryType.PROCEDURAL,
        }:
            reasons.append("low_trust_persistent_authority_write")
            risk += 0.35
        if source.startswith("web:") or source.startswith("email:"):
            risk += 0.10

        risk = min(risk, 1.0)
        required_approval = memory_type in {MemoryType.POLICY, MemoryType.IDENTITY, MemoryType.EXCEPTION}
        quarantined = persistent and (base.quarantined or risk >= 0.60)
        return ImmuneDecision(
            accepted=not quarantined,
            quarantined=quarantined,
            risk_score=round(risk, 6),
            reasons=tuple(sorted(set(reasons))),
            redacted_content=base.redacted_content,
            required_approval=required_approval,
            metadata={"source": source, "persistent": persistent},
        )
