from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from bio_agent_os import AccessContext, BeliefState, MemoryOS, MemoryType, TrustTier
from bio_agent_os.cognitive.retrieval import cosine_counter, tokenize


CASES = [
    {"query":"Can we deploy on Friday?", "expected":"policy-friday", "kind":"policy"},
    {"query":"Is an emergency Friday hotfix allowed?", "expected":"exception-hotfix", "kind":"policy"},
    {"query":"What was the product price on January 15?", "expected":"price-old", "kind":"temporal", "as_of":"2026-01-15T00:00:00+00:00"},
    {"query":"What is the current product price?", "expected":"price-new", "kind":"factual"},
    {"query":"How do we fix the Vite peer conflict?", "expected":"proc-vite", "kind":"procedural"},
    {"query":"Why did the service recover?", "expected":"cause-recovery", "kind":"causal"},
    {"query":"Which database is selected for ERP?", "expected":"db-postgres", "kind":"factual"},
    {"query":"Can support agents read board compensation?", "expected":"policy-access", "kind":"policy"},
]


def build_os() -> tuple[MemoryOS, dict[str, str]]:
    os = MemoryOS(":memory:")
    ids = {}
    def add(key, content, mtype, trust=TrustTier.TRUSTED_SYSTEM, **kwargs):
        e = os.observe(tenant_id="locaith", actor="bench", source="fixture", content=content, trust_tier=trust, valid_from=kwargs.pop("valid_from", None), valid_to=kwargs.pop("valid_to", None))
        m = os.remember(event=e, memory_type=mtype, content=content, **kwargs)
        ids[key] = m.memory_id
        return m
    policy = add("policy-friday", "Production deployment is forbidden on Friday", MemoryType.POLICY, TrustTier.SIGNED_POLICY, confidence=.99, lifecycle_state=BeliefState.STABLE, approved_by="cto")
    add("exception-hotfix", "Emergency Friday hotfix is allowed with CTO approval", MemoryType.EXCEPTION, TrustTier.HUMAN_APPROVED, confidence=.98, lifecycle_state=BeliefState.STABLE, approved_by="cto", governed_exception_for=policy.memory_id)
    add("price-old", "Product price is 100 dollars", MemoryType.SEMANTIC, valid_from="2026-01-01T00:00:00+00:00", valid_to="2026-02-01T00:00:00+00:00")
    add("price-new", "Product price is 120 dollars", MemoryType.SEMANTIC, valid_from="2026-02-01T00:00:00+00:00")
    add("proc-vite", "To fix Vite peer conflict inspect the lockfile, align peer versions, clean install, then run tests", MemoryType.PROCEDURAL, confidence=.9, utility=.95, lifecycle_state=BeliefState.REINFORCED)
    add("cause-recovery", "The service recovered because the dependency endpoint became healthy, not because of restart", MemoryType.CAUSAL, confidence=.85)
    add("db-postgres", "PostgreSQL is selected as the ERP database", MemoryType.SEMANTIC, confidence=.95)
    add("policy-access", "Support agents cannot read restricted board compensation", MemoryType.POLICY, TrustTier.SIGNED_POLICY, confidence=.99, lifecycle_state=BeliefState.STABLE, approved_by="security")
    add("distractor1", "Friday is the fifth weekday in some calendars", MemoryType.SEMANTIC)
    add("distractor2", "Vite is a frontend build tool", MemoryType.SEMANTIC)
    add("distractor3", "Restarting services is a common operation", MemoryType.BELIEF, confidence=.9, lifecycle_state=BeliefState.CHALLENGED)
    return os, ids


def naive_recall(os: MemoryOS, query: str, as_of: str | None = None):
    q = Counter(tokenize(query))
    candidates = os.memories.active("locaith", as_of=as_of)
    ranked = sorted(candidates, key=lambda m: cosine_counter(q, Counter(tokenize(m.content))), reverse=True)
    return ranked[0] if ranked else None


def run() -> dict:
    os, ids = build_os()
    ctx = AccessContext(tenant_id="locaith", agent_id="bench")
    rows=[]
    naive_lat=[]
    hybrid_lat=[]
    naive_ok=0
    hybrid_ok=0
    for case in CASES:
        start=time.perf_counter_ns(); naive=naive_recall(os, case["query"], case.get("as_of")); naive_lat.append((time.perf_counter_ns()-start)/1e6)
        start=time.perf_counter_ns(); hybrid=os.recall(case["query"], context=ctx, as_of=case.get("as_of"), state={"risk_level":"high"}, limit=1); hybrid_lat.append((time.perf_counter_ns()-start)/1e6)
        expected=ids[case["expected"]]
        n_ok=bool(naive and naive.memory_id==expected)
        h_ok=bool(hybrid and hybrid[0].memory.memory_id==expected)
        naive_ok += n_ok; hybrid_ok += h_ok
        rows.append({"query":case["query"],"kind":case["kind"],"naive_correct":n_ok,"hybrid_correct":h_ok,"naive_top":naive.content if naive else None,"hybrid_top":hybrid[0].memory.content if hybrid else None})
    return {
        "dataset":"deterministic-foundation-smoke-v1",
        "cases":len(CASES),
        "naive_accuracy":naive_ok/len(CASES),
        "hybrid_accuracy":hybrid_ok/len(CASES),
        "absolute_accuracy_gain":(hybrid_ok-naive_ok)/len(CASES),
        "naive_latency_ms":{"p50":statistics.median(naive_lat),"max":max(naive_lat)},
        "hybrid_latency_ms":{"p50":statistics.median(hybrid_lat),"max":max(hybrid_lat)},
        "rows":rows,
        "warning":"Synthetic smoke benchmark. Not a substitute for LoCoMo/LongMemEval head-to-head evaluation."
    }


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--out", default="reports/foundation_benchmark.json"); args=parser.parse_args()
    result=run(); path=Path(args.out); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(result, indent=2), encoding="utf-8"); print(json.dumps(result, indent=2))

if __name__=="__main__": main()
