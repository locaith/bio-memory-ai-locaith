from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .governance import GovernanceEngine
from .memory_store import SQLiteMemoryStore
from .models import AccessContext, BeliefState, CognitiveMemory, MemoryType, TrustTier


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(t) > 1]


def cosine_counter(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b.get(k, 0) for k in a)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class RetrievalResult:
    memory: CognitiveMemory
    score: float
    explanation: dict[str, Any]


class HybridRetrievalEngine:
    def __init__(self, store: SQLiteMemoryStore, governance: GovernanceEngine | None = None):
        self.store = store
        self.governance = governance or GovernanceEngine()

    @staticmethod
    def classify_query(query: str) -> str:
        q = query.lower()
        if any(x in q for x in ["hôm qua", "yesterday", "lần trước", "when", "ngày nào"]):
            return "temporal"
        if any(x in q for x in ["được phép", "policy", "quy định", "có được", "forbidden"]):
            return "policy"
        if any(x in q for x in ["cách sửa", "how to", "procedure", "runbook", "làm thế nào"]):
            return "procedural"
        if any(x in q for x in ["tại sao", "why", "nguyên nhân", "cause"]):
            return "causal"
        return "factual"

    def recall(
        self,
        query: str,
        ctx: AccessContext,
        state: dict[str, Any] | None = None,
        as_of: str | None = None,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        state = state or {}
        query_type = self.classify_query(query)
        q_counter = Counter(tokenize(query))
        effective_as_of = as_of or datetime.now(timezone.utc).isoformat()
        candidates = self.store.active(ctx.tenant_id, ctx.workspace_id, as_of=effective_as_of)
        results: list[RetrievalResult] = []
        for memory in candidates:
            allowed, access_reasons = self.governance.can_read(memory, ctx)
            if not allowed:
                continue
            score_parts: dict[str, float] = {}
            score_parts["semantic"] = cosine_counter(q_counter, Counter(tokenize(memory.content))) * 2.0
            overlap = len(set(q_counter) & set(tokenize(memory.content)))
            score_parts["lexical"] = min(overlap * 0.30, 1.20)
            score_parts["confidence"] = memory.confidence * 0.55
            score_parts["trust"] = (int(memory.trust_tier) / int(TrustTier.SIGNED_POLICY)) * 0.45
            score_parts["utility"] = memory.utility * 0.25
            score_parts["salience"] = memory.salience * 0.20
            score_parts["reinforcement"] = min(memory.reinforcement_count * 0.04, 0.25)
            score_parts["contradiction_penalty"] = -min(memory.contradiction_count * 0.15, 0.75)
            score_parts["state_match"] = self._state_score(memory, state)
            score_parts["query_type"] = self._type_score(memory, query_type)
            score_parts["temporal"] = self._temporal_score(memory, as_of, query_type)
            score_parts["governance"] = self._governance_score(memory)
            total = sum(score_parts.values())
            if total <= 0.05:
                continue
            results.append(
                RetrievalResult(
                    memory=memory,
                    score=round(total, 6),
                    explanation={
                        "query_type": query_type,
                        "score_components": {k: round(v, 6) for k, v in score_parts.items()},
                        "source_event_ids": memory.source_event_ids,
                        "valid_from": memory.valid_from,
                        "valid_to": memory.valid_to,
                        "trust_tier": int(memory.trust_tier),
                        "lifecycle_state": memory.lifecycle_state.value,
                        "governed_exception_for": memory.governed_exception_for,
                        "approved_by": memory.approved_by,
                        "access": "allowed",
                    },
                )
            )
        results.sort(key=lambda r: (-r.score, -int(r.memory.trust_tier), -r.memory.confidence))
        selected = results[:limit]
        for result in selected:
            self.store.mark_retrieved(result.memory)
        return selected

    def _state_score(self, memory: CognitiveMemory, state: dict[str, Any]) -> float:
        score = 0.0
        tags = memory.metadata.get("state", {}) if isinstance(memory.metadata, dict) else {}
        for key in ("mode", "risk_level", "project_version", "task_type"):
            if state.get(key) is not None and tags.get(key) == state.get(key):
                score += 2.25
        if state.get("risk_level") == "high" and memory.memory_type in {MemoryType.POLICY, MemoryType.EXCEPTION}:
            score += 0.35
        return score

    @staticmethod
    def _type_score(memory: CognitiveMemory, query_type: str) -> float:
        mapping = {
            "temporal": {MemoryType.EPISODIC: 0.70, MemoryType.SEMANTIC: 0.15},
            "policy": {MemoryType.POLICY: 0.90, MemoryType.EXCEPTION: 1.0, MemoryType.BELIEF: 0.35},
            "procedural": {MemoryType.PROCEDURAL: 0.90, MemoryType.EPISODIC: 0.20},
            "causal": {MemoryType.CAUSAL: 0.90, MemoryType.EPISODIC: 0.25},
            "factual": {MemoryType.SEMANTIC: 0.50, MemoryType.RELATIONAL: 0.30},
        }
        return mapping.get(query_type, {}).get(memory.memory_type, 0.0)

    @staticmethod
    def _temporal_score(memory: CognitiveMemory, as_of: str | None, query_type: str) -> float:
        if query_type != "temporal":
            return 0.0
        if as_of and memory.valid_from and memory.valid_from <= as_of and (memory.valid_to is None or as_of < memory.valid_to):
            return 0.65
        if memory.valid_from or memory.valid_to:
            return 0.25
        return 0.0

    def _governance_score(self, memory: CognitiveMemory) -> float:
        score = 0.0
        if memory.lifecycle_state in {BeliefState.STABLE, BeliefState.REINFORCED}:
            score += 0.20
        if memory.lifecycle_state in {BeliefState.CHALLENGED, BeliefState.DEPRECATED, BeliefState.ARCHIVED}:
            score -= 0.80
        if memory.memory_type == MemoryType.EXCEPTION:
            score += 0.45 if self.governance.exception_is_active(memory) else -1.50
        return score
