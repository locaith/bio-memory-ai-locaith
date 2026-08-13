from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .governance import GovernanceEngine
from .memory_store import SQLiteMemoryStore
from .models import (
    AccessContext,
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    MemoryType,
    TrustTier,
    VerificationStatus,
)


#: A memory has to be *about* the query to be returned at all.
#:
#: Of the thirteen score components below, only `semantic` and `lexical` depend
#: on the query. The rest — confidence, trust, utility, salience, reinforcement,
#: governance — describe the memory on its own, so a memory stored with
#: confidence 0.98 contributes 0.539 before the query is even read. Summed
#: against the old `total <= 0.05` threshold, that meant every confident memory
#: was returned for every query ever asked. Measured on the live database on
#: 2026-08-07:
#:
#:     "bedroom paint colour"  ->  a bank account, score 1.624
#:     "aaaa bbbb cccc"        ->  the same account, score 1.624
#:
#: So relevance is a **gate**, applied before the sum: no amount of confidence
#: rescues a memory the query never mentioned.
#:
#: Derived, not chosen. On a fixed corpus, 8 matching and 10 unrelated queries:
#:
#:     matching     lowest 1.913   median 2.440
#:     unrelated    highest 0.734  median 0.000
#:
#: **Off by default (0.0), because on this scoring function no floor works.**
#:
#: The calibration above used queries that reuse the memory's own words. Real
#: queries paraphrase, and measured against the project's own domain corpus:
#:
#:     "Blender non destructive scale"   -> its memory scores 0.000
#:     "child pickup reminder"           -> its memory scores 0.000
#:     "experiment causal conclusion"    -> its memory scores 0.000
#:     "cong thuc nau pho bo" (unrelated) -> best memory scores 0.734
#:
#: Genuine matches with zero token overlap are indistinguishable from nonsense,
#: so any floor above zero deletes real answers — 16 tests failed at 1.0, 5 at
#: 0.55, and the three above cannot be rescued by any threshold. IDF weighting
#: was tried and does not separate them either (lowest match 1.252, highest
#: non-match 1.252).
#:
#: The two symptoms are one defect: `cognitive/` ranks by token counts and has
#: no semantic representation, so it can neither reject the irrelevant nor
#: recognise a paraphrase. The fix is embeddings in this layer — the mechanism
#: is kept here, wired and tested, and switches on the moment relevance means
#: something. Note an embedder is not an LLM: milliseconds, deterministic, and
#: local if wanted, so it does not breach the no-model rule this layer keeps.
RELEVANCE_FLOOR = float(os.getenv("BIO_RETRIEVAL_RELEVANCE_FLOOR", "0.0"))


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
    """State-, time-, governance- and epistemics-aware retrieval."""

    def __init__(self, store: SQLiteMemoryStore, governance: GovernanceEngine | None = None):
        self.store = store
        self.governance = governance or GovernanceEngine()

    @staticmethod
    def classify_query(query: str) -> str:
        q = query.lower()
        if any(x in q for x in ["hôm qua", "yesterday", "lần trước", "when", "ngày nào", "timeline"]):
            return "temporal"
        if any(x in q for x in ["được phép", "policy", "quy định", "có được", "forbidden", "approval", "allowed", "can we"]):
            return "policy"
        if any(x in q for x in [
            "cách sửa", "how to", "procedure", "runbook", "làm thế nào", "fix", "repair",
            "recover", "recovery", "restart", "apply transform", "scale", "khắc phục", "xử lý",
        ]):
            return "procedural"
        if any(x in q for x in ["tại sao", "why", "nguyên nhân", "cause"]):
            return "causal"
        if any(x in q for x in ["nếu", "what if", "giả sử", "counterfactual"]):
            return "counterfactual"
        if any(x in q for x in ["nhắc", "khi nào cần", "todo", "follow up", "when condition"]):
            return "prospective"
        if any(x in q for x in ["ai là", "relationship", "vai trò", "who is"]):
            return "social"
        if any(x in q for x in ["ở đâu", "where", "layout", "path", "map"]):
            return "spatial"
        if any(x in q for x in ["tôi có thể", "can i", "khả năng của tôi", "capability"]):
            return "self_model"
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
        candidates = self.store.candidate_pool(
            ctx.tenant_id, query, ctx.workspace_id, as_of=effective_as_of, limit=max(100, limit * 20)
        )
        results: list[RetrievalResult] = []
        for memory in candidates:
            allowed, access_reasons = self.governance.can_read(memory, ctx)
            if not allowed:
                continue
            score_parts: dict[str, float] = {}
            score_parts["semantic"] = cosine_counter(q_counter, Counter(tokenize(memory.content))) * 2.0
            overlap = len(set(q_counter) & set(tokenize(memory.content)))
            score_parts["lexical"] = min(overlap * 0.30, 1.20)

            # The gate. Everything below this line can only describe how good a
            # memory is, never whether it answers *this* question, so the two
            # query-dependent components decide admission on their own.
            relevance = score_parts["semantic"] + score_parts["lexical"]
            if relevance < RELEVANCE_FLOOR:
                continue

            score_parts["confidence"] = memory.confidence * 0.55
            score_parts["trust"] = (int(memory.trust_tier) / int(TrustTier.SIGNED_POLICY)) * 0.45
            score_parts["utility"] = memory.utility * 0.25
            score_parts["salience"] = memory.salience * 0.20
            score_parts["reinforcement"] = min(memory.reinforcement_count * 0.04, 0.25)
            score_parts["contradiction_penalty"] = -min(memory.contradiction_count * 0.15, 0.75)
            score_parts["state_match"] = self._state_score(memory, state)
            score_parts["context_match"] = self._context_score(memory, state)
            score_parts["query_type"] = self._type_score(memory, query_type)
            score_parts["temporal"] = self._temporal_score(memory, as_of, query_type)
            score_parts["governance"] = self._governance_score(memory)
            score_parts["epistemic"] = self._epistemic_score(memory, state)
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
                        "counterevidence_event_ids": memory.counterevidence_event_ids,
                        "valid_from": memory.valid_from,
                        "valid_to": memory.valid_to,
                        "trust_tier": int(memory.trust_tier),
                        "lifecycle_state": memory.lifecycle_state.value,
                        "epistemic_status": memory.epistemic_status.value,
                        "verification_status": memory.verification_status.value,
                        "simulation_id": memory.simulation_id,
                        "governed_exception_for": memory.governed_exception_for,
                        "approved_by": memory.approved_by,
                        "access": "allowed",
                        "access_reasons": access_reasons,
                        "candidate_pool_size": len(candidates),
                        "fts_first_stage": bool(getattr(self.store, "fts_available", False)),
                    },
                )
            )
        results.sort(key=lambda r: (-r.score, -int(r.memory.trust_tier), -r.memory.confidence))
        selected = results[:limit]
        self.store.mark_retrieved_many([result.memory for result in selected])
        return selected

    def _state_score(self, memory: CognitiveMemory, state: dict[str, Any]) -> float:
        score = 0.0
        tags = memory.metadata.get("state", {}) if isinstance(memory.metadata, dict) else {}
        for key in ("mode", "risk_level", "project_version", "task_type", "stress_state", "goal"):
            if state.get(key) is not None and tags.get(key) == state.get(key):
                score += 2.25
        if state.get("risk_level") in {"high", "critical"} and memory.memory_type in {MemoryType.POLICY, MemoryType.EXCEPTION}:
            score += 0.45
        return score

    @staticmethod
    def _context_score(memory: CognitiveMemory, state: dict[str, Any]) -> float:
        if not memory.applicable_context:
            return 0.0
        matched = 0
        mismatched = 0
        for key, value in memory.applicable_context.items():
            if key not in state:
                continue
            if state[key] == value:
                matched += 1
            else:
                mismatched += 1
        return matched * 0.35 - mismatched * 0.50

    @staticmethod
    def _type_score(memory: CognitiveMemory, query_type: str) -> float:
        mapping = {
            "temporal": {MemoryType.EPISODIC: 0.70, MemoryType.AUTOBIOGRAPHICAL: 0.65, MemoryType.SEMANTIC: 0.15},
            "policy": {MemoryType.POLICY: 0.90, MemoryType.EXCEPTION: 1.0, MemoryType.BELIEF: 0.35},
            "procedural": {MemoryType.PROCEDURAL: 0.90, MemoryType.EPISODIC: 0.20},
            "causal": {MemoryType.CAUSAL: 0.90, MemoryType.EPISODIC: 0.25},
            "counterfactual": {MemoryType.COUNTERFACTUAL: 0.90, MemoryType.CAUSAL: 0.45},
            "prospective": {MemoryType.PROSPECTIVE: 1.0},
            "social": {MemoryType.SOCIAL: 0.90, MemoryType.RELATIONAL: 0.55},
            "spatial": {MemoryType.SPATIAL: 0.90, MemoryType.RELATIONAL: 0.35},
            "self_model": {MemoryType.SELF_MODEL: 1.0, MemoryType.AUTOBIOGRAPHICAL: 0.45},
            "factual": {MemoryType.SEMANTIC: 0.50, MemoryType.WORLD_STATE: 0.45, MemoryType.RELATIONAL: 0.30},
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

    @staticmethod
    def _epistemic_score(memory: CognitiveMemory, state: dict[str, Any]) -> float:
        score = {
            EpistemicStatus.OBSERVED: 0.18,
            EpistemicStatus.REPORTED: 0.04,
            EpistemicStatus.INFERRED: 0.0,
            EpistemicStatus.HYPOTHESIZED: -0.12,
            EpistemicStatus.SIMULATED: -0.20,
            EpistemicStatus.VERIFIED: 0.28,
        }[memory.epistemic_status]
        score += {
            VerificationStatus.UNVERIFIED: 0.0,
            VerificationStatus.MACHINE_CHECKED: 0.14,
            VerificationStatus.HUMAN_APPROVED: 0.24,
            VerificationStatus.REJECTED: -1.20,
        }[memory.verification_status]
        if state.get("risk_level") in {"high", "critical"}:
            if memory.epistemic_status == EpistemicStatus.SIMULATED:
                score -= 1.25
            if memory.verification_status == VerificationStatus.UNVERIFIED:
                score -= 0.55
        return score
