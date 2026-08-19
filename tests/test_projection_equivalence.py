"""Luật equivalence phải tự chứng minh nó có răng — HBF-1.1.

    Tên của proof phải YẾU HƠN HOẶC BẰNG thứ measurement thực sự chứng minh.
"""
from __future__ import annotations

import json

from bio_agent_os.cognitive.projection_equivalence import (EVENT_DERIVED,
                                                           compare)

EVENT = {"source": "claude-code:UserPromptSubmit",
         "payload_json": json.dumps({"content": "hook=UserPromptSubmit | prompt=x"}),
         "metadata_json": json.dumps({"hook": "UserPromptSubmit",
                                      "session_id": "s", "tool": None}),
         "tenant_id": "t1", "workspace_id": "w1", "trust_tier": 2,
         "security_label": 1, "valid_from": None, "valid_to": None,
         "observed_at": "2026-08-19T00:00:00+00:00", "modality": "text"}

MEMORY = {"content": "hook=UserPromptSubmit | prompt=x",
          "tenant_id": "t1", "workspace_id": "w1", "trust_tier": 2,
          "security_label": 1, "valid_from": None, "valid_to": None,
          "observed_at": "2026-08-19T00:00:00+00:00", "modality": "text",
          "confidence": 0.72, "importance": 0.55, "salience": 0.50,
          "utility": 0.65, "memory_type": "episodic",
          "lifecycle_state": "proposed", "verification_status": "unverified",
          "metadata_json": json.dumps({"hook": "UserPromptSubmit",
                                       "session_id": "s", "tool": None,
                                       "state": {"mode": "implement"}}),
          "applicable_context_json": "{}",
          "approved_by": None, "governed_exception_for": None,
          "approval_expires_at": None, "simulation_id": None,
          "allowed_agents_json": "[]", "allowed_roles_json": "[]",
          "purpose_allowlist_json": "[]",
          "counterevidence_event_ids_json": "[]"}


def test_full_contract_passes_and_counts_fields():
    v = compare(EVENT, MEMORY)
    assert v.equivalent and v.proof == "full_projection_contract_v1"
    # FULL phải là full: bản 6-trường cũ đo ~6; bản này phải đo trên 20.
    assert v.fields_measured >= 20, (
        f"comparator tự gọi là FULL nhưng chỉ đo {v.fields_measured} trường — "
        f"đúng họ false-green SP-0")


def test_m2_mutant_content_identical_but_full_field_altered_must_die():
    """M2 đúng nghĩa: content GIỮ NGUYÊN, đổi một trường mà comparator FULL
    thật sự kiểm — nếu vẫn equivalent thì bài học SP-0 chưa được khoá."""
    for field, bad in (("observed_at", "2026-08-19T00:00:59+00:00"),
                       ("trust_tier", 4),
                       ("tenant_id", "t2"),
                       ("lifecycle_state", "stable"),
                       ("approved_by", "ai-do")):
        m = dict(MEMORY)
        m[field] = bad
        v = compare(EVENT, m)
        assert not v.equivalent, (
            f"đổi {field} mà vẫn equivalent — comparator không kiểm trường này")
        assert v.reasons, field


def test_degraded_to_content_only_must_die():
    """Mutant CONTENT_ONLY: memory đúng content nhưng mọi semantic về default
    model (chính hình dạng regression SP-0) — FULL comparator phải từ chối."""
    m = dict(MEMORY)
    m.update({"confidence": 0.5, "importance": 0.5, "utility": 0.5,
              "metadata_json": "{}"})
    v = compare(EVENT, m)
    assert not v.equivalent and any("confidence" in r for r in v.reasons)


def test_curated_proof_is_declared_weaker():
    ev = dict(EVENT, source="session-2026-08-06-claude-code",
              payload_json=json.dumps({"content": "bài học font"}))
    m = dict(MEMORY, content="bài học font", confidence=0.95,
             memory_type="procedural",
             metadata_json=json.dumps({"kind": "error_lesson",
                                       "provenance": "curated"}))
    v = compare(ev, m)
    assert v.equivalent
    assert v.proof == "content_plus_curated_provenance_v1", (
        "curated không được mượn danh full contract")


def test_unknown_writer_never_adopts():
    ev = dict(EVENT, source="mystery-writer")
    v = compare(ev, MEMORY)
    assert not v.equivalent and v.contract_name == "UNKNOWN_WRITER"
