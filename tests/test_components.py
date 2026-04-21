import asyncio
import os
import time
from pathlib import Path

import pytest

from bio_agent_os import (
    ApprovalQueue,
    AsyncSQLiteStore,
    BioAgentRESTClient,
    ContradictionResolver,
    EpisodeStore,
    L1WorkingMemory,
    L2SemanticMemory,
    MemoryHealthMonitor,
    Persona,
    RetrievalService,
)
from bio_agent_os.background_jobs.hippocampus import CompiledMemory
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.core.db_adapter import (
    SQLiteAdapter,
    _translate_insert_or_replace,
    _translate_qmark_placeholders,
    resolve_database_backend,
)
from bio_agent_os.core.migration import SQLiteToPostgresMigrator, map_sqlite_type
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.embedder import Embedder
from bio_agent_os.core.reconciliation import RuleRelationDecision
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.plugins.openclaw import OpenClawMemoryPlugin, build_openclaw_plugin
from bio_agent_os.plugins.swe_agent import SWEAgentMemoryPlugin, build_swe_agent_plugin


def test_l1_memory():
    l1 = L1WorkingMemory(agent_name="test_agent", storage_dir="test_data")
    l1.clear()
    l1.add(
        "Login endpoint failing",
        metadata={"importance_score": 8, "is_junk_or_transient": False},
    )

    assert l1.count == 1
    assert "Login endpoint" in l1.build_context_string()
    assert l1.get_focus_set(1)[0]["attention_score"] > 0.0


def test_database_backend_defaults_to_sqlite():
    adapter = resolve_database_backend(storage_dir="test_data", db_name="adapter_test.db")
    assert isinstance(adapter, SQLiteAdapter)


def test_postgres_placeholder_translation():
    translated = _translate_qmark_placeholders("SELECT * FROM table WHERE id = ? AND scope = ?")
    assert translated == "SELECT * FROM table WHERE id = %s AND scope = %s"


def test_insert_or_replace_translation():
    translated = _translate_insert_or_replace(
        "INSERT OR REPLACE INTO sample_table (rule_id, text, updated_at) VALUES (?, ?, ?)"
    )
    assert "ON CONFLICT (rule_id) DO UPDATE SET" in translated
    assert "text=EXCLUDED.text" in translated


def test_async_sqlite_store_helpers():
    assert AsyncSQLiteStore.sanitize_identifier("Bio Agent OS") == "bio_agent_os"
    assert AsyncSQLiteStore.loads_json("", default=[]) == []


def test_sqlite_type_mapping_for_postgres_migration():
    assert map_sqlite_type("TEXT") == "TEXT"
    assert map_sqlite_type("INTEGER") == "BIGINT"
    assert map_sqlite_type("REAL") == "DOUBLE PRECISION"


def test_migration_create_table_sql():
    migrator = SQLiteToPostgresMigrator.__new__(SQLiteToPostgresMigrator)
    sql = migrator._create_table_sql(
        "sample_table",
        [
            {"name": "rule_id", "type": "TEXT", "notnull": 1, "dflt_value": None, "pk": 1},
            {"name": "confidence", "type": "REAL", "notnull": 1, "dflt_value": "0.5", "pk": 0},
        ],
    )
    assert "CREATE TABLE IF NOT EXISTS sample_table" in sql
    assert "rule_id TEXT NOT NULL" in sql
    assert "confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5" in sql
    assert "PRIMARY KEY (rule_id)" in sql


def test_rest_client_headers():
    client = BioAgentRESTClient(base_url="http://127.0.0.1:8055", api_key="secret")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_embedder_falls_back_to_hash_when_provider_embedding_fails():
    embedder = Embedder.__new__(Embedder)
    embedder.backend = "gemini"
    embedder.model_id = "broken-model"
    embedder._client = object()
    embedder._model = None
    embedder._dimensions = 384
    embedder._fallback_backend = "hash"
    embedder._fallback_active = False

    class FailingModels:
        def embed_content(self, model, contents):
            raise RuntimeError("embedding unavailable")

    class FailingClient:
        models = FailingModels()

    embedder._client = FailingClient()
    vector = Embedder.embed(embedder, "fallback me")
    assert len(vector) == 384
    assert embedder.effective_backend == "gemini->hash"


