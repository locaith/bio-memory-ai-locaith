from pathlib import Path

import pytest

from bio_agent_os import EpisodeStore, GarbageCollector, Hippocampus, L1WorkingMemory, Persona
from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter


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
