"""Replay puts back what a deletion removed. Recorded, not yet fixed.

Reproduced on 2026-08-15, forty lines, no world model and no benchmark:

    remember "Số điện thoại của Hoàng Yến là 0977123456."
    forget_scoped(...)          -> deleted=1, verified_clean=True
    cognitive_memories          -> 0                        gone
    replay + rebuild_version + drain
    cognitive_memories          -> 1                        back, verbatim

Nothing here is broken by accident. `forget_scoped` runs at derived level and
says so; the event log is append-only by design; the replay engine rebuilds
projections from that log. Each piece is correct alone. Together they mean a
deletion holds until the first recovery operation, and `verified_clean=True`
is a true answer to the wrong question — it says the deleter did what it
decided to do, never that the value is gone for good.

The lifetime benchmark measured the same thing from the other end on the same
day: the serving layer was clean after all eighteen deletion requests, and all
eighteen values were still in `cognitive_events.payload_json`.

Two ways out, and the choice is not the test's to make:

    replay consults the erasure record and skips tombstoned events
    forget at derived level refuses to report success while the payload stands

Marked xfail(strict=True) deliberately: when someone fixes this, the suite
fails until they come here and say which of the two they chose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

SECRET = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "resurrect.db")
    yield memory_os
    memory_os.close()


def _holding(memory_os: MemoryOS) -> int:
    return memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]


def _store_and_forget(memory_os: MemoryOS) -> None:
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=SECRET, workspace_id="w1")
    memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                       content=SECRET, confidence=0.9)
    assert _holding(memory_os) == 1

    result = forget_scoped(memory_os, "Hãy quên số điện thoại của Hoàng Yến.",
                           actor="test")
    assert result.deleted_claims == 1
    assert _holding(memory_os) == 0, "xoá còn chưa chạy được thì test này vô nghĩa"


def test_the_event_log_keeps_the_payload_after_a_derived_delete(os_):
    """The precondition, stated so the xfail below cannot be misread.

    This is the design, not the defect. It is what makes the deletion
    reversible, and the reversal is the next test.
    """
    _store_and_forget(os_)
    n = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert n == 1


@pytest.mark.xfail(strict=True, reason="replay không đọc tombstone — ký ức sống lại")
def test_a_rebuild_does_not_bring_back_what_was_forgotten(os_):
    from bio_agent_os.cognitive.projection_control import drain
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    _store_and_forget(os_)

    engine = ProjectionReplayEngine(os_.memories.conn)
    engine.replay(dry_run=False, tenant_id="t1")
    engine.rebuild_version("cognitive_memory", tenant_id="t1", dry_run=False)
    drain(os_, timeout_seconds=30, worker_id="resurrection-test")

    assert _holding(os_) == 0, (
        "ký ức đã xoá quay lại sau replay — continuity engine đang hồi sinh "
        "thứ người dùng yêu cầu quên")