@pytest.mark.asyncio
async def test_sensitive_rule_pending_approval_still_persists_l2_memory():
    class ApprovalSensitiveEngine:
        backend = "test"
        model_id = "approval-sensitive"

        async def generate_structured(self, prompt, schema, temperature=0.1, effort=None):
            if schema.__name__ == "MemoryLabel":
                return {
                    "topic": "git",
                    "importance_score": 9,
                    "is_junk_or_transient": False,
                    "user_state": "urgent",
                }
            if schema.__name__ == "CompiledMemory":
                return {
                    "episodic_summary": "Approved hotfix policy recorded.",
                    "semantic_memory": "Approved hotfixes can override the default frontend branch rule.",
                    "procedural_memory": "Require approval and audit logging before any frontend hotfix force push.",
                    "exception_memory": "Exception: approved frontend hotfixes may use git push -f with audit logging.",
                    "identity_rule": "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
                    "confidence": 0.94,
                    "scope": "project",
                }
            raise ValueError(schema.__name__)

    for file_path in (
        Path("test_data/pending-approval-agent_core_identity.json"),
        Path("test_data/pending-approval-agent_episodes.json"),
        Path("test_data/pending-approval-agent_l1_memory.json"),
        Path("test_data/pending-approval-agent_l2_memory.json"),
        Path("test_data/pending-approval-agent_approvals.json"),
    ):
        if file_path.exists():
            file_path.unlink()

    engine = ApprovalSensitiveEngine()
    l1 = L1WorkingMemory(agent_name="pending-approval-agent", storage_dir="test_data")
    l1.clear()
    l2 = L2SemanticMemory(agent_name="pending-approval-agent", storage_dir="test_data")
    persona = Persona(name="pending-approval-agent", storage_dir="test_data")
    approvals = ApprovalQueue(agent_name="pending-approval-agent", storage_dir="test_data")
    episodes = EpisodeStore(agent_name="pending-approval-agent", storage_dir="test_data")
    hippo = Hippocampus(
        engine=engine,
        l1=l1,
        l2=l2,
        persona=persona,
        episodes=episodes,
        approval_queue=approvals,
    )

    await hippo.label_and_store(
        "Approved hotfixes may use git push -f on the frontend branch only with explicit approval and audit logging.",
        source="openclaw-user",
        task_id="approval-task",
        workspace_id="frontend",
        project_version="v3.1.0",
        observation_type="policy_hotfix",
    )
    l1.increment_nights()
    l1.increment_nights()
    l1.increment_nights()
    result = await hippo.consolidate()

    assert result["pending_approval"] == 1
    assert persona.rule_count == 0
    assert approvals.pending_count == 1
    assert l2.count >= 3
    results = l2.search(
        "frontend hotfix force push approval and audit logging",
        top_k=5,
        retrieval_state={
            "mode": "deploy",
            "stress_state": "normal",
            "risk_level": "high",
            "task_id": "approval-task",
            "workspace_id": "frontend",
            "project_version": "v3.1.0",
            "prefer_exception": True,
        },
    )
    assert any(item["memory_type"] == "exception" for item in results)


class FakeNLIEngine:
    is_ready = True

    def __init__(self):
        self.calls = 0

    async def generate_structured(self, prompt, schema, temperature=0.0, effort=None):
        self.calls += 1
        if schema.__name__ != "RuleRelationDecision":
            raise ValueError(schema.__name__)
        lowered = prompt.lower()
        if "overnight only" in lowered and "every production release must happen at 10 am" in lowered:
            return {
                "relation": "contradiction",
                "confidence": 0.93,
                "reason": "Night-only deployment conflicts with a 10 AM server update.",
            }
        if "tenant a" in lowered and "after onboarding" in lowered:
            return {
                "relation": "governed_exception",
                "confidence": 0.91,
                "reason": "Tenant-specific override is a governed exception of the default onboarding policy.",
            }
        return {
            "relation": "neutral",
            "confidence": 0.6,
            "reason": "No contradiction detected.",
        }


def test_l2_semantic_memory():
    l2 = L2SemanticMemory(agent_name="test_agent", storage_dir="test_data")
    l2.store("Always use generic exception handling", importance=8.0, tags=["coding"])

    results = l2.search("exception handling", top_k=1)
    assert len(results) > 0
    assert results[0]["importance"] == 8.0


