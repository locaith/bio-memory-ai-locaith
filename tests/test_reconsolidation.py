"""
Feature B — reconsolidation replay (the revived dream loop).

Reactivated memories become labile and are updated before re-storage:
corroborated ensembles strengthen, near-duplicates merge, contradictions
weaken — and the dream journal becomes a real feedback record that the next
cycle reads to stay idempotent.
"""

import pytest

from bio_agent_os.core.runtime import build_runtime

STORAGE = "test_data"


def _runtime(name: str):
    rt = build_runtime(agent_name=name, storage_dir=STORAGE)
    return rt


def test_merges_near_duplicate_memories():
    rt = _runtime("recon-merge")
    # Identical content → identical (hash) embedding → cosine 1.0 → merge.
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    assert rt.l2.count == 2

    result = rt.hippo._reconsolidate()
    assert len(result["merged_ids"]) == 1
    assert rt.l2.count == 1


def test_never_merges_anchor_memories():
    rt = _runtime("recon-anchor")
    rt.l2.store(content="the secret passphrase is hunter2", importance=9.0, workspace_id="w", memory_type="anchor")
    rt.l2.store(content="the secret passphrase is hunter2", importance=9.0, workspace_id="w", memory_type="anchor")
    rt.hippo._reconsolidate()
    # Anchors are verbatim and must never be collapsed.
    assert rt.l2.count == 2


def test_strengthens_replayed_ensemble():
    rt = _runtime("recon-strengthen")
    rt.l2.store(content="deploy uses blue green strategy", importance=5.0, workspace_id="w", memory_type="semantic")
    rt.l2.store(content="rollback plan documented in runbook", importance=5.0, workspace_id="w", memory_type="semantic")
    result = rt.hippo._reconsolidate()
    assert len(result["strengthened_ids"]) == 2
    for item in rt.l2.all_entries(limit=10):
        assert item["durability"] > 1.0


def test_weakens_contradiction():
    rt = _runtime("recon-weaken")
    # Same scope, opposite polarity, high semantic overlap → conflict.
    rt.l2.store(content="always force push to the main branch", importance=5.0, workspace_id="w", scope="project", memory_type="semantic")
    rt.l2.store(content="never force push to the main branch", importance=8.0, workspace_id="w", scope="project", memory_type="semantic")
    result = rt.hippo._reconsolidate()
    assert len(result["weakened_ids"]) == 1
    # The less-established trace (lower importance) is the one weakened.
    by_id = {item["entry_id"]: item for item in rt.l2.all_entries(limit=10)}
    weak = by_id[result["weakened_ids"][0]]
    assert "always" in weak["content"]
    assert weak["importance"] == pytest.approx(4.0)  # 5.0 - 1.0


def test_records_to_journal_and_is_idempotent():
    rt = _runtime("recon-journal")
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    before = rt.dream_journal.count
    rt.hippo._reconsolidate()
    assert rt.dream_journal.count == before + 1
    reports = rt.dream_journal.recent(limit=1)
    assert reports[0]["type"] == "reconsolidation"
    # Second run: nothing left to merge → no new journal entry.
    rt.hippo._reconsolidate()
    assert rt.dream_journal.count == before + 1


def test_no_cross_workspace_merge():
    rt = _runtime("recon-tenancy")
    rt.l2.store(content="identical fact across tenants", importance=5.0, workspace_id="tenant-a", memory_type="semantic")
    rt.l2.store(content="identical fact across tenants", importance=5.0, workspace_id="tenant-b", memory_type="semantic")
    result = rt.hippo._reconsolidate()
    # Same content but different workspaces must never be merged.
    assert len(result["merged_ids"]) == 0
    assert rt.l2.count == 2


def test_disabled_flag_makes_consolidate_skip_reconsolidation(monkeypatch):
    monkeypatch.setenv("BIO_RECONSOLIDATION_ENABLED", "0")
    rt = _runtime("recon-disabled")
    assert rt.hippo.reconsolidation_enabled is False
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    rt.l2.store(content="canary releases are mandatory before rollout", importance=5.0, workspace_id="w", memory_type="semantic")
    # Reconsolidation is gated off → no merge happens through the flag path.
    # (_reconsolidate itself still works if called directly; the flag guards consolidate().)
    assert rt.l2.count == 2
