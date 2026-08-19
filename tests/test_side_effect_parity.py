"""SP-1B — SIDE-EFFECT PARITY: hai đường ghi phải để lại CÙNG dấu vết bên ngoài.

Nợ bằng chứng do CI-CLOSEOUT khai ra: luật này có runtime thật
(`CognitiveMemoryBuilder.after_commit`) nhưng **không một test nào** trong
suite chạm tới `after_commit` hay `invalidate_scope`. Nó chỉ được chứng bởi
MỘT lần chạy activation — một nhân chứng, không phải một hàng rào.

    EXACTLY-ONCE EXECUTION != SEMANTIC PARITY
    và semantic parity không dừng ở hàng đã ghi: nó gồm cả những gì
    hàng ấy làm với phần còn lại của hệ thống.

`remember()` làm ba việc rời nhau, đúng thứ tự này:

    memories.put(memory)
    world_model.ingest(stored)
    context_blocks.invalidate_scope(tenant, workspace)

Đường outbox phải tái tạo đúng bộ ba đó, và phải chạy SAU commit của cặp
ledger+memory — không nằm trong transaction, vì legacy cũng không.
"""
from __future__ import annotations

import sqlite3

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.reconciliation_worker import (CognitiveMemoryBuilder,
                                                          worker_for)

PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "sp1b",
          "prompt": "khách An Phú chốt hợp đồng 120 triệu, mã SP1B-PARITY."}


class _Spy:
    """Ghi lại thứ tự các lời gọi — thứ tự là một phần của hợp đồng."""

    def __init__(self, memory_os) -> None:
        self.calls: list = []
        self._os = memory_os
        self.durable_at_call: list = []

    def ingest(self, stored) -> None:
        self.calls.append(("ingest", str(stored.memory_id)))
        self.durable_at_call.append(("ingest", self._row_is_durable(stored)))

    def invalidate_scope(self, tenant_id, workspace_id) -> None:
        self.calls.append(("invalidate_scope", tenant_id, workspace_id))

    def _row_is_durable(self, stored) -> bool:
        """Đọc bằng MỘT CONNECTION KHÁC: nếu hàng đã thấy được từ ngoài
        transaction thì commit đã xảy ra TRƯỚC side-effect — đúng thứ tự
        legacy. Hỏi chính connection đang ghi thì luôn thấy, và câu trả lời
        đó không chứng minh gì."""
        conn = sqlite3.connect(
            f"file:{self._os.db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM cognitive_memories WHERE memory_id=?",
                (str(stored.memory_id),)).fetchone()
        except sqlite3.OperationalError:
            return False
        finally:
            conn.close()
        return row is not None


def _outbox_run(tmp_path, name="sp1b.db"):
    memory_os = MemoryOS(tmp_path / name, projection_mode="outbox")
    spy = _Spy(memory_os)
    memory_os.world_model = spy
    memory_os.context_blocks = spy
    ClaudeCodeHookAdapter(memory_os, "t1", "w1").ingest("UserPromptSubmit",
                                                        PROMPT)
    worker_for(memory_os, lease_seconds=300).run_once(batch_size=10)
    return memory_os, spy


def _legacy_run(tmp_path, name="legacy.db"):
    memory_os = MemoryOS(tmp_path / name, projection_mode="legacy")
    spy = _Spy(memory_os)
    memory_os.world_model = spy
    memory_os.context_blocks = spy
    ClaudeCodeHookAdapter(memory_os, "t1", "w1").ingest("UserPromptSubmit",
                                                        PROMPT)
    return memory_os, spy


def test_outbox_path_reproduces_the_same_side_effects_as_remember(tmp_path):
    """Cùng một input, hai đường ghi, CÙNG dấu vết bên ngoài."""
    legacy_os, legacy = _legacy_run(tmp_path)
    outbox_os, outbox = _outbox_run(tmp_path)
    try:
        shape = lambda spy: [c[0] for c in spy.calls]         # noqa: E731
        assert shape(legacy) == ["ingest", "invalidate_scope"], legacy.calls
        assert shape(outbox) == shape(legacy), (
            f"đường outbox để lại dấu vết khác legacy: "
            f"{outbox.calls} vs {legacy.calls}")
        assert legacy.calls[1][1:] == outbox.calls[1][1:], (
            "invalidate_scope gọi trên scope khác nhau giữa hai đường ghi")
    finally:
        legacy_os.close()
        outbox_os.close()


def test_side_effects_run_after_the_row_is_durable(tmp_path):
    """SAU commit, không nằm trong transaction — đúng thứ tự legacy.

    Đo bằng một connection ĐỘC LẬP: hàng phải đã thấy được từ bên ngoài tại
    thời điểm side-effect chạy."""
    memory_os, spy = _outbox_run(tmp_path)
    try:
        assert spy.durable_at_call, "side-effect không hề chạy"
        for what, durable in spy.durable_at_call:
            assert durable, (
                f"{what} chạy khi hàng CHƯA bền — side-effect nằm trong "
                f"transaction, khác hợp đồng của legacy")
    finally:
        memory_os.close()


def test_mutant_dropping_after_commit_must_die(tmp_path, monkeypatch):
    """M: bỏ `after_commit` — parity phải vỡ.

    Nếu mutant này không làm test đỏ thì hai test trên đang xanh vì lý do
    khác, không vì side-effect thật sự được tái tạo."""
    monkeypatch.setattr(CognitiveMemoryBuilder, "after_commit",
                        lambda self: None)
    memory_os, spy = _outbox_run(tmp_path, "mutant.db")
    try:
        built = memory_os.memories.conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        assert built == 1, "mutant chưa chạm được đường ghi — ký ức không được dựng"
        assert spy.calls == [], (
            f"bỏ after_commit mà side-effect vẫn xảy ra: {spy.calls} — "
            f"chúng đến từ đường nào đó khác, và test trên chưa đo cái nó nghĩ")
    finally:
        memory_os.close()