def test_state_dependent_retrieval_prefers_exception_memory_for_debug():
    l2 = L2SemanticMemory(agent_name="stateful_agent", storage_dir="test_data")
    l2.store(
        "Check the lockfile and plugin major versions before changing code.",
        importance=7.0,
        tags=["dependency", "procedural"],
        memory_type="procedural",
        mode_hints=["implement"],
        risk_level="medium",
        stress_state="normal",
        workspace_id="erp-frontend",
    )
    l2.store_exception(
        "Exception: tenant X deployment breaks if vite is upgraded without pinning the plugin first.",
        exception_for="dependency",
        importance=9.0,
        tags=["dependency"],
        mode_hints=["debug", "deploy"],
        workspace_id="erp-frontend",
    )

    results = l2.search(
        "vite plugin dependency issue",
        top_k=2,
        retrieval_state={
            "mode": "debug",
            "stress_state": "failure",
            "risk_level": "high",
            "workspace_id": "erp-frontend",
            "prefer_exception": True,
        },
    )
    assert results[0]["memory_type"] == "exception"
    assert results[0]["exception_for"] == "dependency"


def test_persona_rule_lifecycle():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    identity_file = Path(storage_dir) / "test_agent_v2_core_identity.json"
    if identity_file.exists():
        identity_file.unlink()

    persona = Persona(name="test_agent_v2", storage_dir=storage_dir)
    first_rule_id = persona.add_rule(
        "Never force push to the frontend branch.",
        scope="project",
        confidence=0.7,
        evidence_episode_ids=["ep1"],
    )
    second_rule_id = persona.add_rule(
        "Never force push to the frontend branch.",
        scope="project",
        confidence=0.7,
        evidence_episode_ids=["ep2"],
    )

    rules = persona.get_rule_records()
    assert first_rule_id == second_rule_id
    assert rules[first_rule_id]["support_count"] == 2
    assert rules[first_rule_id]["state"] == "reinforced"
    assert "Never force push" in persona.get_identity_prompt(include_scopes=["project"])


def test_episode_store():
    episodes = EpisodeStore(agent_name="test_agent", storage_dir="test_data")
    record = episodes.add(
        raw_payload="npm install failed with peer dependency mismatch",
        actor="OpenClaw-Worker",
        source="terminal",
        topic="dependency",
        confidence=0.8,
        task_id="task-142",
        workspace_id="workspace-main",
        project_version="v2.1.0",
        source_refs=["terminal://1", {"kind": "git", "ref": "commit:abc123"}],
    )
    assert record["episode_id"]
    assert episodes.get(record["episode_id"]) is not None
    assert record["task_id"] == "task-142"
    assert record["workspace_id"] == "workspace-main"
    assert record["project_version"] == "v2.1.0"
    assert len(record["source_refs"]) == 2


def test_contradiction_resolver_deprecates_weaker_rule():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    identity_file = Path(storage_dir) / "test_agent_reconcile_core_identity.json"
    if identity_file.exists():
        identity_file.unlink()

    persona = Persona(name="test_agent_reconcile", storage_dir=storage_dir)
    old_rule_id = persona.add_rule(
        "Always use git push -f on the frontend branch.",
        scope="project",
        confidence=0.55,
        evidence_episode_ids=["old"],
    )
    new_rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=["new"],
    )

    resolver = ContradictionResolver(persona)
    assert old_rule_id in resolver.find_conflicts(new_rule_id)
    stats = resolver.reconcile(new_rule_id)
    rules = persona.get_rule_records()

    assert stats["deprecated"] == 1
    assert rules[old_rule_id]["state"] == "deprecated"
    assert rules[old_rule_id]["superseded_by"] == new_rule_id


def test_conditional_exception_is_reinforced_without_deprecating_default_policy():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    persona = Persona(name="conditional_exception_agent", storage_dir="test_data")
    policy_rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.9,
        evidence_episode_ids=["neg-1"],
        promotion_threshold=3,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.9,
        evidence_episode_ids=["neg-2"],
        promotion_threshold=3,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.9,
        evidence_episode_ids=["neg-3"],
        promotion_threshold=3,
    )

    exception_rule_id = persona.add_rule(
        "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
        scope="project",
        confidence=0.86,
        evidence_episode_ids=["pos-1"],
        promotion_threshold=3,
    )
    persona.add_rule(
        "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
        scope="project",
        confidence=0.86,
        evidence_episode_ids=["pos-2"],
        promotion_threshold=3,
    )

    resolver = ContradictionResolver(persona)
    stats = resolver.reconcile(exception_rule_id)
    rules = persona.get_rule_records()

    assert stats["governed"] >= 1
    assert rules[policy_rule_id]["state"] == "stable"
    assert rules[exception_rule_id]["state"] == "reinforced"
    assert rules[exception_rule_id]["confidence"] >= 0.9


