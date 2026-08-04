from bio_agent_os import (
    AccessContext,
    BeliefState,
    EpistemicStatus,
    MemoryOS,
    MemoryType,
    TrustTier,
    VerificationStatus,
)

memory = MemoryOS(":memory:")
context = AccessContext(tenant_id="locaith", agent_id="jarvis", roles=("operator",))

event = memory.observe(
    tenant_id="locaith",
    actor="deployment-monitor",
    source="system:deployment",
    content="Production deployment failed because migration lock was held",
    trust_tier=TrustTier.TRUSTED_SYSTEM,
)

stored = memory.remember(
    event=event,
    memory_type=MemoryType.CAUSAL,
    content="A migration lock caused the production deployment failure",
    confidence=0.88,
    importance=0.9,
    lifecycle_state=BeliefState.REINFORCED,
    epistemic_status=EpistemicStatus.VERIFIED,
    verification_status=VerificationStatus.MACHINE_CHECKED,
    structured_content={
        "entity": "production-deployment",
        "attribute": "failure_cause",
        "value": "migration-lock",
    },
)

results = memory.recall(
    "Why did production deployment fail?",
    context=context,
    state={"risk_level": "high", "mode": "debug"},
)

packet = memory.compile_context(
    "How should we recover the production deployment?",
    context=context,
    goal="Recover production safely",
    state={"risk_level": "critical", "mode": "debug"},
    token_budget=512,
)

checkpoint = memory.create_checkpoint(
    tenant_id="locaith",
    agent_id="jarvis",
    workspace_id=None,
    goal="Recover production safely",
    completed_steps=("identify migration lock",),
    pending_steps=("clear lock", "rerun migration", "verify health"),
    active_memory_ids=packet.provenance_memory_ids,
)

plan, prefetched = memory.prefetch("Deploy the repaired release to production", context=context)

print("Stored memory:", stored.memory_id)
print("Recall top:", results[0].memory.content)
print("Compiled tokens:", packet.estimated_tokens)
print("Context blocks:", len(packet.blocks))
print("Checkpoint:", checkpoint.checkpoint_id)
print("Prefetch intent:", plan.predicted_intent, "results:", len(prefetched))
print("Rendered context:\n", packet.render())

memory.close()
