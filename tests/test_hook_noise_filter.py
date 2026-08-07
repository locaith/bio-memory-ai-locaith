"""A hook firing is an event. It is not automatically a memory.

Measured on the live store, 2026-08-07: a recall limited to five results
returned three rows whose entire content was `hook=SessionStart`. The store held
the company bank account for a signed contract and a customer's corrected
pricing at that moment. It was not short of signal — the signal was being
outvoted by the system's own logging, which is newer than everything else on
every single session and therefore wins on recency forever.

The distinction these tests pin: the **event** is always written, because events
are the audit trail and dropping one would misreport what happened; the
**memory** is only written when the payload brought something back.
"""

from __future__ import annotations

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import AccessContext


@pytest.fixture()
def adapter(tmp_path) -> ClaudeCodeHookAdapter:
    return ClaudeCodeHookAdapter(MemoryOS(tmp_path / "memory.db"),
                                 tenant_id="locaith", workspace_id="ws")


def _memory_count(adapter: ClaudeCodeHookAdapter) -> int:
    return adapter.memory_os.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]


def _event_count(adapter: ClaudeCodeHookAdapter) -> int:
    return adapter.memory_os.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events").fetchone()[0]


def test_a_bare_hook_records_an_event_but_not_a_memory(adapter):
    result = adapter.ingest("SessionStart", {})

    assert result.accepted, (
        "the hook must still be accepted — SessionStart is exactly where recalled "
        "context gets injected, and refusing it would silence the brief")
    assert result.event_id, "the event is the audit trail and must be written"
    assert result.memory_id is None
    assert result.reason == "no_substantive_content"
    assert _event_count(adapter) == 1
    assert _memory_count(adapter) == 0


def test_a_hook_carrying_a_prompt_becomes_a_memory(adapter):
    result = adapter.ingest("UserPromptSubmit", {
        "prompt": "số tài khoản công ty để ký hợp đồng Reti là gì"})

    assert result.accepted and result.memory_id
    assert result.reason is None
    assert _memory_count(adapter) == 1


def test_a_marker_sized_value_is_not_substance(adapter):
    """An "ok" is not a memory, and neither is a stray punctuation mark."""
    for payload in ({"prompt": ""}, {"prompt": "  "}, {"prompt": "ok"},
                    {"tool_output": "."}, {"reason": None}, {"tool_input": {}}):
        result = adapter.ingest("PostToolUse", payload)
        assert result.memory_id is None, f"{payload} produced a memory"
    assert _memory_count(adapter) == 0
    assert _event_count(adapter) == 6, "every one of them is still an event"


def test_the_noise_no_longer_crowds_out_the_signal(adapter):
    """The measured failure, reproduced end to end.

    Twelve bare session hooks land after two real memories. Before the filter,
    a five-result recall came back mostly noise because the noise is always the
    newest thing in the store. After it, the real rows are the only rows there
    are to return.
    """
    adapter.ingest("UserPromptSubmit", {
        "prompt": "tài khoản công ty là Techcombank 19040131667011, chi nhánh Chùa Láng"})
    adapter.ingest("UserPromptSubmit", {
        "prompt": "đơn giá ARCHILAB xây trọn gói 6,0-6,9 triệu mỗi m2 diện tích quy đổi"})
    for _ in range(12):
        adapter.ingest("SessionStart", {})

    assert _memory_count(adapter) == 2
    assert _event_count(adapter) == 14, "the twelve sessions still happened"

    found = adapter.memory_os.recall(
        "tài khoản công ty Techcombank hợp đồng",
        context=AccessContext(tenant_id="locaith", workspace_id="ws",
                              agent_id="claude-code"),
        limit=5)
    contents = [str(r.memory.content) for r in found]
    assert contents, "recall returned nothing at all"
    assert not any("hook=SessionStart" in c and "prompt=" not in c for c in contents), (
        f"bare hook rows are still competing for slots: {contents}")
    assert any("Techcombank" in c for c in contents), contents


def test_an_unsupported_hook_is_still_rejected_the_old_way(adapter):
    """The new reason must not blur into the existing one."""
    result = adapter.ingest("NotAHook", {"prompt": "something real"})
    assert not result.accepted
    assert result.reason == "unsupported_hook"
    assert _event_count(adapter) == 0
