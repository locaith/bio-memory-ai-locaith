"""
L2 durability: long-term memory must survive process restarts even when
the vector index is in-memory (no external Qdrant configured).
"""

from bio_agent_os.memory.l2_semantic import L2SemanticMemory

STORAGE = "test_data"


def test_l2_memory_survives_restart():
    l2 = L2SemanticMemory(agent_name="persist-test", storage_dir=STORAGE)
    l2.store(
        content="The deploy passphrase is XK-77.",
        importance=8.0,
        workspace_id="ws-p",
    )
    assert l2.count == 1

    # A new instance simulates a process restart: the in-memory index is
    # rebuilt from the durable SQLite rows.
    reloaded = L2SemanticMemory(agent_name="persist-test", storage_dir=STORAGE)
    assert reloaded.count == 1
    results = reloaded.search("deploy passphrase", top_k=3)
    assert any("XK-77" in item["content"] for item in results)
    assert results[0]["workspace_id"] == "ws-p"


def test_l2_prune_removes_durable_rows():
    l2 = L2SemanticMemory(agent_name="prune-test", storage_dir=STORAGE)
    l2.store(content="Ephemeral note that should decay away.", importance=0.001)
    assert l2.count == 1
    removed = l2.prune_decayed(threshold=1.0)
    assert removed == 1

    reloaded = L2SemanticMemory(agent_name="prune-test", storage_dir=STORAGE)
    assert reloaded.count == 0