@pytest.mark.asyncio
async def test_hybrid_nli_detector_finds_semantic_contradiction_beyond_keyword_overlap():
    persona = Persona(name="nli_contradiction_agent", storage_dir="test_data")
    rule_a_id = persona.add_rule(
        "Deploy production releases overnight only.",
        scope="project",
        confidence=0.8,
        evidence_episode_ids=["ep-a"],
    )
    rule_b_id = persona.add_rule(
        "Every production release must happen at 10 AM every business day.",
        scope="project",
        confidence=0.82,
        evidence_episode_ids=["ep-b"],
    )
    resolver = ContradictionResolver(
        persona,
        engine=FakeNLIEngine(),
        detector_mode="hybrid",
    )
    relation = await resolver.classify_relation(
        persona.get_rule_records()[rule_a_id],
        persona.get_rule_records()[rule_b_id],
    )
    assert relation.relation == "contradiction"
    stats = await resolver.areconcile(rule_b_id)
    assert stats["challenged"] + stats["deprecated"] >= 1


@pytest.mark.asyncio
async def test_hybrid_nli_detector_preserves_governed_exception():
    persona = Persona(name="nli_exception_agent", storage_dir="test_data")
    default_rule_id = persona.add_rule(
        "Never rename ERP customer codes after onboarding.",
        scope="project",
        confidence=0.9,
        evidence_episode_ids=["ep-default"],
    )
    exception_rule_id = persona.add_rule(
        "Allow customer code rename for Tenant A only with finance approval and audit logging.",
        scope="project",
        confidence=0.88,
        evidence_episode_ids=["ep-override-1"],
    )
    persona.add_rule(
        "Allow customer code rename for Tenant A only with finance approval and audit logging.",
        scope="project",
        confidence=0.88,
        evidence_episode_ids=["ep-override-2"],
    )
    resolver = ContradictionResolver(
        persona,
        engine=FakeNLIEngine(),
        detector_mode="hybrid",
    )
    relation = await resolver.classify_relation(
        persona.get_rule_records()[default_rule_id],
        persona.get_rule_records()[exception_rule_id],
    )
    assert relation.relation == "governed_exception"
    stats = await resolver.areconcile(exception_rule_id)
    assert stats["governed"] >= 1


@pytest.mark.asyncio
async def test_hybrid_nli_detector_uses_cache_for_repeated_pair():
    engine = FakeNLIEngine()
    persona = Persona(name="nli_cache_agent", storage_dir="test_data")
    resolver = ContradictionResolver(
        persona,
        engine=engine,
        detector_mode="hybrid",
    )
    left = {"text": "Deploy production releases overnight only.", "scope": "project"}
    right = {"text": "Every production release must happen at 10 AM every business day.", "scope": "project"}
    first = await resolver.classify_relation(left, right)
    second = await resolver.classify_relation(left, right)
    assert first.relation == "contradiction"
    assert second.reason == "nli-cache"
    assert engine.calls == 1


def test_belief_graph_and_health_snapshot():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    identity_file = Path(storage_dir) / "test_agent_health_core_identity.json"
    episode_file = Path(storage_dir) / "test_agent_health_episodes.json"
    graph_file = Path(storage_dir) / "test_agent_health_knowledge_graph.json"
    for file_path in (identity_file, episode_file, graph_file):
        if file_path.exists():
            file_path.unlink()

    persona = Persona(name="test_agent_health", storage_dir=storage_dir)
    episodes = EpisodeStore(agent_name="test_agent_health", storage_dir=storage_dir)
    l1 = L1WorkingMemory(agent_name="test_agent_health", storage_dir=storage_dir)
    l2 = L2SemanticMemory(agent_name="test_agent_health", storage_dir=storage_dir)
    graph = KnowledgeGraph(agent_name="test_agent_health", storage_dir=storage_dir)

    episode = episodes.add(
        raw_payload="Do not force push to frontend main.",
        actor="agent",
        source="terminal",
        topic="git",
        confidence=0.9,
    )
    rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.8,
        evidence_episode_ids=[episode["episode_id"]],
    )
    rule = persona.get_rule_records()[rule_id]
    graph.add_belief_rule(rule)
    graph.add_episode_evidence(rule_id, episode["episode_id"], confidence=0.8)

    monitor = MemoryHealthMonitor(l1=l1, l2=l2, persona=persona, episodes=episodes, graph=graph)
    snapshot = monitor.snapshot()

    assert snapshot["belief_graph"]["belief_rules"] == 1
    assert snapshot["belief_graph"]["support_edges"] == 1
    assert snapshot["rules_total"] == 1

    belief_results = graph.retrieve_beliefs(
        "frontend force push policy",
        top_k=1,
        retrieval_state={"preferred_scope": "project"},
    )
    assert belief_results
    assert belief_results[0]["rule_id"] == rule_id


