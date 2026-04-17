from pathlib import Path

import pytest

from bio_agent_os import EpisodeStore, GarbageCollector, Hippocampus, L1WorkingMemory, L2SemanticMemory, Persona
from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph


class FakeEngine:
    backend = "test"
    model_id = "fake-memory-compiler"

    async def generate(self, prompt, temperature=0.2, effort=None):
        return f"reflection-effort:{effort or 'none'}"

    async def generate_structured(self, prompt, schema, temperature=0.1, effort=None):
        schema_name = schema.__name__
        if schema_name == "MemoryLabel":
            return {
                "topic": "git",
                "importance_score": 8,
                "is_junk_or_transient": False,
                "user_state": "focused",
            }
        if schema_name == "CompiledMemory":
            return {
                "episodic_summary": "OpenClaw saw a risky git action.",
                "semantic_memory": "Force pushing on protected branches is risky.",
                "procedural_memory": "Check the branch policy before pushing.",
                "exception_memory": "Exception: emergency hotfix branches may allow force push only with explicit approval.",
                "identity_rule": "Never use git push -f on the frontend branch.",
                "confidence": 0.82,
                "scope": "project",
            }
        raise ValueError(f"Unsupported schema: {schema_name}")


@pytest.mark.asyncio
async def test_openclaw_benchmark_mini():
    for file_path in (
        Path("test_data/benchmark-agent_core_identity.json"),
        Path("test_data/benchmark-agent_episodes.json"),
        Path("test_data/benchmark-agent_l1_memory.json"),
    ):
        if file_path.exists():
            file_path.unlink()

    engine = FakeEngine()
    l1 = L1WorkingMemory(agent_name="benchmark-agent", storage_dir="test_data")
    l1.clear()
    persona = Persona(name="benchmark-agent", storage_dir="test_data")
    episodes = EpisodeStore(agent_name="benchmark-agent", storage_dir="test_data")
    hippo = Hippocampus(engine=engine, l1=l1, persona=persona, episodes=episodes)
    gc = GarbageCollector(l1=l1)
    adapter = OpenClawBioAdapter(hippocampus=hippo, garbage_collector=gc, persona=persona)

    for _ in range(10):
        await adapter.ingest_observation("run_command", "git push -f origin main")

    await adapter.trigger_micro_sleep()
    await adapter.trigger_micro_sleep()

    rules = persona.get_rule_records()
    assert rules
    assert any("git push -f" in rule["text"] for rule in rules.values())


@pytest.mark.asyncio
async def test_openclaw_long_session_benchmark():
    for file_path in (
        Path("test_data/long-session-agent_core_identity.json"),
        Path("test_data/long-session-agent_episodes.json"),
        Path("test_data/long-session-agent_l1_memory.json"),
    ):
        if file_path.exists():
            file_path.unlink()

    engine = FakeEngine()
    l1 = L1WorkingMemory(agent_name="long-session-agent", storage_dir="test_data")
    l1.clear()
    persona = Persona(name="long-session-agent", storage_dir="test_data")
    episodes = EpisodeStore(agent_name="long-session-agent", storage_dir="test_data")
    hippo = Hippocampus(engine=engine, l1=l1, persona=persona, episodes=episodes)
    gc = GarbageCollector(l1=l1)
    adapter = OpenClawBioAdapter(hippocampus=hippo, garbage_collector=gc, persona=persona)

    for turn in range(30):
        await adapter.ingest_observation("run_command", f"git push -f origin main attempt {turn}")

    await adapter.trigger_micro_sleep()
    await adapter.trigger_micro_sleep()
    await adapter.trigger_micro_sleep()

    rules = persona.get_rule_records()
    assert len(rules) >= 1
    strongest = max(rules.values(), key=lambda rule: rule["support_count"])
    assert strongest["support_count"] >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "query", "expected_memory_type"),
    [
        ("debug", "build failed after dependency change", "exception"),
        ("implement", "how should I change the feature safely", "procedural"),
        ("refactor", "what architecture rule matters during cleanup", "semantic"),
        ("deploy", "what should I avoid during release", "exception"),
    ],
)
async def test_openclaw_stateful_retrieval_modes(mode, query, expected_memory_type):
    for file_path in (
        Path("test_data/mode-benchmark-agent_core_identity.json"),
        Path("test_data/mode-benchmark-agent_episodes.json"),
        Path("test_data/mode-benchmark-agent_l1_memory.json"),
    ):
        if file_path.exists():
            file_path.unlink()

    engine = FakeEngine()
    l1 = L1WorkingMemory(agent_name="mode-benchmark-agent", storage_dir="test_data")
    l1.clear()
    l2 = L2SemanticMemory(agent_name="mode-benchmark-agent", storage_dir="test_data")
    persona = Persona(name="mode-benchmark-agent", storage_dir="test_data")
    episodes = EpisodeStore(agent_name="mode-benchmark-agent", storage_dir="test_data")
    hippo = Hippocampus(engine=engine, l1=l1, l2=l2, persona=persona, episodes=episodes)

    observations = {
        "debug": "build failed with dependency mismatch and urgent production issue",
        "implement": "implement a safer branch policy workflow for contributors",
        "refactor": "refactor the deployment helpers while preserving dependency rules",
        "deploy": "deploy release candidate and avoid risky branch operations in production",
    }
    await hippo.label_and_store(
        observations[mode],
        source="openclaw",
        task_id=f"task-{mode}",
        workspace_id="workspace-a",
        project_version="v2.2.0",
        source_refs=[{"kind": "terminal", "ref": mode}],
    )
    l1.increment_nights()
    l1.increment_nights()
    l1.increment_nights()
    await hippo.consolidate()

    results = hippo.l2.search(
        query,
        top_k=3,
        retrieval_state={
            "mode": mode,
            "stress_state": "failure" if mode in {"debug", "deploy"} else "normal",
            "risk_level": "high" if mode in {"debug", "deploy"} else "medium",
            "task_id": f"task-{mode}",
            "workspace_id": "workspace-a",
            "project_version": "v2.2.0",
            "prefer_exception": mode in {"debug", "deploy"},
        },
    )

    assert results
    assert results[0]["memory_type"] == expected_memory_type


