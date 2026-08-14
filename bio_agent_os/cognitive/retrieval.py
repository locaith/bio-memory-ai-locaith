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

#: The floor that actually means something — cosine between the query and the
#: memory, in [-1, 1]. Unlike token overlap this separates the two populations,
#: because a paraphrase is near its memory in vector space while an unrelated
#: question is not.
#:
#: Measured, on gemini-embedding-001 at 3072 dims, over 5 matching and 6
#: unrelated queries against a mixed English/Vietnamese corpus:
#:
#:     matching     lowest 0.672   and all five retrieved the right memory
#:     unrelated    highest 0.607
#:
#: including the three pairs that score exactly 0.000 on token overlap — they
#: land at 0.672, 0.750 and 0.679 here and find their own memory. That is the
#: whole case for this layer having vectors.
#:
#: 0.64 is the midpoint of that gap. Two warnings that matter more than the
#: number:
#:
#: * **It is model-dependent.** This embedder puts unrelated text at ~0.58, so
#:   0.64 is a small margin above its own noise floor. A model whose unrelated
#:   baseline sits at 0.1 would need a completely different value, and reusing
#:   0.64 there would admit everything. Re-derive whenever the embedding model
#:   changes: `scripts/derive_embedding_floor.py`.
#: * **The gap is narrow** — 0.065 — on a small sample. It separates cleanly
#:   here and should be re-measured on real data before being leaned on.
#:
#: Applied only to memories that have a vector, so a partially embedded store
#: keeps working while the backfill runs.
EMBEDDING_FLOOR = float(os.getenv("BIO_RETRIEVAL_EMBEDDING_FLOOR", "0.64"))

#: Should the hippocampus label influence ranking?
#:
#: The label exists on the write path and until now nothing read it, which made
#: the "biological" half of this product a name rather than a mechanism. Two
#: uses, both conservative:
#:
#: * a memory the hippocampus called junk is pushed down, not deleted — the
#:   label is a model's opinion, and a model's opinion should not be able to
#:   make a memory unfindable
#: * `importance_score` becomes a small tie-breaker, worth at most 0.3 against
#:   a semantic score already scaled to 2.0, so it can reorder near-ties and
#:   cannot overturn relevance
#:
#: Off by default until it is measured. This is Phase 3 of the join plan and the
#: whole question is whether it helps; shipping it on by assumption would be the
#: opposite of finding out.
USE_HIPPOCAMPUS_LABELS = os.getenv("BIO_RETRIEVAL_USE_LABELS", "0").strip() == "1"
LABEL_JUNK_PENALTY = float(os.getenv("BIO_RETRIEVAL_JUNK_PENALTY", "0.60"))
LABEL_IMPORTANCE_WEIGHT = float(os.getenv("BIO_RETRIEVAL_IMPORTANCE_WEIGHT", "0.03"))


def _staleness_of(memory: CognitiveMemory) -> dict[str, Any]:
    """Age facts for the explanation block.

    Both this system and plain RAG scored 0/3 on stale memories, and the cause
    was that `observed_at` never left the store. A caller cannot say "as of
    March" if retrieval does not tell it March. Wrapped because a date parse
    must never be able to fail a recall that would otherwise have succeeded.
    """
    try:
        from .staleness import describe

        return describe(memory)
    except Exception:
        return {}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower(), flags=re.UNICODE) if len(t) > 1]


#: A date or a clock time stated in the text itself.
#:
#: Covers the shapes this corpus actually contains — "[13 May, 2023]",
#: "12/03/2026", "8:30 pm", "last spring", "on Wednesdays" — rather than every
#: date format in existence. Deliberately cheap: it runs per candidate on every
#: temporal query, and a slow regex here would cost more than the ranking gains.
_TIME_PATTERNS = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s*(?:am|pm)"
    r"|\d{1,2}:\d{2}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|(?:yesterday|today|tomorrow|last\s+\w+|next\s+\w+|this\s+morning)"
    r"|(?:hôm\s+qua|hôm\s+nay|ngày\s+mai|tuần\s+trước|tháng\s+\d{1,2})"
    r"|\b(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)


