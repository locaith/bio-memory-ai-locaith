from __future__ import annotations

import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path

from bio_agent_os import (
    AccessContext,
    BeliefState,
    EpistemicStatus,
    MemoryOS,
    MemoryType,
    TrustTier,
    VerificationStatus,
)
from bio_agent_os.cognitive.retrieval import cosine_counter, tokenize


CASES = [
    {"id": "current-fact", "query": "What is the current product price?", "expected": "price-new"},
    {"id": "historical-fact", "query": "What was the product price on January 15?", "expected": "price-old", "as_of": "2026-01-15T00:00:00+00:00"},
    {"id": "policy", "query": "Can we deploy on Friday?", "expected": "policy-friday", "state": {"risk_level": "high"}},
    {"id": "active-exception", "query": "Is an emergency Friday hotfix allowed?", "expected": "exception-hotfix", "state": {"risk_level": "high"}},
    {"id": "debug-state", "query": "How do we install dependencies?", "expected": "proc-debug", "state": {"mode": "debug"}},
    {"id": "implement-state", "query": "How do we install dependencies?", "expected": "proc-implement", "state": {"mode": "implement"}},
    {"id": "windows-context", "query": "How do we restart the service?", "expected": "restart-windows", "state": {"os": "windows"}},
    {"id": "linux-context", "query": "How do we restart the service?", "expected": "restart-linux", "state": {"os": "linux"}},
    {"id": "verified-over-simulated", "query": "Which rollback procedure should production use?", "expected": "rollback-verified", "state": {"risk_level": "critical"}},
    {"id": "checked-over-rejected", "query": "What caused the outage?", "expected": "cause-checked", "state": {"risk_level": "high"}},
    {"id": "social", "query": "Who is the CTO?", "expected": "social-cto"},
    {"id": "spatial", "query": "Where is the production config path?", "expected": "spatial-config"},
]


def build_fixture() -> tuple[MemoryOS, dict[str, str]]:
    memory = MemoryOS(":memory:")
    ids: dict[str, str] = {}

    def add(key: str, content: str, memory_type: MemoryType, *, trust=TrustTier.TRUSTED_SYSTEM, **kwargs):
        event = memory.observe(
            tenant_id="bench", actor="fixture", source=f"fixture:{key}", content=content,
            trust_tier=trust, valid_from=kwargs.pop("valid_from", None), valid_to=kwargs.pop("valid_to", None),
            epistemic_status=kwargs.get("epistemic_status", EpistemicStatus.OBSERVED),
        )
        result = memory.remember(event=event, memory_type=memory_type, content=content, **kwargs)
        if not hasattr(result, "memory_id"):
            raise RuntimeError(f"fixture {key} quarantined: {result}")
        ids[key] = result.memory_id
        return result

    add("price-old", "Product price is 100 dollars", MemoryType.SEMANTIC,
        valid_from="2026-01-01T00:00:00+00:00", valid_to="2026-02-01T00:00:00+00:00")
    add("price-new", "Product price is 120 dollars", MemoryType.SEMANTIC,
        valid_from="2026-02-01T00:00:00+00:00")
    policy = add("policy-friday", "Production deployment is forbidden on Friday", MemoryType.POLICY,
                 trust=TrustTier.SIGNED_POLICY, confidence=.99, lifecycle_state=BeliefState.STABLE,
                 approved_by="cto", verification_status=VerificationStatus.HUMAN_APPROVED)
    add("exception-hotfix", "Emergency Friday hotfix is allowed with CTO approval", MemoryType.EXCEPTION,
        trust=TrustTier.HUMAN_APPROVED, confidence=.98, lifecycle_state=BeliefState.STABLE,
        approved_by="cto", governed_exception_for=policy.memory_id,
        verification_status=VerificationStatus.HUMAN_APPROVED)
    add("proc-debug", "Install dependencies by inspecting the lockfile first", MemoryType.PROCEDURAL,
        metadata={"state": {"mode": "debug"}}, confidence=.82)
    add("proc-implement", "Install dependencies with the standard package command", MemoryType.PROCEDURAL,
        metadata={"state": {"mode": "implement"}}, confidence=.82)
    add("restart-windows", "Restart the service using Windows Service Control Manager", MemoryType.PROCEDURAL,
        applicable_context={"os": "windows"}, confidence=.82)
    add("restart-linux", "Restart the service using systemctl on Linux", MemoryType.PROCEDURAL,
        applicable_context={"os": "linux"}, confidence=.82)
    add("rollback-verified", "Production should use the verified blue-green rollback procedure", MemoryType.PROCEDURAL,
        confidence=.72, verification_status=VerificationStatus.MACHINE_CHECKED)
    add("rollback-simulated", "Production should use the experimental instant rollback procedure", MemoryType.COUNTERFACTUAL,
        confidence=.99, epistemic_status=EpistemicStatus.SIMULATED)
    add("cause-rejected", "The cache caused the outage", MemoryType.CAUSAL,
        confidence=.99, verification_status=VerificationStatus.REJECTED)
    add("cause-checked", "A database lock caused the outage", MemoryType.CAUSAL,
        confidence=.72, verification_status=VerificationStatus.MACHINE_CHECKED)
    add("social-cto", "Lan is the CTO", MemoryType.SOCIAL, confidence=.9)
    add("spatial-config", "The production config path is /etc/locaith/prod.yaml", MemoryType.SPATIAL, confidence=.9)
    add("distractor-friday", "Friday is a weekday", MemoryType.SEMANTIC, confidence=.95)
    add("distractor-restart", "Restarting a computer can clear temporary state", MemoryType.SEMANTIC, confidence=.95)
    return memory, ids


