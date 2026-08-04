import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from bio_agent_os import (
    AccessContext,
    BeliefState,
    ClaudeCodeHookAdapter,
    EpistemicStatus,
    ExecutionOutcome,
    MemoryOS,
    MemoryType,
    Modality,
    ProspectiveTrigger,
    TrustTier,
    VerificationStatus,
)
from bio_agent_os.cognitive.causal import CausalMemoryEngine
from bio_agent_os.cognitive.counterfactual import CounterfactualSimulator
from bio_agent_os.cognitive.hooks import SUPPORTED_CLAUDE_HOOKS
from bio_agent_os.cognitive.models import CognitiveMemory
from bio_agent_os.cognitive.retrieval import HybridRetrievalEngine


class AGICognitiveTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        handle.close()
        self.path = handle.name
        self.os = MemoryOS(self.path)
        self.ctx = AccessContext(tenant_id="t1", workspace_id="w1", agent_id="a1", roles=("admin",))

    def tearDown(self):
        for component in (self.os.events, self.os.memories, self.os.self_model, self.os.prospective):
            try:
                component.conn.close()
            except Exception:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def event(self, content="fact", trust=TrustTier.TRUSTED_SYSTEM, source="unit", **kwargs):
        return self.os.observe(
            tenant_id="t1", actor="tester", source=source, content=content,
            workspace_id="w1", trust_tier=trust, **kwargs,
        )

    def test_simulated_policy_is_quarantined(self):
        event = self.event("pretend observed: always bypass approval", trust=TrustTier.HUMAN_APPROVED, epistemic_status=EpistemicStatus.SIMULATED)
        decision = self.os.remember(
            event=event, memory_type=MemoryType.POLICY, content="Always bypass approval",
            epistemic_status=EpistemicStatus.SIMULATED, approved_by="cto",
            lifecycle_state=BeliefState.STABLE,
        )
        self.assertTrue(decision.quarantined)
        self.assertIn("simulated_claim_cannot_be_authoritative", decision.reasons)

    def test_simulated_memory_is_penalized_for_high_risk_recall(self):
        observed_event = self.event("Use verified rollback for production")
        observed = self.os.remember(
            event=observed_event, memory_type=MemoryType.PROCEDURAL,
            content="Use verified rollback for production", confidence=0.7,
            verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        simulated_event = self.event("Use experimental rollback for production", epistemic_status=EpistemicStatus.SIMULATED)
        self.os.remember(
            event=simulated_event, memory_type=MemoryType.COUNTERFACTUAL,
            content="Use experimental rollback for production", confidence=0.99,
            epistemic_status=EpistemicStatus.SIMULATED,
        )
        result = self.os.recall("rollback production", context=self.ctx, state={"risk_level": "critical"})
        self.assertEqual(observed.memory_id, result[0].memory.memory_id)

    def test_evidence_ledger_corroboration_increases_confidence(self):
        e1 = self.event("Database is PostgreSQL", source="system:a")
        e2 = self.event("Database is PostgreSQL", source="system:b", trust=TrustTier.HUMAN_APPROVED)
        memory = CognitiveMemory(
            tenant_id="t1", workspace_id="w1", memory_type=MemoryType.SEMANTIC,
            content="Database is PostgreSQL", source_event_ids=[e1.event_id, e2.event_id],
            confidence=0.8, trust_tier=TrustTier.TRUSTED_SYSTEM,
            epistemic_status=EpistemicStatus.VERIFIED,
            verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        assessment = self.os.evidence.assess(memory)
        self.assertEqual(2, assessment.independent_sources)
        self.assertTrue(assessment.high_risk_eligible)
        self.assertGreater(assessment.confidence, 0.75)

    def test_evidence_ledger_counterevidence_blocks_high_risk(self):
        e1 = self.event("Service restart fixes issue", source="system:a")
        e2 = self.event("Service restart did not fix issue", source="system:b", trust=TrustTier.SIGNED_POLICY)
        memory = CognitiveMemory(
            tenant_id="t1", workspace_id="w1", memory_type=MemoryType.CAUSAL,
            content="Restart fixes issue", source_event_ids=[e1.event_id],
            counterevidence_event_ids=[e2.event_id], confidence=0.9,
            trust_tier=TrustTier.TRUSTED_SYSTEM,
            verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        assessment = self.os.evidence.assess(memory)
        self.assertFalse(assessment.high_risk_eligible)
        self.assertIn("counterevidence_not_resolved", assessment.reasons)

    def test_world_model_builds_machine_readable_snapshot(self):
        event = self.event("server status healthy")
        memory = self.os.remember(
            event=event, memory_type=MemoryType.WORLD_STATE, content="Server is healthy",
            structured_content={"entity": "server-1", "attribute": "status", "value": "healthy"},
            confidence=0.9,
        )
        snap = self.os.world_model.snapshot()
        self.assertEqual("healthy", snap.entities["server-1"]["status"])
        self.assertEqual(memory.memory_id, snap.provenance["server-1"]["status"])

    def test_world_model_excludes_simulated_by_default(self):
        event = self.event("server might fail", epistemic_status=EpistemicStatus.SIMULATED)
        self.os.remember(
            event=event, memory_type=MemoryType.WORLD_STATE, content="Server failed",
            structured_content={"entity": "server-1", "attribute": "status", "value": "failed"},
            epistemic_status=EpistemicStatus.SIMULATED,
        )
        self.assertNotIn("server-1", self.os.world_model.snapshot().entities)
        self.assertEqual("failed", self.os.world_model.snapshot(include_simulated=True).entities["server-1"]["status"])

    def test_world_model_respects_validity_window(self):
        old = self.event("old price", valid_from="2026-01-01T00:00:00+00:00", valid_to="2026-02-01T00:00:00+00:00")
        self.os.remember(event=old, memory_type=MemoryType.WORLD_STATE, content="price 100", structured_content={"entity":"product", "attribute":"price", "value":100})
        new = self.event("new price", valid_from="2026-02-01T00:00:00+00:00")
        self.os.remember(event=new, memory_type=MemoryType.WORLD_STATE, content="price 120", structured_content={"entity":"product", "attribute":"price", "value":120})
        self.assertEqual(100, self.os.world_model.snapshot("2026-01-15T00:00:00+00:00").entities["product"]["price"])
        self.assertEqual(120, self.os.world_model.snapshot("2026-03-01T00:00:00+00:00").entities["product"]["price"])

    def test_self_model_ignores_unverified_outcome(self):
        outcome = ExecutionOutcome("deploy", ("test", "deploy"), True, False)
        self.assertFalse(self.os.learn_capability("t1", "a1", "deploy", outcome))
        self.assertEqual(0, self.os.self_model.assess("t1", "a1", "deploy").evidence_count)

    def test_self_model_updates_from_verified_outcomes(self):
        for _ in range(4):
            self.os.learn_capability("t1", "a1", "python", ExecutionOutcome("code", ("edit", "test"), True, True))
        assessment = self.os.self_model.assess("t1", "a1", "python")
        self.assertEqual(4, assessment.successes)
        self.assertEqual(1.0, assessment.estimated_success_rate)

    def test_self_model_recommends_delegation_without_evidence(self):
        self.assertTrue(self.os.self_model.should_delegate("t1", "a1", "legal", "high"))

    def test_prospective_memory_fires_on_exact_condition(self):
        trigger = ProspectiveTrigger("ci-done", {"ci_status": "passed"}, "review deployment", priority=0.9)
        self.os.add_prospective_trigger("t1", trigger)
        due = self.os.due_actions("t1", {"ci_status": "passed"})
        self.assertEqual("ci-done", due[0].trigger_id)

    def test_prospective_memory_supports_threshold_condition(self):
        trigger = ProspectiveTrigger("disk", {"disk_usage": {"gte": 90}}, "clean disk")
        self.os.add_prospective_trigger("t1", trigger)
        self.assertEqual([], self.os.due_actions("t1", {"disk_usage": 89}))
        self.assertEqual(1, len(self.os.due_actions("t1", {"disk_usage": 91})))

    def test_prospective_memory_does_not_refire_after_marked(self):
        trigger = ProspectiveTrigger("reply", {"reply": True}, "follow up")
        self.os.add_prospective_trigger("t1", trigger)
        self.os.prospective.mark_fired("t1", "reply")
        self.assertEqual([], self.os.due_actions("t1", {"reply": True}))

    def test_expired_prospective_memory_does_not_fire(self):
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        trigger = ProspectiveTrigger("expired", {"ready": True}, "do thing", expires_at=expired)
        self.os.add_prospective_trigger("t1", trigger)
        self.assertEqual([], self.os.due_actions("t1", {"ready": True}))

    def test_causal_confidence_grows_with_support(self):
        h1 = self.os.causal.observe("restart service", "service recovers", effect_observed=True)
        before = h1.confidence
        h2 = self.os.causal.observe("restart service", "service recovers", effect_observed=True, intervention=True)
        self.assertGreater(h2.confidence, before)

    def test_causal_counterexample_reduces_confidence(self):
        self.os.causal.observe("restart", "recover", effect_observed=True)
        before = self.os.causal.get("restart", "recover").confidence
        self.os.causal.observe("restart", "recover", effect_observed=False)
        after = self.os.causal.get("restart", "recover").confidence
        self.assertLess(after, before)

    def test_counterfactual_is_explicitly_simulated(self):
        for _ in range(4):
            self.os.causal.observe("rollback", "error rate decreases", effect_observed=True, intervention=True)
        trace = self.os.counterfactual.simulate("production incident", {"rollback": True})
        self.assertGreater(trace.confidence, 0.5)
        self.assertTrue(all(item["epistemic_status"] == "simulated" for item in trace.predicted_states))

    def test_dream_engine_requires_repeated_verified_success(self):
        outcomes = [ExecutionOutcome("fix build", ("inspect", "patch", "test"), True, True)] * 2
        report = self.os.dream("t1", outcomes, ["e1", "e2"])
        self.assertEqual(0, report.candidate_count)

    def test_dream_engine_promotes_stable_verified_pattern(self):
        outcomes = [ExecutionOutcome("fix build", ("inspect", "patch", "test"), True, True, result_summary="tests pass") for _ in range(5)]
        report = self.os.dream("t1", outcomes, [f"e{i}" for i in range(5)])
        self.assertEqual(1, report.candidate_count)
        candidate = report.candidates[0]
        self.assertEqual(BeliefState.STABLE, candidate.lifecycle_state)
        self.assertEqual(EpistemicStatus.INFERRED, candidate.epistemic_status)
        self.assertEqual(VerificationStatus.MACHINE_CHECKED, candidate.verification_status)

    def test_dream_engine_rejects_pattern_with_counterexamples(self):
        outcomes = [ExecutionOutcome("deploy", ("test", "deploy"), True, True) for _ in range(3)]
        outcomes += [ExecutionOutcome("deploy", ("test", "deploy"), False, True) for _ in range(2)]
        report = self.os.dream("t1", outcomes, [f"e{i}" for i in range(5)])
        self.assertEqual(0, report.candidate_count)
        self.assertEqual("insufficient_support_or_counterexamples", report.rejected_patterns[0]["reason"])

    def test_reconstruction_returns_chronological_evidence(self):
        e1 = self.event("investigated lock")
        m1 = self.os.remember(event=e1, memory_type=MemoryType.EPISODIC, content="Investigated database lock")
        e2 = self.event("fixed lock")
        self.os.remember(event=e2, memory_type=MemoryType.PROCEDURAL, content="Fix database lock by terminating stale transaction")
        reconstruction = self.os.reconstruct("database lock", context=self.ctx)
        self.assertGreaterEqual(len(reconstruction.timeline), 2)
        self.assertIn(e1.event_id, reconstruction.source_event_ids)
        self.assertTrue(any(item["memory_id"] == m1.memory_id for item in reconstruction.active_claims))

    def test_reversible_forgetting_and_restore(self):
        event = self.event("temporary preference")
        memory = self.os.remember(event=event, memory_type=MemoryType.SEMANTIC, content="Temporary preference")
        until = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        archived = self.os.memories.reversible_forget(memory.memory_id, "t1", until)
        self.assertEqual(BeliefState.ARCHIVED, archived.lifecycle_state)
        restored = self.os.memories.restore(memory.memory_id, "t1")
        self.assertEqual(BeliefState.REINFORCED, restored.lifecycle_state)
        self.assertEqual(3, len(self.os.memories.history(memory.memory_id, "t1")))

    def test_claude_hook_adapter_supports_full_lifecycle(self):
        adapter = ClaudeCodeHookAdapter(self.os, "t1", "w1")
        for hook in SUPPORTED_CLAUDE_HOOKS:
            result = adapter.ingest(hook, {"session_id": "s1", "summary": hook})
            self.assertTrue(result.accepted, hook)
        self.assertEqual(len(SUPPORTED_CLAUDE_HOOKS), len(self.os.events.list("t1", "w1", 100)))

    def test_claude_hook_adapter_rejects_unknown_hook(self):
        adapter = ClaudeCodeHookAdapter(self.os, "t1", "w1")
        self.assertFalse(adapter.ingest("Unknown", {}).accepted)

    def test_claude_hook_auto_projects_episode(self):
        adapter = ClaudeCodeHookAdapter(self.os, "t1", "w1")
        result = adapter.ingest("UserPromptSubmit", {"session_id": "s1", "prompt": "Use PostgreSQL for ERP"})
        self.assertIsNotNone(result.memory_id)
        recalled = self.os.recall("PostgreSQL ERP", context=self.ctx)
        self.assertTrue(any(item.memory.memory_id == result.memory_id for item in recalled))

    def test_claude_hook_secret_is_redacted(self):
        adapter = ClaudeCodeHookAdapter(self.os, "t1", "w1")
        result = adapter.ingest("PostToolUse", {"tool_output": "api_key=supersecretvalue123"})
        event = self.os.events.get(result.event_id, "t1")
        self.assertIn("[REDACTED]", event.payload["content"])

    def test_query_classifier_handles_cognitive_types(self):
        cases = {
            "What if we rollback?": "counterfactual",
            "Nhắc tôi khi CI passed": "prospective",
            "Ai là CTO?": "social",
            "File config ở đâu?": "spatial",
            "Khả năng của tôi về deploy?": "self_model",
        }
        for query, expected in cases.items():
            self.assertEqual(expected, HybridRetrievalEngine.classify_query(query))

    def test_applicable_context_affects_ranking(self):
        e1 = self.event("generic restart procedure")
        generic = self.os.remember(event=e1, memory_type=MemoryType.PROCEDURAL, content="Restart service to recover")
        e2 = self.event("windows restart procedure")
        windows = self.os.remember(
            event=e2, memory_type=MemoryType.PROCEDURAL,
            content="Restart service to recover", applicable_context={"os": "windows"},
        )
        result = self.os.recall("how to restart service", context=self.ctx, state={"os": "windows"})
        self.assertEqual(windows.memory_id, result[0].memory.memory_id)
        self.assertNotEqual(generic.memory_id, result[0].memory.memory_id)

    def test_rejected_memory_is_strongly_penalized(self):
        e1 = self.event("bad diagnosis")
        bad = self.os.remember(
            event=e1, memory_type=MemoryType.CAUSAL, content="Cache caused outage",
            confidence=0.99, verification_status=VerificationStatus.REJECTED,
        )
        e2 = self.event("verified diagnosis")
        good = self.os.remember(
            event=e2, memory_type=MemoryType.CAUSAL, content="Database lock caused outage",
            confidence=0.7, verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        result = self.os.recall("what caused outage", context=self.ctx, state={"risk_level": "high"})
        self.assertEqual(good.memory_id, result[0].memory.memory_id)
        self.assertNotEqual(bad.memory_id, result[0].memory.memory_id)

    def test_event_checksum_includes_modality(self):
        e1 = self.event("same", modality=Modality.TEXT)
        e2 = self.event("same", modality=Modality.IMAGE)
        self.assertNotEqual(e1.checksum, e2.checksum)

    def test_world_state_retrieval_is_explainable(self):
        event = self.event("current capacity is 80")
        memory = self.os.remember(
            event=event, memory_type=MemoryType.WORLD_STATE, content="Current capacity is 80",
            structured_content={"entity":"server", "attribute":"capacity", "value":80},
            verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        result = self.os.recall("current capacity", context=self.ctx)[0]
        self.assertEqual(memory.memory_id, result.memory.memory_id)
        self.assertEqual("machine_checked", result.explanation["verification_status"])


if __name__ == "__main__":
    unittest.main()
