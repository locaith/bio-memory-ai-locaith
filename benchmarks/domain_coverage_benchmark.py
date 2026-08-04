from __future__ import annotations

import json
from pathlib import Path

from bio_agent_os import AccessContext, BeliefState, MemoryOS, MemoryType, TrustTier, VerificationStatus


CASES = [
    ("coding", "Vite peer dependency error", "Inspect package lock before changing dependency versions", MemoryType.PROCEDURAL),
    ("erp", "ERP canonical customer database", "PostgreSQL is the canonical ERP customer database", MemoryType.SEMANTIC),
    ("legal", "contract advance payment approval", "Advance payment requires a payment request, not an acceptance certificate", MemoryType.POLICY),
    ("healthcare", "high risk medical response", "Medical high-risk outputs require clinician review and evidence citation", MemoryType.POLICY),
    ("finance", "maximum trade risk", "Maximum risk per trade is one percent of verified equity", MemoryType.POLICY),
    ("iot", "vehicle vibration bearing", "When vibration rises with normal tire pressure inspect wheel bearings", MemoryType.PROCEDURAL),
    ("education", "grade four lesson plan", "Grade four lessons require age-appropriate language and learning objectives", MemoryType.POLICY),
    ("cybersecurity", "production force push", "Never force push production without approved incident exception", MemoryType.POLICY),
    ("support", "refund promise ACME", "Customer ACME was promised a refund review within forty-eight hours", MemoryType.EPISODIC),
    ("personal", "child pickup reminder", "Pick up Minh at school when the calendar reaches 16:30", MemoryType.PROSPECTIVE),
    ("blender", "how to apply transform Blender scale", "Apply transforms only after preserving a backup and checking modifiers", MemoryType.PROCEDURAL),
    ("wind", "wind project planning overlap", "If wind planning overlaps bauxite reserves propose phased land use", MemoryType.PROCEDURAL),
    ("devops", "rollback production deployment", "Production release requires a tested rollback command", MemoryType.POLICY),
    ("database", "how to recover database lock", "Terminate the verified stale transaction before restarting the database", MemoryType.PROCEDURAL),
    ("robotics", "robot emergency stop", "Emergency stop overrides every autonomous motion command", MemoryType.POLICY),
    ("science", "experiment causal conclusion", "Do not promote correlation to cause without intervention or counterexample analysis", MemoryType.POLICY),
    ("sales", "enterprise discount approval", "Discounts above fifteen percent require sales director approval", MemoryType.POLICY),
    ("hr", "employee confidential salary", "Salary information is restricted to authorized HR roles", MemoryType.POLICY),
    ("manufacturing", "machine bearing temperature", "Stop the machine when bearing temperature exceeds the verified safety threshold", MemoryType.PROCEDURAL),
    ("vietnamese", "cơ sở dữ liệu ERP", "Cơ sở dữ liệu ERP chính thức là PostgreSQL", MemoryType.SEMANTIC),
]


def run() -> dict:
    rows = []
    top1 = 0
    context_recall = 0
    for domain, query, expected, memory_type in CASES:
        memory = MemoryOS(":memory:")
        ctx = AccessContext(tenant_id="org", agent_id="agent", workspace_id="main", roles=("operator", "authorized_hr"))
        event = memory.observe(
            tenant_id="org", actor="fixture", source="verified", content=expected,
            workspace_id="main", trust_tier=TrustTier.TRUSTED_SYSTEM,
        )
        target = memory.remember(
            event=event, memory_type=memory_type, content=expected, importance=.9,
            lifecycle_state=BeliefState.REINFORCED,
            verification_status=VerificationStatus.MACHINE_CHECKED,
            applicable_context={"domain": domain},
        )
        distractors = [{
            "content": f"Unrelated {domain} record {i} about generic administration",
            "memory_type": MemoryType.EPISODIC,
            "applicable_context": {"domain": domain},
        } for i in range(50)]
        memory.bulk_ingest(tenant_id="org", actor="fixture", source="distractor", items=distractors, workspace_id="main")
        results = memory.recall(query, context=ctx, state={"domain": domain, "risk_level": "high"}, limit=1)
        packet = memory.compile_context(query, context=ctx, state={"domain": domain}, token_budget=220)
        top_ok = bool(results and results[0].memory.memory_id == target.memory_id)
        context_ok = target.memory_id in packet.provenance_memory_ids
        top1 += int(top_ok)
        context_recall += int(context_ok)
        rows.append({"domain": domain, "top1": top_ok, "context_included": context_ok})
        memory.close()
    return {
        "benchmark": "multi-domain-coverage-smoke-v1",
        "domains": len(CASES),
        "top1_accuracy": top1 / len(CASES),
        "compiled_context_recall": context_recall / len(CASES),
        "rows": rows,
        "disclaimer": "Synthetic domain coverage verifies routing and isolation, not professional correctness in regulated fields.",
    }


def main() -> None:
    result = run()
    path = Path("reports/domain_coverage_v080.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
