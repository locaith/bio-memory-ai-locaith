from __future__ import annotations

import json
import time
from pathlib import Path

from bio_agent_os import (
    AccessContext, BeliefState, MemoryOS, MemoryType, TrustTier, VerificationStatus,
)
from bio_agent_os.context_fabric import estimate_tokens


def run() -> dict:
    memory = MemoryOS(":memory:")
    tenant = "context-bench"
    relevant = [
        ("Production deployment requires CTO approval", MemoryType.POLICY),
        ("Production deployment requires a tested rollback command", MemoryType.POLICY),
        ("Use blue-green rollback when error rate exceeds five percent", MemoryType.PROCEDURAL),
        ("Current production region is asia-southeast1", MemoryType.WORLD_STATE),
        ("Friday deployment is forbidden unless a hotfix exception is approved", MemoryType.POLICY),
        ("The latest production incident was caused by a database lock", MemoryType.CAUSAL),
        ("Verify health checks before shifting traffic", MemoryType.PROCEDURAL),
        ("The operator role may initiate rollback but not delete audit logs", MemoryType.POLICY),
    ]
    items = []
    for content, memory_type in relevant:
        items.append({
            "content": content, "memory_type": memory_type, "importance": 0.95,
            "confidence": 0.9, "verification_status": VerificationStatus.MACHINE_CHECKED,
            "lifecycle_state": BeliefState.REINFORCED,
            "applicable_context": {"mode": "deploy", "risk_level": "critical"},
        })
    for i in range(800):
        items.append({
            "content": f"Historical unrelated session {i} discussed component-{i % 41} and routine administration " + "detail " * 12,
            "memory_type": MemoryType.EPISODIC, "importance": 0.2,
        })
    memories, rejected = memory.bulk_ingest(
        tenant_id=tenant, actor="fixture", source="context-benchmark", items=items,
        trust_tier=TrustTier.TRUSTED_SYSTEM,
    )
    ctx = AccessContext(tenant_id=tenant, agent_id="deploy-agent", roles=("operator",))
    state = {"mode": "deploy", "risk_level": "critical"}
    query = "Can we deploy production and what rollback procedure is required?"
    naive_tokens = sum(estimate_tokens(item.content) for item in memories)

    t0 = time.perf_counter_ns()
    first = memory.compile_context(
        query, context=ctx, goal="Safely deploy production", state=state,
        token_budget=420, recall_limit=60, use_cache=True,
    )
    first_ms = (time.perf_counter_ns() - t0) / 1e6
    t0 = time.perf_counter_ns()
    cached = memory.compile_context(
        query, context=ctx, goal="Safely deploy production", state=state,
        token_budget=420, recall_limit=60, use_cache=True,
    )
    cached_ms = (time.perf_counter_ns() - t0) / 1e6

    selected_text = " ".join(block.content for block in first.blocks)
    required_signals = ["approval", "rollback", "health", "traffic"]
    signal_recall = sum(signal in selected_text.lower() for signal in required_signals) / len(required_signals)

    checkpoint = memory.create_checkpoint(
        tenant_id=tenant, agent_id="deploy-agent", workspace_id=None,
        goal="Safely deploy production", completed_steps=("review policy",),
        pending_steps=("run health checks", "deploy", "verify"),
        tool_state={"branch": "release/v2", "environment": "production"},
        active_memory_ids=first.provenance_memory_ids,
    )
    t0 = time.perf_counter_ns()
    restored = memory.restore_checkpoint(tenant, checkpoint_id=checkpoint.checkpoint_id)
    restore_ms = (time.perf_counter_ns() - t0) / 1e6
    plan, prefetched = memory.prefetch("Deploy release to production", context=ctx, state=state)

    result = {
        "benchmark": "context-efficiency-smoke-v1",
        "memory_count": len(memories),
        "quarantined": len(rejected),
        "naive_full_context_tokens": naive_tokens,
        "compiled_context_tokens": first.estimated_tokens,
        "token_reduction_ratio": 1.0 - first.estimated_tokens / max(naive_tokens, 1),
        "token_budget_respected": first.estimated_tokens <= first.token_budget,
        "required_signal_recall": signal_recall,
        "selected_blocks": len(first.blocks),
        "omitted_blocks": len(first.omitted_block_ids),
        "first_compile_ms": first_ms,
        "cached_compile_ms": cached_ms,
        "cache_hit": cached.metrics.get("cache_hit"),
        "checkpoint_restore_ms": restore_ms,
        "checkpoint_round_trip": bool(restored and restored.checkpoint_id == checkpoint.checkpoint_id),
        "prefetch_intent": plan.predicted_intent,
        "prefetch_results": len(prefetched),
        "context_metrics": memory.context_metrics_snapshot(),
        "disclaimer": "Synthetic context-economics smoke benchmark, not a GPU KV-cache or NVIDIA hardware benchmark.",
    }
    memory.close()
    return result


def main() -> None:
    result = run()
    path = Path("reports/context_efficiency_v080.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
