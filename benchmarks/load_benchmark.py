from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from bio_agent_os import AccessContext, MemoryOS, MemoryType, TrustTier, VerificationStatus


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int((len(ordered) - 1) * p), len(ordered) - 1)
    return ordered[index]


def run(memory_count: int = 10_000, query_count: int = 100, batch_size: int = 1000) -> dict:
    memory = MemoryOS(":memory:")
    started = time.perf_counter()
    for offset in range(0, memory_count, batch_size):
        items = []
        for i in range(offset, min(offset + batch_size, memory_count)):
            content = f"Component unit-{i} uses protocol-{i % 37} and owner-team-{i % 19}"
            items.append({
                "content": content,
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.8,
                "verification_status": VerificationStatus.MACHINE_CHECKED,
                "importance": 0.5,
            })
        memory.bulk_ingest(
            tenant_id="load-tenant", actor="load-generator", source="benchmark",
            items=items, trust_tier=TrustTier.TRUSTED_SYSTEM,
        )
    ingest_seconds = time.perf_counter() - started

    ctx = AccessContext(tenant_id="load-tenant")
    # Separate one cold-start query from steady-state latency.
    warm_target = 0
    cold_started = time.perf_counter_ns()
    cold_results = memory.recall(
        f"Which protocol does component unit-{warm_target} use?", context=ctx, limit=1
    )
    cold_start_ms = (time.perf_counter_ns() - cold_started) / 1e6

    latencies: list[float] = []
    correct = 0
    candidate_sizes: list[int] = []
    for j in range(query_count):
        target = (j * 47) % memory_count
        query = f"Which protocol does component unit-{target} use?"
        start = time.perf_counter_ns()
        results = memory.recall(query, context=ctx, limit=1)
        latencies.append((time.perf_counter_ns() - start) / 1e6)
        if results:
            candidate_sizes.append(int(results[0].explanation.get("candidate_pool_size", 0)))
        if results and f"unit-{target}" in results[0].memory.content:
            correct += 1

    result = {
        "benchmark": "sqlite-fts5-indexed-load-smoke-v2",
        "memory_count": memory_count,
        "query_count": query_count,
        "batch_size": batch_size,
        "fts_available": bool(memory.memories.fts_available),
        "ingest_seconds": ingest_seconds,
        "ingest_per_second": memory_count / max(ingest_seconds, 1e-9),
        "top1_accuracy": correct / max(query_count, 1),
        "candidate_pool": {
            "median": statistics.median(candidate_sizes) if candidate_sizes else 0,
            "max": max(candidate_sizes) if candidate_sizes else 0,
        },
        "latency_ms": {
            "cold_start": cold_start_ms,
            "p50": statistics.median(latencies),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies),
        },
        "disclaimer": (
            "Single-process SQLite/FTS5 smoke test using the bulk-ingest path. "
            "It is not a distributed, concurrent or billion-memory benchmark."
        ),
    }
    memory.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memories", type=int, default=10_000)
    parser.add_argument("--queries", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--out", default="reports/load_benchmark_v080.json")
    args = parser.parse_args()
    result = run(args.memories, args.queries, args.batch_size)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
