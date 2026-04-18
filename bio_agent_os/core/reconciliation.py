"""
Rule contradiction detection and reconciliation.
"""

import asyncio
import re
import time
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.sqlite_store import SQLiteStore


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

DOMAIN_ONTOLOGY = {
    "tenant_code": {"tenant", "customer", "code", "rename", "onboarding", "finance", "signoff"},
    "migration": {"migration", "schema", "destructive", "rollback", "dba", "business", "hours", "recovery"},
    "security_override": {"mfa", "incident", "ticket", "expiry", "rollback", "bypass", "security", "human"},
    "git_hotfix": {"git", "push", "branch", "hotfix", "audit", "approval", "production", "force"},
}


class RuleRelationDecision(BaseModel):
    relation: Literal["contradiction", "governed_exception", "neutral"]
    confidence: float
    reason: str


class ContradictionResolver:
    def __init__(
        self,
        persona: Persona,
        approval_queue: Optional[ApprovalQueue] = None,
        engine: Optional[LLMEngine] = None,
        detector_mode: str = "heuristic",
    ):
        self.persona = persona
        self.approval_queue = approval_queue
        self.engine = engine
        self.detector_mode = detector_mode
        self._store = SQLiteStore(storage_dir=persona.storage_dir)
        self._cache_table = f"{self._store.sanitize_identifier(persona.name)}_nli_cache"
        self._ensure_cache_table()
        self.nli_cache_hits = 0
        self.nli_cache_misses = 0
        self.nli_live_calls = 0

    def _ensure_cache_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._cache_table} (
                cache_key TEXT PRIMARY KEY,
                left_text TEXT NOT NULL,
                right_text TEXT NOT NULL,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    def _normalize_text(self, text: str) -> str:
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-z0-9\u00c0-\u024f\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered

    def _cache_key(self, left: Dict, right: Dict) -> str:
        left_text = self._normalize_text(left["text"])
        right_text = self._normalize_text(right["text"])
        ordered = sorted(
            [
                f"{left.get('scope', 'project')}::{left_text}",
                f"{right.get('scope', 'project')}::{right_text}",
            ]
        )
        return "||".join(ordered)

    def _load_cached_decision(self, left: Dict, right: Dict) -> Optional[RuleRelationDecision]:
        row = self._store.fetchone(
            f"SELECT relation, confidence, reason FROM {self._cache_table} WHERE cache_key = ?",
            [self._cache_key(left, right)],
        )
        if not row:
            return None
        self.nli_cache_hits += 1
        return RuleRelationDecision(
            relation=row["relation"],
            confidence=float(row["confidence"]),
            reason="nli-cache",
        )

    def _save_cached_decision(self, left: Dict, right: Dict, decision: RuleRelationDecision):
        self._store.execute(
            f"""
            INSERT OR REPLACE INTO {self._cache_table}
            (cache_key, left_text, right_text, relation, confidence, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                self._cache_key(left, right),
                self._normalize_text(left["text"]),
                self._normalize_text(right["text"]),
                decision.relation,
                float(decision.confidence),
                str(decision.reason),
                time.time(),
            ],
        )

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

    def _ontology_domains(self, text: str) -> set[str]:
        tokens = self._semantic_core(text)
        matched = {
            name for name, markers in DOMAIN_ONTOLOGY.items()
            if len(tokens & markers) >= 2
        }
        return matched

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
        left_core = self._semantic_core(left["text"])
        right_core = self._semantic_core(right["text"])
        overlap = len(left_core & right_core) / max(1, min(len(left_core), len(right_core)))
        shared_domains = self._ontology_domains(left["text"]) & self._ontology_domains(right["text"])
        if overlap < 0.45 and not shared_domains:
            return False
        return (
            (self._is_conditional_exception(left) and self._is_general_negative_policy(right))
            or (self._is_conditional_exception(right) and self._is_general_negative_policy(left))
        )

    def _heuristic_relation(self, left: Dict, right: Dict) -> str:
        if self._is_governed_exception_pair(left, right):
            return "governed_exception"
        if self._is_conflict(left, right):
            return "contradiction"
        return "neutral"

    def _should_escalate_to_nli(self, left: Dict, right: Dict) -> bool:
        if self.detector_mode not in {"hybrid", "nli"}:
            return False
        if not self.engine or not getattr(self.engine, "is_ready", True):
            return False
        if left["scope"] != right["scope"]:
            return False
        heuristic = self._heuristic_relation(left, right)
        if self.detector_mode == "nli":
            return True
        left_domains = self._ontology_domains(left["text"])
        right_domains = self._ontology_domains(right["text"])
        shared_domains = left_domains & right_domains
        left_core = self._semantic_core(left["text"])
        right_core = self._semantic_core(right["text"])
        overlap = len(left_core & right_core) / max(1, min(len(left_core), len(right_core)))
        return heuristic == "neutral" or bool(shared_domains) or overlap < 0.7

    async def _nli_relation(self, left: Dict, right: Dict) -> RuleRelationDecision:
        cached = self._load_cached_decision(left, right)
        if cached:
            return cached
        self.nli_cache_misses += 1
        self.nli_live_calls += 1
        prompt = (
            "You are a contradiction detector for long-term agent memory.\n"
            "Classify the logical relation between Rule A and Rule B.\n"
            "Use exactly one relation:\n"
            "- contradiction: they cannot both be followed at the same time\n"
            "- governed_exception: one is a conditional approved override of the other\n"
            "- neutral: neither contradiction nor governed exception\n"
            "Temporal and scheduling conflicts count as contradiction.\n"
            "If Rule A restricts an action to nighttime and Rule B requires the same action at 10 AM, that is contradiction.\n"
            "A tenant-specific or hotfix-specific approved override of a default rule is governed_exception.\n"
            "Return JSON only.\n\n"
            f"Rule A:\n{left['text']}\n\n"
            f"Rule B:\n{right['text']}\n\n"
            f"Scope A: {left['scope']}\n"
            f"Scope B: {right['scope']}\n"
        )
        payload = await self.engine.generate_structured(
            prompt,
            schema=RuleRelationDecision,
            temperature=0.0,
            effort="low",
        )
        decision = RuleRelationDecision.model_validate(payload)
        self._save_cached_decision(left, right, decision)
        return decision

    async def classify_relation(self, left: Dict, right: Dict) -> RuleRelationDecision:
        heuristic_relation = self._heuristic_relation(left, right)
        if not self._should_escalate_to_nli(left, right):
            confidence = 0.92 if heuristic_relation != "neutral" else 0.55
            return RuleRelationDecision(
                relation=heuristic_relation,
                confidence=confidence,
                reason="heuristic",
            )
        try:
            return await self._nli_relation(left, right)
        except Exception:
            confidence = 0.92 if heuristic_relation != "neutral" else 0.55
            return RuleRelationDecision(
                relation=heuristic_relation,
                confidence=confidence,
                reason="heuristic-fallback",
            )

    def _override_metadata(self, exception_rule: Dict) -> Dict[str, object]:
        normalized = self._normalize_text(exception_rule["text"])
        requires_human_approval = any(
            token in normalized for token in ["approval", "approved", "human", "signoff", "dba", "incident ticket"]
        )
        valid_from = time.time()
        valid_to = None
        if any(token in normalized for token in ["expiry", "temporary", "window", "recovery window", "business hours"]):
            if "business hours" in normalized:
                valid_to = valid_from + (12 * 3600)
            elif "recovery" in normalized:
                valid_to = valid_from + (6 * 3600)
            else:
                valid_to = valid_from + (24 * 3600)
        return {
            "requires_human_approval": requires_human_approval,
            "valid_from": valid_from,
            "valid_to": valid_to,
        }

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

    async def find_conflicts_async(self, rule_id: str) -> List[str]:
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
            relation = await self.classify_relation(target, other_rule)
            if relation.relation == "contradiction":
                conflicts.append(other_id)
        return conflicts

    def reconcile(self, rule_id: str) -> Dict[str, object]:
        if self.detector_mode in {"hybrid", "nli"} and self.engine:
            try:
                return asyncio.run(self.areconcile(rule_id))
            except RuntimeError:
                pass
        rules = self.persona.get_rule_records()
        target = rules.get(rule_id)
        if not target:
            return {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "governed_pairs": [], "approval_request_ids": [], "fallback_actions": []}

        conflicts = self.find_conflicts(rule_id)
        stats = {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "governed_pairs": [], "approval_request_ids": [], "fallback_actions": []}
        governed_defaults = set()

        for other_id, other in rules.items():
            if other_id == rule_id:
                continue
            if other["state"] in {"deprecated", "archived"}:
                continue
            if not self._is_governed_exception_pair(target, other):
                continue

            conditional_rule_id = rule_id if self._is_conditional_exception(target) else other_id
            default_rule_id = other_id if conditional_rule_id == rule_id else rule_id
            if default_rule_id in governed_defaults:
                continue
            if self.persona.govern_exception_rule(conditional_rule_id):
                metadata = self._override_metadata(
                    self.persona.get_rule_records()[conditional_rule_id]
                )
                stats["governed"] += 1
                stats["governed_ids"].append(conditional_rule_id)
                stats["governed_pairs"].append(
                    {
                        "exception_rule_id": conditional_rule_id,
                        "default_rule_id": default_rule_id,
                        "requires_human_approval": metadata["requires_human_approval"],
                        "valid_from": metadata["valid_from"],
                        "valid_to": metadata["valid_to"],
                    }
                )
                governed_defaults.add(default_rule_id)
                target = self.persona.get_rule_records()[rule_id]

        for other_id in conflicts:
            other = self.persona.get_rule_records()[other_id]
            if self._is_governed_exception_pair(target, other):
                conditional_rule_id = rule_id if self._is_conditional_exception(target) else other_id
                default_rule_id = other_id if conditional_rule_id == rule_id else rule_id
                if default_rule_id in governed_defaults:
                    continue
                if self.persona.govern_exception_rule(conditional_rule_id):
                    metadata = self._override_metadata(
                        self.persona.get_rule_records()[conditional_rule_id]
                    )
                    stats["governed"] += 1
                    stats["governed_ids"].append(conditional_rule_id)
                    stats["governed_pairs"].append(
                        {
                            "exception_rule_id": conditional_rule_id,
                            "default_rule_id": default_rule_id,
                            "requires_human_approval": metadata["requires_human_approval"],
                            "valid_from": metadata["valid_from"],
                            "valid_to": metadata["valid_to"],
                        }
                    )
                    governed_defaults.add(default_rule_id)
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

    async def areconcile(self, rule_id: str) -> Dict[str, object]:
        rules = self.persona.get_rule_records()
        target = rules.get(rule_id)
        if not target:
            return {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "governed_pairs": [], "approval_request_ids": [], "fallback_actions": []}

        conflicts = await self.find_conflicts_async(rule_id)
        stats = {"challenged": 0, "deprecated": 0, "governed": 0, "pending_approval": 0, "challenged_ids": [], "deprecated_ids": [], "governed_ids": [], "governed_pairs": [], "approval_request_ids": [], "fallback_actions": [], "nli_used": 0}
        governed_defaults = set()

        for other_id, other in rules.items():
            if other_id == rule_id:
                continue
            if other["state"] in {"deprecated", "archived"}:
                continue
            relation = await self.classify_relation(target, other)
            if relation.reason != "heuristic":
                stats["nli_used"] += 1
            if relation.relation != "governed_exception":
                continue
            conditional_rule_id = rule_id if self._is_conditional_exception(target) else other_id
            default_rule_id = other_id if conditional_rule_id == rule_id else rule_id
            if default_rule_id in governed_defaults:
                continue
            if self.persona.govern_exception_rule(conditional_rule_id):
                metadata = self._override_metadata(
                    self.persona.get_rule_records()[conditional_rule_id]
                )
                stats["governed"] += 1
                stats["governed_ids"].append(conditional_rule_id)
                stats["governed_pairs"].append(
                    {
                        "exception_rule_id": conditional_rule_id,
                        "default_rule_id": default_rule_id,
                        "requires_human_approval": metadata["requires_human_approval"],
                        "valid_from": metadata["valid_from"],
                        "valid_to": metadata["valid_to"],
                    }
                )
                governed_defaults.add(default_rule_id)
                target = self.persona.get_rule_records()[rule_id]

        for other_id in conflicts:
            other = self.persona.get_rule_records()[other_id]
            relation = await self.classify_relation(target, other)
            if relation.reason != "heuristic":
                stats["nli_used"] += 1
            if relation.relation == "governed_exception":
                conditional_rule_id = rule_id if self._is_conditional_exception(target) else other_id
                default_rule_id = other_id if conditional_rule_id == rule_id else rule_id
                if default_rule_id in governed_defaults:
                    continue
                if self.persona.govern_exception_rule(conditional_rule_id):
                    metadata = self._override_metadata(
                        self.persona.get_rule_records()[conditional_rule_id]
                    )
                    stats["governed"] += 1
                    stats["governed_ids"].append(conditional_rule_id)
                    stats["governed_pairs"].append(
                        {
                            "exception_rule_id": conditional_rule_id,
                            "default_rule_id": default_rule_id,
                            "requires_human_approval": metadata["requires_human_approval"],
                            "valid_from": metadata["valid_from"],
                            "valid_to": metadata["valid_to"],
                        }
                    )
                    governed_defaults.add(default_rule_id)
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