def test_l1_attention_scheduler_prioritizes_unresolved_errors():
    l1 = L1WorkingMemory(agent_name="attention_agent", storage_dir="test_data")
    l1.clear()
    l1.add(
        "Background lint note",
        source="worker",
        metadata={"importance_score": 3, "topic": "lint", "unresolved": False},
    )
    l1.add(
        "Critical build failed on frontend main due to dependency mismatch",
        source="terminal",
        metadata={"importance_score": 9, "topic": "build", "unresolved": True},
    )

    focus = l1.get_focus_set(limit=1)
    assert focus[0]["source"] == "terminal"
    assert focus[0]["unresolved_status"] == 1.0


def test_l1_neuromodulation_increases_stress_weight_during_failure_streak():
    l1 = L1WorkingMemory(agent_name="attention_gain_agent", storage_dir="test_data")
    l1.clear()
    for index in range(4):
        l1.add(
            f"Critical deploy failed {index}",
            source="terminal",
            metadata={"importance_score": 9, "unresolved": True, "topic": "deploy"},
        )

    state = l1.get_attention_state()
    assert state["stress_level"] > 0.5
    assert state["global_gain"] > 1.0
    assert state["severity_weight"] > 0.15
    assert state["unresolved_weight"] > 0.20


def test_persona_layers_group_core_project_and_adaptive_rules():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    identity_file = Path(storage_dir) / "test_agent_layers_core_identity.json"
    if identity_file.exists():
        identity_file.unlink()

    persona = Persona(name="test_agent_layers", storage_dir=storage_dir)
    core_rule_id = persona.add_rule(
        "Security-first. Never bypass authentication checks.",
        scope="core",
        confidence=0.95,
        source="human-approved",
        layer="core",
    )
    project_rule_id = persona.add_rule(
        "Never force push to the frontend branch.",
        scope="project",
        confidence=0.82,
        evidence_episode_ids=["ep-project"],
    )
    adaptive_rule_id = persona.add_rule(
        "This workspace appears to dislike wildcard imports.",
        scope="agent",
        confidence=0.58,
        evidence_episode_ids=["ep-adaptive"],
    )
    persona.add_rule(
        "Never force push to the frontend branch.",
        scope="project",
        confidence=0.82,
        evidence_episode_ids=["ep-project-2"],
    )
    persona.add_rule(
        "This workspace appears to dislike wildcard imports.",
        scope="agent",
        confidence=0.58,
        evidence_episode_ids=["ep-adaptive-2"],
    )

    layers = persona.get_layer_records()

    assert layers["core"][0]["id"] == core_rule_id
    assert layers["project"][0]["id"] == project_rule_id
    assert layers["adaptive"][0]["id"] == adaptive_rule_id

    prompt = persona.get_identity_prompt(include_scopes=["core", "project", "agent"])
    assert "CORE RULES:" in prompt
    assert "PROJECT RULES:" in prompt
    assert "ADAPTIVE RULES:" in prompt


def test_approval_queue_for_sensitive_rules():
    queue = ApprovalQueue(agent_name="approval_agent", storage_dir="test_data")
    request = queue.submit(
        "promote_sensitive_rule",
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.92,
    )
    assert queue.requires_approval("Never use git push -f on the frontend branch.", scope="project", confidence=0.92)
    assert queue.pending_count >= 1
    resolved = queue.resolve(request["request_id"], "approved", reviewer="qa")
    assert resolved["status"] == "approved"


def test_human_approved_project_rule_becomes_stable():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    persona = Persona(name="human_approved_project_rule", storage_dir="test_data")
    rule_id = persona.add_rule(
        "Do not execute dependency updates or reinstalls unless vite and vite-plugin-react share the same major version number in package.json.",
        scope="project",
        confidence=0.99,
        source="human-approved",
        evidence_episode_ids=["ep-approved"],
    )

    rule = persona.get_rule_records()[rule_id]
    assert rule["state"] == "stable"
    assert rule["layer"] == "project"


