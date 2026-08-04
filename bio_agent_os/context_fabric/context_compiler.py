from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.retrieval import RetrievalResult

from .block_store import ContextBlockStore, estimate_tokens
from .models import ContextBlock, ContextBlockKind, ContextPacket, StorageTier
from .tiering import TieringScheduler


TYPE_TO_BLOCK = {
    MemoryType.POLICY: ContextBlockKind.POLICY,
    MemoryType.EXCEPTION: ContextBlockKind.POLICY,
    MemoryType.PROCEDURAL: ContextBlockKind.PROCEDURE,
    MemoryType.EPISODIC: ContextBlockKind.EPISODE,
    MemoryType.WORLD_STATE: ContextBlockKind.WORLD_STATE,
    MemoryType.SELF_MODEL: ContextBlockKind.SELF_MODEL,
    MemoryType.IDENTITY: ContextBlockKind.IDENTITY,
    MemoryType.COUNTERFACTUAL: ContextBlockKind.SIMULATION,
}


class ContextCompiler:
    """Compiles minimal, provenance-preserving context packets.

    Selection is deterministic and token-budgeted. High-risk tasks prioritize
    verified policies and procedures; simulated memories are kept in a distinct
    section and excluded entirely unless explicitly requested.
    """

    def __init__(self, block_store: ContextBlockStore):
        self.blocks = block_store
        self.tiering = TieringScheduler()

    @staticmethod
    def cache_key(
        tenant_id: str,
        workspace_id: str | None,
        query: str,
        goal: str | None,
        state: dict[str, Any],
        token_budget: int,
    ) -> str:
        payload = json.dumps(
            {
                "tenant_id": tenant_id, "workspace_id": workspace_id, "query": query,
                "goal": goal, "state": state, "token_budget": token_budget,
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


    def cached_packet(
        self,
        *,
        query: str,
        context: AccessContext,
        goal: str | None,
        state: dict[str, Any],
        token_budget: int,
    ) -> ContextPacket | None:
        key = self.cache_key(context.tenant_id, context.workspace_id, query, goal, state, token_budget)
        cached = self.blocks.get_cached_packet(key, context.tenant_id)
        if cached is None:
            return None
        return ContextPacket(
            tenant_id=context.tenant_id, query=query, goal=goal, blocks=tuple(cached),
            token_budget=token_budget, estimated_tokens=sum(b.token_count for b in cached),
            provenance_memory_ids=tuple(dict.fromkeys(m for b in cached for m in b.source_memory_ids)),
            cache_key=key, metrics={"cache_hit": True, "selected_blocks": len(cached),
                                    "compiled_tokens": sum(b.token_count for b in cached)},
        )

    def compile(
        self,
        *,
        query: str,
        context: AccessContext,
        retrieval_results: list[RetrievalResult],
        goal: str | None = None,
        state: dict[str, Any] | None = None,
        token_budget: int = 4096,
        include_simulations: bool = False,
        use_cache: bool = True,
    ) -> ContextPacket:
        state = state or {}
        key = self.cache_key(context.tenant_id, context.workspace_id, query, goal, state, token_budget)
        if use_cache:
            cached = self.blocks.get_cached_packet(key, context.tenant_id)
            if cached is not None:
                return ContextPacket(
                    tenant_id=context.tenant_id, query=query, goal=goal, blocks=tuple(cached),
                    token_budget=token_budget, estimated_tokens=sum(b.token_count for b in cached),
                    provenance_memory_ids=tuple(m for b in cached for m in b.source_memory_ids),
                    cache_key=key, metrics={"cache_hit": True, "selected_blocks": len(cached)},
                )

        candidates: list[ContextBlock] = []
        for item in retrieval_results:
            memory = item.memory
            if memory.epistemic_status.value == "simulated" and not include_simulations:
                continue
            kind = TYPE_TO_BLOCK.get(memory.memory_type, ContextBlockKind.FACT)
            block = ContextBlock(
                tenant_id=memory.tenant_id, workspace_id=memory.workspace_id, kind=kind,
                content=memory.content, source_memory_ids=[memory.memory_id],
                source_event_ids=list(memory.source_event_ids), trust_score=int(memory.trust_tier) / 5.0,
                relevance_score=max(0.0, min(item.score / 5.0, 1.0)), importance=memory.importance,
                latency_sensitivity=0.9 if kind in {ContextBlockKind.GOAL, ContextBlockKind.POLICY, ContextBlockKind.TOOL_STATE} else 0.45,
                recomputation_cost=0.8 if kind in {ContextBlockKind.PROCEDURE, ContextBlockKind.WORLD_STATE} else 0.35,
                expected_reuse=min(1.0, 0.35 + memory.retrieval_count * 0.04 + memory.utility * 0.3),
                security_label=memory.security_label,
                metadata={
                    "verification_status": memory.verification_status.value,
                    "epistemic_status": memory.epistemic_status.value,
                    "lifecycle_state": memory.lifecycle_state.value,
                    "retrieval_explanation": item.explanation,
                },
            )
            block.tier = self.tiering.choose_tier(block)
            candidates.append(self.blocks.put(block))

        # Policies and goal-aligned procedures get priority, but exact relevance
        # remains the strongest signal.
        priority = {
            ContextBlockKind.GOAL: 1.0, ContextBlockKind.POLICY: 0.95,
            ContextBlockKind.PROCEDURE: 0.82, ContextBlockKind.WORLD_STATE: 0.78,
            ContextBlockKind.IDENTITY: 0.72, ContextBlockKind.SELF_MODEL: 0.65,
            ContextBlockKind.FACT: 0.58, ContextBlockKind.EPISODE: 0.48,
            ContextBlockKind.SIMULATION: 0.25,
        }
        candidates.sort(
            key=lambda b: (
                -(b.relevance_score * 0.55 + priority.get(b.kind, 0.4) * 0.25 + b.trust_score * 0.12 + b.importance * 0.08),
                b.token_count,
            )
        )
        selected: list[ContextBlock] = []
        omitted: list[str] = []
        used_tokens = estimate_tokens(goal) if goal else 0
        seen_hashes: set[str] = set()
        for block in candidates:
            if block.content_hash in seen_hashes:
                continue
            if used_tokens + block.token_count > token_budget:
                omitted.append(block.block_id)
                continue
            selected.append(block)
            seen_hashes.add(block.content_hash)
            used_tokens += block.token_count
            if block.tier in {StorageTier.COLD, StorageTier.ARCHIVE} and block.relevance_score >= 0.65:
                self.blocks.promote(block.block_id, context.tenant_id, StorageTier.WARM)

        self.blocks.cache_packet(key, context.tenant_id, context.workspace_id, [b.block_id for b in selected])
        naive_tokens = sum(b.token_count for b in candidates) + (estimate_tokens(goal) if goal else 0)
        return ContextPacket(
            tenant_id=context.tenant_id, query=query, goal=goal, blocks=tuple(selected),
            token_budget=token_budget, estimated_tokens=used_tokens,
            omitted_block_ids=tuple(omitted),
            provenance_memory_ids=tuple(dict.fromkeys(m for b in selected for m in b.source_memory_ids)),
            cache_key=key,
            metrics={
                "cache_hit": False,
                "candidate_blocks": len(candidates),
                "selected_blocks": len(selected),
                "naive_tokens": naive_tokens,
                "compiled_tokens": used_tokens,
                "token_reduction_ratio": round(1.0 - used_tokens / max(naive_tokens, 1), 6),
            },
        )
