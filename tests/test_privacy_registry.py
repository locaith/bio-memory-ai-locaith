"""The privacy perimeter is a closed world, not a list.

`rejected_inputs.payload_json` was found by an independent review sitting
outside the deletion verifier. Adding it to a list fixes that one table and
leaves the next to be found the same way — by somebody reading carefully at
the right moment.

So the rule is subtraction:

    every table in the database − every table declared = ∅

and the mutant below proves it works: a table created without a declaration
fails the suite. Without that test the registry is documentation.

Enumerating a live database turned up five stores holding content outside the
verifier, not one. `cognitive_memory_fts` is the one worth naming — a
searchable copy of every sentence, which no audit had mentioned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import privacy_registry as registry
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "perimeter.db")
    # Exercise the paths that create tables lazily, so the enumeration sees
    # the real world rather than the subset a bare open creates.
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content="Phạm Vy sống ở Hà Nội.",
                              workspace_id="w1")
    memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                       content="Phạm Vy sống ở Hà Nội.", confidence=0.9)
    memory_os.attach_runtime()
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    forget_scoped(memory_os, "Hãy quên nơi sống của Phạm Vy.", actor="t")
    yield memory_os
    memory_os.close()


def test_every_persistent_store_is_declared(os_):
    """The subtraction. Empty is the only acceptable answer."""
    missing = registry.undeclared(os_.memories.conn)
    assert not missing, (
        f"{len(missing)} kho không khai báo ngữ nghĩa riêng tư: "
        f"{sorted(missing)}. Thêm một dòng vào privacy_registry.REGISTRY, "
        f"kèm chính sách cho forget/erase/replay và cách kiểm chứng.")


def test_an_undeclared_store_fails_the_suite(os_):
    """The mutant. Without this the registry is documentation.

    A developer adding a table in six months breaks CI on the commit that
    adds it, rather than on the day somebody audits.
    """
    os_.memories.conn.execute(
        "CREATE TABLE user_notes_v2 (id TEXT PRIMARY KEY, content TEXT)")
    os_.memories.conn.commit()
    assert "user_notes_v2" in registry.undeclared(os_.memories.conn)


def test_a_table_that_merely_looks_like_an_fts_shadow_is_not_exempt(os_):
    """The exemption is for shadows of a *declared* index, not for any name
    ending in `_data`."""
    os_.memories.conn.execute(
        "CREATE TABLE customer_exports_data (id TEXT, payload_json TEXT)")
    os_.memories.conn.commit()
    assert registry.is_fts_shadow("cognitive_memory_fts_data")
    assert not registry.is_fts_shadow("customer_exports_data")
    assert "customer_exports_data" in registry.undeclared(os_.memories.conn)


def test_the_full_text_index_is_declared(os_):
    """Found by enumeration, not by review: a searchable copy of every
    sentence that no audit had mentioned."""
    policy = registry.declared("cognitive_memory_fts")
    assert policy is not None
    assert policy.contains_derived_payload
    assert policy.forget_derived_policy == registry.CASCADE


def test_every_declaration_answers_all_three_deletion_levels():
    """A store whose policy is silent on one level is a store nobody has
    decided about."""
    allowed = {registry.CASCADE, registry.INVALIDATE, registry.REDACT,
               registry.KEEP, registry.NOT_APPLICABLE}
    for policy in registry.REGISTRY.values():
        assert policy.forget_derived_policy in allowed, policy.store_name
        assert policy.erase_history_policy in allowed, policy.store_name
        assert policy.replay_policy, policy.store_name
        assert policy.verification_strategy, policy.store_name
        assert policy.serving_or_history in {"serving", "history",
                                             "operational"}, policy.store_name


def test_a_store_holding_content_and_keeping_it_must_say_why():
    """"Kept deliberately" and "kept accidentally" must not look alike."""
    for policy in registry.REGISTRY.values():
        holds_content = (policy.contains_raw_payload
                         or policy.contains_derived_payload)
        if holds_content and policy.forget_derived_policy == registry.KEEP:
            assert policy.note, policy.store_name


def test_no_declared_store_holds_content_outside_the_verifier():
    """Empty since 17/08, when the last two were closed.

    `rejected_inputs` and `agent_checkpoints` sat here declared and described
    while every deletion verified itself against a list that omitted them —
    measured on a real MemoryOS, `erase_history` returned verified_clean=True
    with `api_key: sk-live-…` still in `rejected_inputs.content`. Both are now
    scanned and both are rewritten by `erase_history`; see
    `test_quarantine_erasure.py`.
    """
    outside = registry.unscanned_content_stores()
    assert outside == [], (
        f"{[p.store_name for p in outside]} giữ nội dung mà không verifier nào "
        f"đọc. Hoặc đưa vào CONTENT_COLUMNS, hoặc nói rõ vì sao giữ.")


def test_the_unscanned_list_still_detects_one():
    """The list being empty must mean nothing is outside, not that the check
    stopped working. A policy that declares content and no verifier is caught.
    """
    mutant = registry.StorePolicy(
        store_name="exports_v2", contains_raw_payload=True,
        contains_derived_payload=False, contains_identity_data=True,
        contains_secret_possible=True, serving_or_history="operational",
        forget_derived_policy=registry.KEEP, erase_history_policy=registry.KEEP,
        replay_policy="never", verification_strategy="NOT scanned")
    patched = dict(registry.REGISTRY)
    patched[mutant.store_name] = mutant
    original, registry.REGISTRY = registry.REGISTRY, patched
    try:
        assert [p.store_name for p in registry.unscanned_content_stores()] == \
            ["exports_v2"]
    finally:
        registry.REGISTRY = original


def test_the_registry_matches_what_the_deletion_verifier_scans():
    """Two lists that must agree, checked rather than assumed."""
    from bio_agent_os.cognitive.forgetting import CONTENT_COLUMNS

    scanned = {table for table, _ in CONTENT_COLUMNS}
    for table in scanned:
        assert registry.declared(table) is not None, (
            f"{table} được verifier quét nhưng không có trong registry")