def test_retrieval_service_builds_lineage_and_safety_guard():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    identity_file = Path(storage_dir) / "test_agent_lineage_core_identity.json"
    episode_file = Path(storage_dir) / "test_agent_lineage_episodes.json"
    graph_file = Path(storage_dir) / "test_agent_lineage_knowledge_graph.json"
    for file_path in (identity_file, episode_file, graph_file):
        if file_path.exists():
            file_path.unlink()

    persona = Persona(name="test_agent_lineage", storage_dir=storage_dir)
    episodes = EpisodeStore(agent_name="test_agent_lineage", storage_dir=storage_dir)
    l2 = L2SemanticMemory(agent_name="test_agent_lineage", storage_dir=storage_dir)
    graph = KnowledgeGraph(agent_name="test_agent_lineage", storage_dir=storage_dir)

    episode = episodes.add(
        raw_payload="Frontend deploy failed because force push bypassed review.",
        actor="agent",
        source="terminal",
        topic="git",
        confidence=0.95,
        workspace_id="frontend",
        project_version="v3.0.0",
    )
    rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    rule = persona.get_rule_records()[rule_id]
    graph.add_belief_rule(rule)
    graph.add_episode_evidence(rule_id, episode["episode_id"], confidence=0.9)
    override_rule_id = persona.add_rule(
        "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
        scope="project",
        confidence=0.88,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    persona.add_rule(
        "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
        scope="project",
        confidence=0.88,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    graph.add_belief_rule(persona.get_rule_records()[override_rule_id])
    graph.add_governed_exception(override_rule_id, rule_id)
    graph.add_approved_by_policy(
        override_rule_id,
        "Never use git push -f on the frontend branch.",
        scope="project",
        domain="git_hotfix",
    )
    graph.add_requires_human_approval(override_rule_id)
    graph.add_expires_override_at(override_rule_id, 1000.0, 2000.0)
    l2.store_exception(
        "Exception: emergency hotfix branches may force push only with explicit approval.",
        exception_for="git",
        workspace_id="frontend",
        project_version="v3.0.0",
        mode_hints=["deploy"],
    )

    service = RetrievalService(l2=l2, graph=graph, episodes=episodes, persona=persona)
    retrieval_state = service.build_retrieval_state(
        {"mode": "deploy", "risk_level": "high", "workspace_id": "frontend", "project_version": "v3.0.0"}
    )
    lineage = service.build_lineage("frontend force push deploy policy", retrieval_state, top_k=3)

    assert lineage["safety_guard"]
    assert lineage["beliefs"]
    assert lineage["episodes"]
    assert lineage["exceptions"]
    assert lineage["override_chains"]
    assert "Default rules:" in lineage["safety_guard"]
    assert "Approved overrides:" in lineage["safety_guard"]
    belief_bundle = graph.belief_query(rule_id=override_rule_id)
    assert belief_bundle["governed_exception_for"]
    assert belief_bundle["approved_by_policy"]
    assert belief_bundle["requires_human_approval"]
    assert belief_bundle["expires_override_at"]
    assert belief_bundle["approved_by_policy"][0]["target"].startswith("policy::")


def test_retrieval_service_surfaces_stable_rules_and_pending_approvals():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    persona = Persona(name="test_agent_pending_prompt", storage_dir=storage_dir)
    episodes = EpisodeStore(agent_name="test_agent_pending_prompt", storage_dir=storage_dir)
    l2 = L2SemanticMemory(agent_name="test_agent_pending_prompt", storage_dir=storage_dir)
    graph = KnowledgeGraph(agent_name="test_agent_pending_prompt", storage_dir=storage_dir)
    approvals = ApprovalQueue(agent_name="test_agent_pending_prompt", storage_dir=storage_dir)

    episode = episodes.add(
        raw_payload="Approved hotfix allows force push only with explicit approval and audit logging.",
        actor="agent",
        source="chat",
        topic="git",
        confidence=0.92,
        workspace_id="frontend",
        project_version="v6.1.0",
    )
    rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.86,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.86,
        evidence_episode_ids=[episode["episode_id"]],
        promotion_threshold=2,
    )
    graph.add_belief_rule(persona.get_rule_records()[rule_id])
    approvals.submit(
        "promote_sensitive_rule",
        "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging.",
        scope="project",
        confidence=0.95,
        metadata={"source": "hippocampus", "evidence_episode_ids": [episode["episode_id"]]},
    )

    service = RetrievalService(
        l2=l2,
        graph=graph,
        episodes=episodes,
        persona=persona,
        approval_queue=approvals,
    )
    retrieval_state = service.build_retrieval_state(
        {"mode": "deploy", "risk_level": "high", "workspace_id": "frontend", "project_version": "v6.1.0"}
    )
    bundle = service.hybrid_retrieve("What is the frontend hotfix force-push rule?", retrieval_state, top_k=5)

    assert bundle["stable_persona_rules"]
    assert any("Never use git push -f" in item["text"] for item in bundle["stable_persona_rules"])
    assert bundle["pending_approvals"]
    assert any("approved hotfix response" in item["rule_text"] for item in bundle["pending_approvals"])


def test_l1_stress_decay_drops_when_failures_are_old():
    l1 = L1WorkingMemory(agent_name="stress_decay_agent", storage_dir="test_data")
    l1.clear()
    old_timestamp = time.time() - (10 * 3600)
    for index in range(3):
        l1.add(
            f"Old critical failure {index}",
            source="terminal",
            metadata={"importance_score": 9, "unresolved": True, "topic": "deploy"},
        )
        l1._entries[-1]["timestamp"] = old_timestamp
    l1.save()
    decayed = l1.get_attention_state()
    assert decayed["stress_decay_factor"] < 1.0
    assert decayed["hours_since_last_failure"] >= 9.0


def test_retrieval_service_adds_fallback_action_for_challenged_beliefs():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    storage_dir = "test_data"
    persona = Persona(name="challenged_guard_agent", storage_dir=storage_dir)
    episodes = EpisodeStore(agent_name="challenged_guard_agent", storage_dir=storage_dir)
    l2 = L2SemanticMemory(agent_name="challenged_guard_agent", storage_dir=storage_dir)
    graph = KnowledgeGraph(agent_name="challenged_guard_agent", storage_dir=storage_dir)

    old_episode = episodes.add(
        raw_payload="Repo policy says always use pnpm.",
        actor="agent",
        source="terminal",
        topic="package-manager",
        confidence=0.8,
        workspace_id="frontend",
    )
    new_episode = episodes.add(
        raw_payload="This workspace uses yarn and explicitly rejects pnpm.",
        actor="agent",
        source="terminal",
        topic="package-manager",
        confidence=0.9,
        workspace_id="frontend",
    )

    old_rule_id = persona.add_rule(
        "Always use pnpm in this workspace.",
        scope="project",
        confidence=0.72,
        evidence_episode_ids=[old_episode["episode_id"]],
    )
    new_rule_id = persona.add_rule(
        "Never use pnpm in this workspace.",
        scope="project",
        confidence=0.90,
        evidence_episode_ids=[new_episode["episode_id"]],
    )

    resolver = ContradictionResolver(persona)
    reconcile_stats = resolver.reconcile(old_rule_id)
    assert reconcile_stats["challenged"] == 1

    for rule in persona.get_rule_records().values():
        graph.add_belief_rule(rule)
        for episode_id in rule.get("evidence_episode_ids", []):
            graph.add_episode_evidence(rule["id"], episode_id, confidence=rule["confidence"])

    service = RetrievalService(l2=l2, graph=graph, episodes=episodes, persona=persona)
    retrieval_state = service.build_retrieval_state({"mode": "implement", "workspace_id": "frontend"})
    lineage = service.build_lineage("package manager workspace policy", retrieval_state, top_k=3)

    assert "Fallback action:" in lineage["safety_guard"]
    assert "challenged beliefs" in lineage["safety_guard"].lower()


def test_hippocampus_normalizes_weird_scope_and_rejects_generic_rule_promotion():
    class DummyEngine:
        backend = "ollama"

    l1 = L1WorkingMemory(agent_name="scope_gate_agent", storage_dir="test_data")
    persona = Persona(name="scope_gate_agent", storage_dir="test_data")
    l2 = L2SemanticMemory(agent_name="scope_gate_agent", storage_dir="test_data")
    hippo = Hippocampus(engine=DummyEngine(), l1=l1, l2=l2, persona=persona)

    metadata = {"importance_score": 8, "workspace_id": "frontend", "project_version": "v1"}
    assert hippo._normalize_scope("team, frontend, deployment", metadata) == "project"
    assert not hippo._should_promote_rule(
        "Always prioritize system stability and quality.",
        scope="project",
        confidence=0.98,
        metadata=metadata,
    )
    assert hippo._should_promote_rule(
        "Never use git push -f on the frontend branch.",
        scope="project",
        confidence=0.85,
        metadata=metadata,
    )
    assert hippo._promotion_threshold(metadata, "Never use git push -f on the frontend branch in production.", "project") == 3


def test_hippocampus_canonicalizes_policy_and_hotfix_rules():
    class DummyEngine:
        backend = "ollama"

    hippo = Hippocampus(
        engine=DummyEngine(),
        l1=L1WorkingMemory(agent_name="canon_agent", storage_dir="test_data"),
        l2=L2SemanticMemory(agent_name="canon_agent", storage_dir="test_data"),
        persona=Persona(name="canon_agent", storage_dir="test_data"),
    )
    metadata = {"workspace_id": "frontend", "importance_score": 9}
    deny_rule = hippo._canonicalize_identity_rule(
        {"identity_rule": "Team policy forbids force pushing."},
        "team policy says never use git push -f on the frontend branch in production",
        metadata,
    )
    allow_rule = hippo._canonicalize_identity_rule(
        {"identity_rule": "Approved hotfixes may allow force push."},
        "approved hotfix runbook says allow force push on hotfix branches only with explicit approval and audit logging",
        metadata,
    )
    tenant_rule = hippo._canonicalize_identity_rule(
        {"identity_rule": "Tenant A can rename customer codes with signoff."},
        "for tenant A, approved code rename override requires finance signoff and audit logging",
        metadata,
    )
    assert deny_rule == "Never use git push -f on the frontend branch in production."
    assert allow_rule == "Allow use git push on the frontend branch during approved hotfix response with explicit approval and audit logging."
    assert tenant_rule == "Allow customer code rename for Tenant A only with finance approval and audit logging."


def test_persona_reaches_stable_after_threshold_repeats():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    persona = Persona(name="threshold_agent", storage_dir="test_data")
    rule_id = persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=["ep1"],
        promotion_threshold=3,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=["ep2"],
        promotion_threshold=3,
    )
    persona.add_rule(
        "Never use git push -f on the frontend branch in production.",
        scope="project",
        confidence=0.85,
        evidence_episode_ids=["ep3"],
        promotion_threshold=3,
    )
    assert persona.get_rule_records()[rule_id]["state"] == "stable"


