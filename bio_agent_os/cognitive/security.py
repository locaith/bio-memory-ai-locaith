from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SecurityAssessment:
    accepted: bool
    quarantined: bool
    reasons: list[str] = field(default_factory=list)
    redacted_content: str | None = None


class MemorySecurityScanner:
    """Deterministic first-line defence before LLM-based classifiers.

    This intentionally errs on the side of quarantine for persistent policy,
    identity and procedural memories.
    """

    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"reveal\s+(the\s+)?system\s+prompt",
        r"act\s+as\s+system",
        r"developer\s+message",
        r"bypass\s+(security|policy|approval)",
        r"disable\s+(audit|logging|guardrails)",
    ]
    SECRET_PATTERNS = [
        r"sk-[A-Za-z0-9_-]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?i)(password|api[_ -]?key|secret)\s*[:=]\s*[^\s,;]{8,}",
    ]

    def assess(self, content: str, persistent: bool = True) -> SecurityAssessment:
        reasons: list[str] = []
        lowered = content.lower()
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                reasons.append("prompt_injection_pattern")
                break
        redacted = content
        for pattern in self.SECRET_PATTERNS:
            if re.search(pattern, redacted, flags=re.IGNORECASE):
                reasons.append("secret_or_credential_pattern")
                redacted = re.sub(pattern, "[REDACTED]", redacted, flags=re.IGNORECASE)
        quarantine = bool(reasons) and persistent
        return SecurityAssessment(
            accepted=not quarantine,
            quarantined=quarantine,
            reasons=sorted(set(reasons)),
            redacted_content=redacted,
        )