def _search_only_score(query: str, content: str) -> float:
    """Portable approximation of search-centric hybrid retrieval.

    It combines token cosine and lexical overlap but intentionally has no
    policy, bitemporal, epistemic, state or context model. This is NOT a run of
    AgentMemory and must not be reported as such.
    """
    q = Counter(tokenize(query))
    c = Counter(tokenize(content))
    overlap = len(set(q) & set(c))
    return cosine_counter(q, c) * 2.0 + min(overlap * .3, 1.2)


def search_only_recall(memory: MemoryOS, query: str, as_of: str | None = None):
    candidates = memory.memories.active("bench", as_of=as_of)
    return max(candidates, key=lambda item: _search_only_score(query, item.content), default=None)


def run() -> dict:
    memory, ids = build_fixture()
    context = AccessContext(tenant_id="bench")
    search_correct = 0
    bio_correct = 0
    search_latency: list[float] = []
    bio_latency: list[float] = []
    rows = []

    for case in CASES:
        t0 = time.perf_counter_ns()
        baseline = search_only_recall(memory, case["query"], case.get("as_of"))
        search_latency.append((time.perf_counter_ns() - t0) / 1e6)

        t0 = time.perf_counter_ns()
        results = memory.recall(
            case["query"], context=context, as_of=case.get("as_of"),
            state=case.get("state", {}), limit=1,
        )
        bio_latency.append((time.perf_counter_ns() - t0) / 1e6)

        expected = ids[case["expected"]]
        baseline_ok = bool(baseline and baseline.memory_id == expected)
        bio_ok = bool(results and results[0].memory.memory_id == expected)
        search_correct += int(baseline_ok)
        bio_correct += int(bio_ok)
        rows.append({
            "case": case["id"],
            "query": case["query"],
            "search_only_correct": baseline_ok,
            "bio_cognitive_correct": bio_ok,
            "search_only_top": baseline.content if baseline else None,
            "bio_cognitive_top": results[0].memory.content if results else None,
            "bio_explanation": results[0].explanation if results else None,
        })

    total = len(CASES)
    return {
        "benchmark": "cognitive-differentiation-smoke-v1",
        "cases": total,
        "search_only_hybrid_accuracy": search_correct / total,
        "bio_cognitive_accuracy": bio_correct / total,
        "absolute_accuracy_gain": (bio_correct - search_correct) / total,
        "search_only_latency_ms": {"p50": statistics.median(search_latency), "max": max(search_latency)},
        "bio_cognitive_latency_ms": {"p50": statistics.median(bio_latency), "max": max(bio_latency)},
        "rows": rows,
        "disclaimer": (
            "The search-only baseline approximates lexical+semantic retrieval. "
            "It is not AgentMemory, Mem0, Letta, Zep or Cognee. Use the supplied "
            "external harness plan for real head-to-head evaluation."
        ),
    }


def main() -> None:
    result = run()
    path = Path("reports/cognitive_benchmark.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
