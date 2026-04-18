"""
Rule contradiction detection and reconciliation.
"""

import re
from typing import Dict, List, Optional

from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.persona import Persona


NEGATIVE_MARKERS = {
    "never",
    "dont",
    "don't",
    "do not",
    "avoid",
    "forbid",
    "forbidden",
    "ban",
    "banned",
    "must not",
    "cannot",
    "can't",
    "cấm",
    "không",
}

POSITIVE_MARKERS = {
    "allow",
    "allowed",
    "always",
    "must",
    "should",
    "prefer",
    "recommended",
    "nên",
    "luôn",
}


class ContradictionResolver:
    def __init__(self, persona: Persona, approval_queue: Optional[ApprovalQueue] = None):
        self.persona = persona
        self.approval_queue = approval_queue

    def _normalize_text(self, text: str) -> str:
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-z0-9\u00c0-\u024f\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered

    def _polarity(self, text: str) -> str:
        normalized = self._normalize_text(text)
        if any(marker in normalized for marker in NEGATIVE_MARKERS):
            return "negative"
        if any(marker in normalized for marker in POSITIVE_MARKERS):
            return "positive"
        return "neutral"

    def _semantic_core(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        for marker in sorted(NEGATIVE_MARKERS | POSITIVE_MARKERS, key=len, reverse=True):
            normalized = normalized.replace(marker, " ")
        tokens = [token for token in normalized.split() if len(token) > 2]
        return set(tokens)

    def _is_conflict(self, left: Dict, right: Dict) -> bool:
        if left["scope"] != right["scope"]:
            return False

        left_polarity = self._polarity(left["text"])
        right_polarity = self._polarity(right["text"])
        if left_polarity == "neutral" or right_polarity == "neutral":
            return False
        if left_polarity == right_polarity:
            return False

        left_core = self._semantic_core(left["text"])
        right_core = self._semantic_core(right["text"])
        if not left_core or not right_core:
            return False

        overlap = len(left_core & right_core) / max(1, min(len(left_core), len(right_core)))
        return overlap >= 0.6

    def _is_conditional_exception(self, rule: Dict) -> bool:
        normalized = self._normalize_text(rule["text"])
        if self._polarity(rule["text"]) != "positive":
            return False
        condition_markers = [
            "only",
            "if",
            "when",
            "during",
            "with",
            "approval",
            "approved",
            "audit",
            "logging",
            "hotfix",
            "exception",
            "runbook",
        ]
        matches = sum(1 for marker in condition_markers if marker in normalized)
        return matches >= 2

    def _is_general_negative_policy(self, rule: Dict) -> bool:
        normalized = self._normalize_text(rule["text"])
        if self._polarity(rule["text"]) != "negative":
            return False
        return not any(marker in normalized for marker in ["except", "unless", "approval", "audit", "hotfix"])

    def _is_governed_exception_pair(self, left: Dict, right: Dict) -> bool:
        if left["scope"] != right["scope"]:
            return False
        if not self._is_conflict(left, right):
            return False
        return (
            (self._is_conditional_exception(left) and self._is_general_negative_policy(right))
            or (self._is_conditional_exception(right) and self._is_general_negative_policy(left))
        )

    def fallback_action(self, rule: Dict, conflicting_rules: List[Dict]) -> Dict[str, object]:
        is_destructive = any(
            token in rule["text"].lower()
            for token in ["delete", "drop", "force push", "push -f", "migration", "production", "auth", "security"]
        )
        return {
            "rule_id": rule["id"],
            "action": "defer_to_safe_mode",
            "destructive_risk": is_destructive,
            "conflicts_with": [item["id"] for item in conflicting_rules],
            "instruction": (
                "Do not treat this challenged belief as a hard rule. Prefer procedural memory and explicit exception memory. "
                "If the action is destructive, irreversible, or production-facing, require explicit approval."
            ),
        }

    def find_conflicts(self, rule_id: str) -> List[str]:
        rules = self.persona.get_rule_records()
        target = rules.get(rule_id)
        if not target:
            return []

        conflicts: List[str] = []
        for other_id, other_rule in rules.items():
            if other_id == rule_id:
                continue
            if other_rule["state"] in {"deprecated", "archived"}:
                continue
            if self._is_conflict(target, other_rule):
                conflicts.append(other_id)
        return conflicts

    def reconcile(self, rule_id: str) -> Dict[str, object]:
        rules = self.persona.get_rule_records()
        target = rules.get(rule_id)
        if not target:
            return {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "approval_request_ids": [], "fallback_actions": []}

        conflicts = self.find_conflicts(rule_id)
        stats = {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "approval_request_ids": [], "fallback_actions": []}

        for other_id in conflicts:
            other = self.persona.get_rule_records()[other_id]
            if self._is_governed_exception_pair(target, other):
                conditional_rule_id = rule_id if self._is_conditional_exception(target) else other_id
                if self.persona.govern_exception_rule(conditional_rule_id):
                    stats["governed"] += 1
                    stats["governed_ids"].append(conditional_rule_id)
                    target = self.persona.get_rule_records()[rule_id]
                continue
            target_score = target["confidence"] + (target["support_count"] * 0.1)
            other_score = other["confidence"] + (other["support_count"] * 0.1)

            if target_score >= other_score:
                if self.approval_queue and self.approval_queue.requires_approval(
                    other["text"],
                    scope=other["scope"],
                    confidence=float(other["confidence"]),
                ):
                    request = self.approval_queue.submit(
                        "deprecate_sensitive_rule",
                        other["text"],
                        scope=other["scope"],
                        confidence=float(other["confidence"]),
                        target_rule_id=other_id,
                        metadata={"superseded_by": rule_id},
                    )
                    stats["pending_approval"] += 1
                    stats["approval_request_ids"].append(request["request_id"])
                    continue
                if self.persona.deprecate_rule(other_id, superseded_by=rule_id):
                    stats["deprecated"] += 1
                    stats["deprecated_ids"].append(other_id)
                target = self.persona.get_rule_records()[rule_id]
            else:
                if self.persona.challenge_rule(rule_id, reason=f"conflicts_with:{other_id}"):
                    stats["challenged"] += 1
                    stats["challenged_ids"].append(rule_id)
                    stats.setdefault("fallback_actions", []).append(self.fallback_action(target, [other]))
                    target = self.persona.get_rule_records()[rule_id]

        return stats
