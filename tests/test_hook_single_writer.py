"""Single-writer-by-mode contract của production hook adapter.

Sự cố 18/08: dưới outbox, `ingest` gọi cả `observe()` (mọc projection job)
lẫn `remember()` (ghi thẳng) — một prompt thật của chủ thành hai memory.
Bản vá: OUTBOX có đúng MỘT materialization authority là worker, và một
event không đáng thành memory thì không được nợ projection.

Mỗi ca khai `declared_mode` và đối chiếu `effective_mode` đọc từ runtime —
lệch là INVALID, không phải PASS (bài học env-leak 18/08).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.shadow import ProjectionMode

PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "s",
          "prompt": "nội dung đáng nhớ mã 0905550001"}
EMPTY = {"hook_event_name": "SessionStart", "session_id": "s"}


def _rig(tmp_path: Path, mode: str):
    memory_os = MemoryOS(tmp_path / f"{mode}.db", projection_mode=mode)
    assert memory_os.projection_mode is ProjectionMode(mode), (
        f"INVALID — declared={mode} effective={memory_os.projection_mode}")
    return memory_os, ClaudeCodeHookAdapter(memory_os, "t1", "w1")


def _counts(memory_os) -> dict:
    conn = memory_os.memories.conn
    return {
        "events": conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0],
        "memories": conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0],
        "pending": conn.execute(
            "SELECT COUNT(*) FROM projection_outbox "
            "WHERE status IN ('pending','in_progress')").fetchone()[0],
        "completed": conn.execute(
            "SELECT COUNT(*) FROM projection_outbox "
            "WHERE status='completed'").fetchone()[0],
    }


def test_legacy_substantive_one_memory_no_production_debt(tmp_path):
    memory_os, adapter = _rig(tmp_path, "legacy")
    try:
        result = adapter.ingest("UserPromptSubmit", PROMPT)
        c = _counts(memory_os)
        assert result.accepted and result.memory_id is not None
        assert c == {"events": 1, "memories": 1, "pending": 0, "completed": 0}, c
    finally:
        memory_os.close()


def test_outbox_substantive_worker_is_the_only_writer(tmp_path):
    memory_os, adapter = _rig(tmp_path, "outbox")
    try:
        result = adapter.ingest("UserPromptSubmit", PROMPT)
        before = _counts(memory_os)
        assert result.accepted and result.memory_id is None
        assert before == {"events": 1, "memories": 0, "pending": 1,
                          "completed": 0}, before
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        after = _counts(memory_os)
        assert after == {"events": 1, "memories": 1, "pending": 0,
                         "completed": 1}, after
        ledger = memory_os.memories.conn.execute(
            "SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
        assert ledger == 1
    finally:
        memory_os.close()


def test_outbox_non_substantive_stays_event_only(tmp_path):
    memory_os, adapter = _rig(tmp_path, "outbox")
    try:
        result = adapter.ingest("SessionStart", EMPTY)
        assert result.accepted and result.reason == "no_substantive_content"
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        c = _counts(memory_os)
        assert c == {"events": 1, "memories": 0, "pending": 0,
                     "completed": 0}, (
            f"con rồng mọc đầu bé: event-only bị materialize — {c}")
    finally:
        memory_os.close()


def test_shadow_semantics_unchanged(tmp_path):
    memory_os, adapter = _rig(tmp_path, "shadow")
    try:
        result = adapter.ingest("UserPromptSubmit", PROMPT)
        c = _counts(memory_os)
        # SHADOW: memory production legacy VẪN được ghi thẳng, và nợ shadow
        # vẫn được enqueue — cách ly ở shadow_memories, không đổi hợp đồng.
        assert result.accepted and result.memory_id is not None
        assert c["events"] == 1 and c["memories"] == 1 and c["pending"] == 1, c
    finally:
        memory_os.close()


def test_m1_exact_incident_mutant_restoring_remember_duplicates(
        tmp_path, monkeypatch):
    """M1: bật lại direct-write dưới OUTBOX → đúng hình sự cố, 1 → 2."""
    memory_os, adapter = _rig(tmp_path, "outbox")
    try:
        monkeypatch.setattr(ClaudeCodeHookAdapter, "_direct_write_allowed",
                            lambda self: True)
        adapter.ingest("UserPromptSubmit", PROMPT)
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        c = _counts(memory_os)
        assert c["memories"] == 2, (
            f"mutant bật lại remember mà không nhân đôi — ca sạch đang xanh "
            f"vì lý do khác: {c}")
    finally:
        memory_os.close()


def test_m2_substantive_mutant_materializes_markers(tmp_path, monkeypatch):
    """M2: cho event-only nợ projection → `hook=SessionStart` thành memory."""
    memory_os, adapter = _rig(tmp_path, "outbox")
    try:
        monkeypatch.setattr(ClaudeCodeHookAdapter, "_projection_debt_allowed",
                            lambda self, substantive: True)
        adapter.ingest("SessionStart", EMPTY)
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        c = _counts(memory_os)
        assert c["memories"] == 1 and "hook=SessionStart" in str(
            memory_os.memories.conn.execute(
                "SELECT content FROM cognitive_memories").fetchone()[0]), (
            f"mutant thả substantive-gate mà marker không materialize: {c}")
    finally:
        memory_os.close()
