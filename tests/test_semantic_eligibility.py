"""Telemetry không phải một lời nói về thế giới.

Đo trên store thật của chủ sở hữu, 17/08/2026: 183 hàng, 156 là telemetry hook,
và **cả 20** hàng có structured slot đều mang entity bắt đầu bằng `hook` hoặc
`UserPromptSubmit`. Hệ thống đang giữ một niềm tin về **nơi làm việc** của một
thứ tên "hook UserPromptSubmit prompt …", chín lần.

Ranh giới:

    EVENT OBSERVED
        ├─ một lời nói về thế giới  -> memory semantics
        ├─ telemetry                -> observability, GIỮ, không semanticize
        ├─ system event             -> provenance / audit
        └─ chưa rõ                  -> không thăng cấp ngữ nghĩa

Khoá theo **provenance, không theo văn bản**. Một luật đọc chữ sẽ nổ khi có
người trích dẫn một dòng log, và sẽ trượt khi hook đổi cách viết.

Và nó **không xoá gì**: raw event, history, content đều nguyên vẹn. Chỉ cái
claim subject/predicate/value suy ra từ chúng là bị gỡ.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.slot_backfill import (
    backfill, is_operational_source, slot_for)

#: Cùng hình dạng store thật ghi ra.
HOOK_SOURCE = "claude-code:UserPromptSubmit"
HUMAN_SOURCE = "session-2026-08-06-claude-code"
#: Một dòng telemetry giải được thành slot — đó chính là vấn đề.
TELEMETRY = "hook=UserPromptSubmit | Bùi Cường làm việc tại công ty Locaith."
HUMAN = "Bùi Cường làm việc tại công ty Locaith."


def test_the_boundary_keys_on_provenance_not_on_words():
    assert is_operational_source("claude-code:UserPromptSubmit")
    assert is_operational_source("some-other-agent:SessionStart")
    assert not is_operational_source(HUMAN_SOURCE)
    assert not is_operational_source("user")
    assert not is_operational_source(None)


def test_the_same_sentence_is_a_claim_from_a_person_and_not_from_a_hook():
    """Cùng một chuỗi ký tự. Chỉ provenance khác. Đây là toàn bộ luật."""
    assert slot_for(HUMAN, event_source=HUMAN_SOURCE).get("attribute")
    assert slot_for(HUMAN, event_source=HOOK_SOURCE) == {}


def test_a_hook_memory_gets_no_slot_at_ingest(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "hook.db")
    try:
        event = memory_os.observe(tenant_id="t1", actor="claude-code",
                                  source=HOOK_SOURCE, content=TELEMETRY,
                                  workspace_id="w1")
        memory = memory_os.remember(event=event,
                                    memory_type=MemoryType.EPISODIC,
                                    content=TELEMETRY, confidence=0.9)
        assert not (memory.structured_content or {}).get("attribute"), (
            "telemetry vẫn được thăng cấp thành claim")
    finally:
        memory_os.close()


def test_a_person_memory_still_gets_one(tmp_path: Path):
    """Nửa phải không được vỡ. Một ranh giới chặn cả người nói là một công tắc
    tắt, không phải một ranh giới."""
    memory_os = MemoryOS(tmp_path / "human.db")
    try:
        event = memory_os.observe(tenant_id="t1", actor="tuan-anh",
                                  source=HUMAN_SOURCE, content=HUMAN,
                                  workspace_id="w1")
        memory = memory_os.remember(event=event,
                                    memory_type=MemoryType.SEMANTIC,
                                    content=HUMAN, confidence=0.9)
        assert (memory.structured_content or {}).get("attribute") == "employer"
    finally:
        memory_os.close()


@pytest.fixture()
def polluted(tmp_path: Path):
    """Store như nó đang có thật: một hàng telemetry đã mang slot."""
    memory_os = MemoryOS(tmp_path / "polluted.db")
    event = memory_os.observe(tenant_id="t1", actor="claude-code",
                              source=HOOK_SOURCE, content=TELEMETRY,
                              workspace_id="w1")
    memory = memory_os.remember(event=event, memory_type=MemoryType.EPISODIC,
                                content=TELEMETRY, confidence=0.9)
    conn = memory_os.memories.conn
    conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"entity": "hook UserPromptSubmit prompt",
                     "attribute": "employer",
                     "resolver_version": "aspect_resolver@1",
                     "goal": "giữ nguyên tôi"},
                    ensure_ascii=False, sort_keys=True), memory.memory_id))
    conn.commit()
    yield memory_os, str(memory.memory_id)
    memory_os.close()


def _slot(memory_os, memory_id) -> dict:
    (blob,) = memory_os.memories.conn.execute(
        "SELECT structured_json FROM cognitive_memories WHERE memory_id = ?",
        (memory_id,)).fetchone()
    return json.loads(blob) if blob else {}


def test_backfill_demotes_a_slot_that_was_never_eligible(polluted):
    memory_os, memory_id = polluted
    assert _slot(memory_os, memory_id)["attribute"] == "employer"

    report = backfill(memory_os.memories.conn)

    assert report.operational_skipped >= 1
    assert not _slot(memory_os, memory_id).get("attribute")
    assert not _slot(memory_os, memory_id).get("entity")


def test_the_demotion_is_reported_row_by_row(polluted):
    """Việc duy nhất trong pass này lấy đi thứ gì đó, nên nó không được phép
    chỉ là một con số."""
    memory_os, memory_id = polluted
    report = backfill(memory_os.memories.conn)

    (demoted,) = [d for d in report.operational_demoted
                  if d["memory_id"] == memory_id]
    assert demoted["source"] == HOOK_SOURCE
    assert demoted["was_attribute"] == "employer"
    assert demoted["was_entity"] == "hook UserPromptSubmit prompt"


def test_demotion_keeps_everything_it_does_not_own(polluted):
    """Procedural compiler ghi goal/steps vào cùng blob. Gỡ slot mà đuổi luôn
    chúng là một cú xoá đội lốt từ 'ranh giới'."""
    memory_os, memory_id = polluted
    backfill(memory_os.memories.conn)
    assert _slot(memory_os, memory_id)["goal"] == "giữ nguyên tôi"


def test_the_raw_event_and_content_are_untouched(polluted):
    """Không xoá lịch sử thô vì derived structure sai."""
    memory_os, memory_id = polluted
    conn = memory_os.memories.conn
    before = conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0]

    backfill(conn)

    assert conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == before
    (content,) = conn.execute(
        "SELECT content FROM cognitive_memories WHERE memory_id = ?",
        (memory_id,)).fetchone()
    assert content == TELEMETRY


def test_a_second_pass_demotes_nothing_new(polluted):
    memory_os, _ = polluted
    backfill(memory_os.memories.conn)
    again = backfill(memory_os.memories.conn)
    assert again.operational_demoted == []
