import os
from pathlib import Path

from bio_agent_os import (
    ContradictionResolver,
    EpisodeStore,
    L1WorkingMemory,
    L2SemanticMemory,
    MemoryHealthMonitor,
    Persona,
)
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph


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


def test_l2_semantic_memory():
    l2 = L2SemanticMemory(agent_name="test_agent", storage_dir="test_data")
    l2.store("Always use generic exception handling", importance=8.0, tags=["coding"])

    results = l2.search("exception handling", top_k=1)
    assert len(results) > 0
    assert results[0]["importance"] == 8.0


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
