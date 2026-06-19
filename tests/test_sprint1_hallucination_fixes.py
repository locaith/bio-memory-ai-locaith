"""
Sprint 1 — no-silent-hallucination fixes beyond the embedder.

Covers three holes audited as "fail-silent → hallucination":
  #6  L1 encode/remove must key on the stable entry_id (UUID), not the float
      timestamp (two same-millisecond events collide → silent data loss).
  #2  Anchor extraction must not store a negated/implausible value as a trusted
      verbatim "secret", and a bare "remember exactly" marker with no concrete
      value must not fabricate a verbatim anchor.
  #3/#4 Reconsolidation must not MERGE two high-cosine but contradictory traces,
      and an errored conflict check must be loud (logged) — never silently
      assumed "no conflict".
"""

import pytest

from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.core.runtime import build_runtime
from bio_agent_os.memory.l1_working import L1WorkingMemory

STORAGE = "test_data"


# ── #6 entry_id, not timestamp ───────────────────────────────────────────────

def test_l1_uses_entry_id_not_timestamp_for_encode_and_remove():
    l1 = L1WorkingMemory(agent_name="uuid-collision", storage_dir=STORAGE)
    e1 = l1.add("first survivor event", source="t")
    e2 = l1.add("second survivor event", source="t")
    assert e1["entry_id"] != e2["entry_id"]

    # Force a timestamp collision (two events in the same millisecond).
    l1.load()
    for entry in l1._entries:
        entry["timestamp"] = 1000.0
    l1.save()

    l1.mark_encoded(e1["entry_id"])
    states = {e["entry_id"]: e["status"] for e in l1.get_all()}
    assert states[e1["entry_id"]] == "encoded"
    # The colliding sibling must NOT be flipped despite the identical timestamp.
    assert states[e2["entry_id"]] == "raw"

    l1.remove_by_ids([e1["entry_id"]])
    remaining = {e["entry_id"] for e in l1.get_all()}
    assert e1["entry_id"] not in remaining
    assert e2["entry_id"] in remaining


# ── #2 anchor validation ─────────────────────────────────────────────────────

def test_anchor_rejects_negated_value():
    h = Hippocampus.__new__(Hippocampus)
    out = h._extract_anchor_fact(
        "Please remember exactly: the password for the vault is NOT 123456"
    )
    # A negated phrase is almost never the real secret → must not be captured.
    assert out == {}


def test_anchor_accepts_clean_value():
    h = Hippocampus.__new__(Hippocampus)
    out = h._extract_anchor_fact("the password for the vault is sunflower42")
    assert out.get("anchor_kind") == "verification_code"
    assert out.get("anchor_value") == "sunflower42"


def test_anchor_marker_only_does_not_fabricate_verbatim():
    h = Hippocampus.__new__(Hippocampus)
    md = h._apply_anchor_overrides(
        "ghi nhớ chính xác điều này nhé bạn ơi cho tôi vài thứ",
        {"importance_score": 5},
        "chat_input",
    )
    # Marker present but no extractable verbatim value → no fabricated anchor.
    assert md.get("retain_verbatim") is not True
    assert md.get("is_anchor_memory") is not True
    assert md["importance_score"] >= 7  # importance still raised modestly


# ── #3/#4 reconsolidation guards ─────────────────────────────────────────────

def _runtime(name: str):
    return build_runtime(agent_name=name, storage_dir=STORAGE)


def test_high_similarity_contradiction_is_not_merged(monkeypatch):
    rt = _runtime("recon-conflict-guard")
    # Make the two traces look like near-duplicates regardless of embedding
    # backend, so only the conflict guard decides merge-vs-weaken.
    monkeypatch.setattr(rt.l2, "_cosine_similarity", lambda a, b: 0.99)
    rt.l2.store(content="always force push to the main branch", importance=5.0,
                workspace_id="w", scope="project", memory_type="semantic")
    rt.l2.store(content="never force push to the main branch", importance=8.0,
                workspace_id="w", scope="project", memory_type="semantic")

    result = rt.hippo._reconsolidate()

    # High cosine BUT contradictory → must be weakened, never merged.
    assert len(result["merged_ids"]) == 0
    assert rt.l2.count == 2
    assert len(result["weakened_ids"]) == 1


def test_conflict_check_error_blocks_merge_and_is_logged(monkeypatch):
    rt = _runtime("recon-conflict-error")
    monkeypatch.setattr(rt.l2, "_cosine_similarity", lambda a, b: 0.99)

    def boom(*args, **kwargs):
        raise RuntimeError("nli down")

    monkeypatch.setattr(rt.hippo.reconciler, "_is_conflict", boom)
    rt.l2.store(content="alpha beta gamma one two", importance=5.0,
                workspace_id="w", scope="project", memory_type="semantic")
    rt.l2.store(content="alpha beta gamma three four", importance=5.0,
                workspace_id="w", scope="project", memory_type="semantic")

    result = rt.hippo._reconsolidate()

    # Relation unknown (checker raised) → do nothing, but say so (not silent).
    assert len(result["merged_ids"]) == 0
    assert len(result["weakened_ids"]) == 0
    assert rt.l2.count == 2
    assert any("conflict-check failed" in line for line in rt.hippo.logs)