@pytest.mark.asyncio
async def test_openclaw_long_task_chain_benchmark():
    for file_path in (
        Path("test_data/chain-benchmark-agent_core_identity.json"),
        Path("test_data/chain-benchmark-agent_episodes.json"),
        Path("test_data/chain-benchmark-agent_l1_memory.json"),
        Path("test_data/chain-benchmark-agent_knowledge_graph.json"),
    ):
        if file_path.exists():
            file_path.unlink()

    engine = FakeEngine()
    l1 = L1WorkingMemory(agent_name="chain-benchmark-agent", storage_dir="test_data")
    l1.clear()
    l2 = L2SemanticMemory(agent_name="chain-benchmark-agent", storage_dir="test_data")
    persona = Persona(name="chain-benchmark-agent", storage_dir="test_data")
    episodes = EpisodeStore(agent_name="chain-benchmark-agent", storage_dir="test_data")
    graph = KnowledgeGraph(agent_name="chain-benchmark-agent", storage_dir="test_data")
    hippo = Hippocampus(engine=engine, l1=l1, l2=l2, persona=persona, episodes=episodes, graph=graph)
    gc = GarbageCollector(l1=l1)
    adapter = OpenClawBioAdapter(hippocampus=hippo, garbage_collector=gc, persona=persona)

    chain = [
        ("debug", "build failed with dependency mismatch after emergency patch"),
        ("implement", "implement a safer branch policy for contributors"),
        ("refactor", "refactor deployment helpers without breaking branch protections"),
        ("deploy", "deploy release candidate and avoid risky branch operations in production"),
    ]

    for mode, observation in chain:
        await hippo.label_and_store(
            observation,
            source="openclaw",
            task_id=f"chain-{mode}",
            workspace_id="workspace-chain",
            project_version="v2.3.0",
            source_refs=[{"kind": "terminal", "ref": mode}],
        )
        await adapter.trigger_micro_sleep()

    l1.increment_nights()
    l1.increment_nights()
    l1.increment_nights()
    await hippo.consolidate()

    l2_results = l2.search(
        "release branch safety and force push risk",
        top_k=5,
        retrieval_state={
            "mode": "deploy",
            "stress_state": "failure",
            "risk_level": "high",
            "task_id": "chain-deploy",
            "workspace_id": "workspace-chain",
            "project_version": "v2.3.0",
            "prefer_exception": True,
        },
    )
    graph_results = graph.retrieve_beliefs(
        "force push policy on frontend branch",
        top_k=3,
        retrieval_state={"preferred_scope": "project"},
    )

    assert l2_results
    assert any(item["memory_type"] == "exception" for item in l2_results[:2])
    assert graph_results
    assert "git push -f" in graph_results[0]["text"]
