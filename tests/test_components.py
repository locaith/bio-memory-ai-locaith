import os
from pathlib import Path

from bio_agent_os import ContradictionResolver, EpisodeStore, L1WorkingMemory, L2SemanticMemory, Persona


def test_l1_memory():
    l1 = L1WorkingMemory(agent_name="test_agent", storage_dir="test_data")
    l1.clear()
    l1.add(
        "Login endpoint failing",
        metadata={"importance_score": 8, "is_junk_or_transient": False},
    )

    assert l1.count == 1
    assert "Login endpoint" in l1.build_context_string()


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
    )
    assert record["episode_id"]
    assert episodes.get(record["episode_id"]) is not None


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