def _states_a_time(text: str) -> bool:
    return bool(_TIME_PATTERNS.search(str(text or "")))


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

    def __init__(self, store: SQLiteMemoryStore, governance: GovernanceEngine | None = None,
                 embedder: Any | None = None):
        self.store = store
        self.governance = governance or GovernanceEngine()
        #: Optional, and injected rather than imported. Anything with
        #: `.embed(text) -> list[float]` will do. Absent, retrieval behaves
        #: exactly as it did before semantic scoring existed — this layer has
        #: to keep answering when the model host is down.
        self.embedder = embedder

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
        # One embedding for the query, then one query for the candidates'
        # vectors. Both outside the loop: the cost must not scale with the pool.
        # Any failure here leaves `vectors` empty and scoring falls back to
        # token overlap, which is what this layer did before.
        query_vector: list[float] | None = None
        vectors: dict[str, Any] = {}
        floor = EMBEDDING_FLOOR
        if self.embedder is not None and candidates:
            try:
                from .semantic_index import load_vectors

                from .semantic_index import calibrated_floor

                query_vector = self.embedder.embed(query)
                vectors = load_vectors(
                    self.store.conn, [m.memory_id for m in candidates]
                )
                # A floor measured on this store's own vectors beats one
                # written down for a different embedder. `EMBEDDING_FLOOR`
                # stays as the fallback for a store that has never been
                # calibrated.
                measured = calibrated_floor(self.store.conn)
                if measured is not None:
                    floor = measured
                else:
                    # Never calibrated — usually because the store is too small
                    # to have a "typical unrelated" at all. Falling back to a
                    # written-down number is what broke the behaviour benchmark:
                    # with one to three memories, 0.64 rejected every one of
                    # them and recall returned "no information" for questions
                    # whose answer was sitting right there.
                    #
                    # No reference distribution, no threshold. A store this
                    # small cannot mislead at scale, and returning the only
                    # memory it holds beats returning nothing.
                    floor = 0.0
            except Exception:
                query_vector, vectors = None, {}

        labels: dict[str, Any] = {}
        if USE_HIPPOCAMPUS_LABELS and candidates:
            try:
                from .hippocampus_label import load_labels_for_events

                event_ids = [e for m in candidates for e in (m.source_event_ids or [])]
                labels = load_labels_for_events(self.store.conn, event_ids)
            except Exception:
                query_vector, vectors = None, {}

        results: list[RetrievalResult] = []
        for memory in candidates:
            allowed, access_reasons = self.governance.can_read(memory, ctx)
            if not allowed:
                continue
            score_parts: dict[str, float] = {}
            score_parts["semantic"] = cosine_counter(q_counter, Counter(tokenize(memory.content))) * 2.0
            overlap = len(set(q_counter) & set(tokenize(memory.content)))
            score_parts["lexical"] = min(overlap * 0.30, 1.20)

            # Semantic similarity when there is a vector for this memory, and
            # nothing when there is not — a missing vector must never delete a
            # memory that the old scoring would have found.
            vector = vectors.get(memory.memory_id) if query_vector else None
            if vector is not None:
                from .semantic_index import cosine

                similarity = cosine(query_vector, vector)
                score_parts["embedding"] = similarity * 2.0
            else:
                similarity = None

            # The gate. Everything below this line can only describe how good a
            # memory is, never whether it answers *this* question, so only the
            # query-dependent components decide admission.
            #
            # Which floor applies depends on what signal exists. Token overlap
            # cannot carry a floor at all — three genuine pairs in this
            # project's own corpus score 0.000 on it — so the token floor stays
            # off by default and the meaningful one is applied to the embedding
            # when there is an embedding to apply it to.
            if similarity is not None:
                if similarity < floor:
                    continue
            else:
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

            if labels:
                for event_id in (memory.source_event_ids or []):
                    label = labels.get(event_id)
                    if not label:
                        continue
                    if label["is_junk"]:
                        score_parts["label_junk"] = -LABEL_JUNK_PENALTY
                    importance = label.get("importance_score")
                    if isinstance(importance, (int, float)):
                        # Centred on 5 so an average memory is neutral and only
                        # the tails move anything.
                        score_parts["label_importance"] = (
                            (float(importance) - 5.0) * LABEL_IMPORTANCE_WEIGHT
                        )
                    break
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
                        "staleness": _staleness_of(memory),
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
        """Score a memory's usefulness to a "when did X happen" question.

        The validity window was the only signal here, and in practice it is
        never set — LoCoMo memories carry no `valid_from`, so this returned 0.0
        for every memory on every temporal question and the branch was dead.

        What the memories *do* carry is a date in the text: `format_turn`
        prefixes `[13 May, 2023] Caroline: ...`, and 72 of 72 temporal questions
        in the benchmark ask *when*. Only a memory that states a time can answer
        one, so a stated time is the signal — read from the content, since that
        is where it is.

        Deterministic, no model, one regex. Applied only to temporal queries, so
        nothing else moves.
        """
        if query_type != "temporal":
            return 0.0
        if as_of and memory.valid_from and memory.valid_from <= as_of and (memory.valid_to is None or as_of < memory.valid_to):
            return 0.65
        if memory.valid_from or memory.valid_to:
            return 0.25
        return 0.55 if _states_a_time(memory.content) else 0.0

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
