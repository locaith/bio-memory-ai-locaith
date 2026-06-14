"""
Feature A — retrieval-aware forgetting (synaptic tag-and-capture).

The central guarantee: a memory that is never reinforced behaves byte-for-byte
like the legacy time-only decay, while a memory that repeatedly *helps* becomes
durable and survives pruning that an identical un-helped memory fails.
"""

import math

from bio_agent_os.memory.l2_semantic import L2SemanticMemory

STORAGE = "test_data"


def _fresh(name: str) -> L2SemanticMemory:
    return L2SemanticMemory(agent_name=name, storage_dir=STORAGE)


def test_new_entries_are_born_with_neutral_durability():
    l2 = _fresh("raf-born")
    l2.store(content="Caroline went to the support group on 7 May.", importance=5.0, workspace_id="w")
    results = l2.search("support group", top_k=3, retrieval_state={"allowed_workspaces": ["w"]})
    assert results
    assert results[0]["entry_id"]
    assert results[0]["durability"] == 1.0


def test_never_reinforced_decay_is_identical_to_legacy():
    l2 = _fresh("raf-identity")
    payload = l2._build_payload(
        content="x", importance=5.0, tags=None, source_rule_id=None,
        memory_type="semantic", scope="project", mode_hints=None,
        risk_level="medium", stress_state="normal", exception_for=None,
        task_id=None, workspace_id="w", project_version=None,
    )
    # durability == 1.0 → effective lambda == per-type lambda, exactly.
    assert l2._effective_lambda(payload) == l2._memory_decay_lambda(payload)
    for days in (0.0, 3.0, 30.0):
        legacy = math.exp(-l2._memory_decay_lambda(payload) * days)
        assert l2._effective_decay(payload, days) == legacy


def test_helped_access_raises_durability_returned_only_does_not():
    l2 = _fresh("raf-gate")
    l2.store(content="deploy uses blue-green strategy", importance=5.0, workspace_id="w")
    eid = l2.search("deploy", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["entry_id"]

    # Returned but never helped: success gate keeps durability at 1.0.
    l2.note_access([eid], helped_ids=[])
    l2.apply_access_consolidation()
    assert l2.search("deploy", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["durability"] == 1.0

    # Helped three times: durability rises (diminishing returns), capped.
    for _ in range(3):
        l2.note_access([eid], helped_ids=[eid])
    l2.apply_access_consolidation()
    dur = l2.search("deploy", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["durability"]
    assert 1.0 < dur <= l2.DUR_MAX
    assert dur == min(l2.DUR_MAX, 1.0 + l2.DUR_GROWTH * math.log1p(3))


def test_durability_persists_across_restart():
    l2 = _fresh("raf-restart")
    l2.store(content="the canary release is mandatory", importance=5.0, workspace_id="w")
    eid = l2.search("canary", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["entry_id"]
    for _ in range(5):
        l2.note_access([eid], helped_ids=[eid])
    l2.apply_access_consolidation()
    dur_before = l2.search("canary", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["durability"]
    assert dur_before > 1.0

    reloaded = _fresh("raf-restart")
    dur_after = reloaded.search("canary", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["durability"]
    assert dur_after == dur_before


def test_reinforced_memory_survives_prune_that_unhelped_one_fails():
    import time

    l2 = _fresh("raf-prune")
    # Two identical-importance semantic memories, aged so that an un-reinforced
    # entry falls below the prune threshold but a durable one survives.
    l2.store(content="alpha fact about onboarding", importance=4.0, workspace_id="w")
    l2.store(content="beta fact about onboarding", importance=4.0, workspace_id="w")
    results = l2.search("onboarding", top_k=5, retrieval_state={"allowed_workspaces": ["w"]})
    alpha = next(r for r in results if "alpha" in r["content"])

    # Age both memories past the decay horizon by backdating timestamps
    # (backend-agnostic: works for both the Qdrant and in-memory paths).
    old = time.time() - 60 * 86400
    for item in l2.all_entries(limit=100):
        payload, vector = l2._load_payload(item["entry_id"])
        payload["timestamp"] = old
        l2._save_payload(payload, vector)

    # Reinforce only alpha, many times, so its durability is high.
    for _ in range(40):
        l2.note_access([alpha["entry_id"]], helped_ids=[alpha["entry_id"]])
    l2.apply_access_consolidation()

    removed = l2.prune_decayed(threshold=1.0)
    survivors = {item["content"] for item in l2.all_entries(limit=100)}
    assert removed == 1
    assert any("alpha" in c for c in survivors)
    assert not any("beta" in c for c in survivors)


def test_dur_max_one_reverts_feature_to_noop(monkeypatch):
    monkeypatch.setattr(L2SemanticMemory, "DUR_MAX", 1.0)
    l2 = _fresh("raf-revert")
    l2.store(content="reversible behavior check", importance=5.0, workspace_id="w")
    eid = l2.search("reversible", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]["entry_id"]
    for _ in range(10):
        l2.note_access([eid], helped_ids=[eid])
    l2.apply_access_consolidation()
    # access_count still accrues, but durability is clamped to 1.0 → decay
    # identical to legacy.
    result = l2.search("reversible", top_k=1, retrieval_state={"allowed_workspaces": ["w"]})[0]
    assert result["durability"] == 1.0