def test_structured_fallback_extracts_json_from_fenced_local_output():
    class DummyStructuredEngine(LLMEngine):
        def __init__(self):
            super().__init__(backend="ollama", model_id="dummy", temperature=0.1)

        async def generate(self, prompt: str, temperature=None, effort=None) -> str:
            return """```json
            {
              "episodic_summary": "Build failed after dependency drift.",
              "semantic_memory": "Dependency drift breaks builds.",
              "procedural_memory": "Check versions before reinstalling dependencies.",
              "exception_memory": "Peer dependency mismatch can appear after tool upgrades.",
              "identity_rule": "Always verify dependency compatibility before upgrades.",
              "confidence": 0.82,
              "scope": "project"
            }
            ```"""

    async def run():
        engine = DummyStructuredEngine()
        payload = await engine.generate_structured("compile this", schema=CompiledMemory, temperature=0.1)
        assert payload["scope"] == "project"
        assert payload["confidence"] == 0.82

    asyncio.run(run())


def test_openclaw_plugin_factory_and_target():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    plugin = build_openclaw_plugin(agent_name="plugin-agent", storage_dir="test_data")
    assert isinstance(plugin, OpenClawMemoryPlugin)
    assert plugin.config_target() == "bio_agent_os.plugins.openclaw:build_openclaw_plugin"
    status = plugin.status()
    assert status["plugin"] == "openclaw"
    assert status["target"] == "bio_agent_os.plugins.openclaw:build_openclaw_plugin"


def test_swe_agent_plugin_factory_and_target():
    os.environ["BIO_AGENT_SECRET_KEY"] = "locaith_secret_key_testing_12345"
    plugin = build_swe_agent_plugin(agent_name="plugin-agent", storage_dir="test_data")
    assert isinstance(plugin, SWEAgentMemoryPlugin)
    assert plugin.config_target() == "bio_agent_os.plugins.swe_agent:build_swe_agent_plugin"
    status = plugin.status()
    assert status["plugin"] == "swe-agent"
    assert status["target"] == "bio_agent_os.plugins.swe_agent:build_swe_agent_plugin"
