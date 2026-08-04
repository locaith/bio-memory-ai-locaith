from __future__ import annotations

import tempfile
import unittest

from bio_agent_os import (
    AccessContext, BeliefState, MemoryOS, MemoryType, TrustTier, VerificationStatus,
)


SCENARIOS = [
    ("coding", "Vite peer dependency error", "Inspect package lock before changing dependency versions", MemoryType.PROCEDURAL),
    ("erp", "ERP canonical customer database", "PostgreSQL is the canonical ERP customer database", MemoryType.SEMANTIC),
    ("legal", "contract advance payment approval", "Advance payment requires a payment request, not an acceptance certificate", MemoryType.POLICY),
    ("healthcare", "high risk medical response", "Medical high-risk outputs require clinician review and evidence citation", MemoryType.POLICY),
    ("finance", "maximum trade risk", "Maximum risk per trade is one percent of verified equity", MemoryType.POLICY),
    ("iot", "vehicle vibration bearing", "When vibration rises with normal tire pressure inspect wheel bearings", MemoryType.PROCEDURAL),
    ("education", "grade four lesson plan", "Grade four lessons require age-appropriate language and learning objectives", MemoryType.POLICY),
    ("cybersecurity", "production force push", "Never force push production without approved incident exception", MemoryType.POLICY),
    ("customer_support", "refund promise", "Customer ACME was promised a refund review within forty-eight hours", MemoryType.EPISODIC),
    ("family_assistant", "child pickup reminder", "Pick up Minh at school when the calendar reaches 16:30", MemoryType.PROSPECTIVE),
    ("blender_3d", "Blender non destructive scale", "Apply transforms only after preserving a backup and checking modifiers", MemoryType.PROCEDURAL),
    ("wind_energy", "wind project planning overlap", "If wind planning overlaps bauxite reserves propose phased land use", MemoryType.PROCEDURAL),
    ("devops", "rollback production deployment", "Production release requires a tested rollback command", MemoryType.POLICY),
    ("database", "database lock recovery", "Terminate the verified stale transaction before restarting the database", MemoryType.PROCEDURAL),
    ("robotics", "robot emergency stop", "Emergency stop overrides every autonomous motion command", MemoryType.POLICY),
    ("science", "experiment causal conclusion", "Do not promote correlation to cause without intervention or counterexample analysis", MemoryType.POLICY),
    ("sales", "enterprise discount approval", "Discounts above fifteen percent require sales director approval", MemoryType.POLICY),
    ("hr", "employee confidential salary", "Salary information is restricted to authorized HR roles", MemoryType.POLICY),
    ("manufacturing", "machine bearing temperature", "Stop the machine when bearing temperature exceeds the verified safety threshold", MemoryType.PROCEDURAL),
    ("multilingual_vi", "cơ sở dữ liệu ERP", "Cơ sở dữ liệu ERP chính thức là PostgreSQL", MemoryType.SEMANTIC),
]


class DomainCoverageBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.os = MemoryOS(tmp.name)
        self.addCleanup(self.os.close)
        self.ctx = AccessContext(tenant_id="org", agent_id="agent", workspace_id="main", roles=("authorized_hr", "operator"))

    def add(self, content: str, memory_type: MemoryType, domain: str):
        event = self.os.observe(
            tenant_id="org", actor="domain-fixture", source="verified-fixture", content=content,
            workspace_id="main", trust_tier=TrustTier.TRUSTED_SYSTEM,
        )
        return self.os.remember(
            event=event, memory_type=memory_type, content=content, importance=0.9,
            lifecycle_state=BeliefState.REINFORCED,
            verification_status=VerificationStatus.MACHINE_CHECKED,
            metadata={"domain": domain}, applicable_context={"domain": domain},
        )


def make_test(domain: str, query: str, expected: str, memory_type: MemoryType):
    def test(self):
        target = self.add(expected, memory_type, domain)
        for index in range(12):
            self.add(f"Unrelated {domain} record {index} about generic administration", MemoryType.EPISODIC, domain)
        results = self.os.recall(query, context=self.ctx, state={"domain": domain, "risk_level": "high"}, limit=3)
        self.assertTrue(results)
        self.assertEqual(target.memory_id, results[0].memory.memory_id)
        packet = self.os.compile_context(query, context=self.ctx, state={"domain": domain}, token_budget=200)
        self.assertIn(target.memory_id, packet.provenance_memory_ids)
    return test


for _domain, _query, _expected, _type in SCENARIOS:
    setattr(DomainCoverageBase, f"test_domain_{_domain}", make_test(_domain, _query, _expected, _type))


if __name__ == "__main__":
    unittest.main()
