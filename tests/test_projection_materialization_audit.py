"""Bolt 2 is the one holding the door, and the job it skips never leaves.

Written 2026-08-17 during the projection materialization audit, which was
commissioned on a false premise: that nothing in `bio_agent_os/` consumes
`projection_outbox` and writes `cognitive_memories`. `reconciliation_worker.py`
has done exactly that since 2026-08-04. The grep that produced the premise
searched for a table name in a module that reaches its tables through objects —
`self.outbox.claim(...)`, `self.memories.put(...)` — so it could not have found
it.

That premise had a consequence worth undoing: the at-apply tombstone check at
`reconciliation_worker.py:334` was described as unreachable, and an unreachable
check is one nobody has to keep. It is not unreachable. This file kills a mutant
to prove it is load-bearing, on the one fixture where it stands alone:

    the job is queued BEFORE the forget

On that fixture `replay()` enqueues nothing — the key already exists — so the
enqueue-time check in `projection_engine` never gets a vote. Only the at-apply
check can stop the rebuild, and blinding it alone brings the memory back
verbatim. One mutation, opposite outcome.

The second test records the defect found while proving the first. The check
holds, and then `outbox.skip()` is called with the wrong arguments, so the job
it just declined is never retired. It is marked `xfail(strict=True)` on purpose:
it asserts the behaviour the system is supposed to have, so it fails today and
goes RED the day `:335` is fixed — which is when someone should come back and
delete the marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting, reconciliation_worker
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.reconciliation_worker import worker_for

SECRET = "Số điện thoại của Hoàng Yến là 0977123456."
CONTROL = "Lê Sơn là trưởng nhóm kỹ thuật."
NEEDLE = "0977123456"
SUBJECT = "Hoàng Yến"


@pytest.fixture()
def queued(tmp_path: Path):
    """A store in outbox mode with the projection job queued and NOT drained.

    The memory exists because the legacy write path wrote it; the job exists
    because `observe()` enqueued it. Forgetting now is the case the two-bolt
    design was built for: a queue outlives the decision that filled it.
    """
    memory_os = MemoryOS(tmp_path / "queued.db", projection_mode="outbox")
    for text in (SECRET, CONTROL):
        event = memory_os.observe(tenant_id="t1", actor="test", source="chat",
                                  content=text, workspace_id="w1")
        memory_os.remember(event=event, memory_type=MemoryType.EPISODIC,
                           content=text)
    yield memory_os
    memory_os.close()


def _holding(memory_os: MemoryOS) -> int:
    return memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]


def _controls_alive(memory_os: MemoryOS) -> int:
    """The control memory must survive every run, or the probe is measuring a
    store that lost everything rather than a deletion that held.

    Counted, not asserted equal to one. After a drain this returns **2**: in
    outbox mode `remember()` writes the memory and the builder writes a second
    copy of the same content under a new `memory_id`. That duplication is a
    real defect and it is recorded in PROJECTION_MATERIALIZATION_AUDIT.md §6.3;
    pinning the number here would make this file fail for the wrong reason the
    day it is fixed.
    """
    return memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        ("%trưởng nhóm%",)).fetchone()[0]


def _forget_and_drain(memory_os: MemoryOS, worker_id: str):
    report = forgetting.forget_derived(memory_os, subject=SUBJECT, actor="test")
    assert report.memories_deleted >= 1, "xoá chưa chạy thì test này vô nghĩa"
    assert _holding(memory_os) == 0, "xoá chưa chạy thì test này vô nghĩa"
    metrics = worker_for(memory_os, worker_id=worker_id).run_once(batch_size=50)
    return report, metrics


def test_the_at_apply_tombstone_check_is_what_holds_a_queued_job(queued):
    """Clean run: the job was queued before the forget, and it is declined."""
    _, metrics = _forget_and_drain(queued, "audit-clean")

    assert metrics.tombstoned == 1, (
        "cái chốt thứ hai không chạy — job đã xếp hàng trước khi xoá mà worker "
        "không đếm tombstoned lần nào")
    assert _holding(queued) == 0, "RESURRECTION — ký ức đã quên quay lại"
    assert _controls_alive(queued) >= 1, "control chết: phép đo không còn nghĩa"


def test_blinding_only_the_at_apply_check_resurrects_it(queued, monkeypatch):
    """The mutant. If this does not resurrect, the test above proves nothing.

    Only `reconciliation_worker.buried` is blinded. The enqueue-time check in
    `projection_engine` is left alone and is not consulted anyway on this
    fixture, which is the entire point of the fixture.
    """
    monkeypatch.setattr(reconciliation_worker, "buried",
                        lambda conn, event_id: False)
    _, metrics = _forget_and_drain(queued, "audit-mutant")

    assert metrics.tombstoned == 0, "mutant chưa chạm được vào chốt nó định tắt"
    assert _holding(queued) == 1, (
        "mutant tắt chốt thứ hai mà ký ức vẫn không quay lại — probe chưa chạm "
        "được vào cơ chế nó định đo, nên kết quả sạch ở test trên chưa chứng "
        "minh gì")
    assert _controls_alive(queued) >= 1, "control chết: phép đo không còn nghĩa"


#: Marker gỡ ngày 17/08 khi `:335` được sửa. Nó là một cái chuông: audit để lại
#: `xfail(strict=True)` kèm chỉ dẫn "sửa xong thì test này XPASS và strict sẽ
#: báo đỏ — lúc đó xoá marker", và nó reo đúng lúc bản vá đáp xuống. Một khuyết
#: tật đã biết mà không có ngày hết hạn thì sẽ ở lại cho tới khi có người tình
#: cờ đọc lại nó.
def test_a_tombstoned_job_is_retired_and_the_queue_can_drain(queued):
    """Safety held; hygiene did not. This asserts the hygiene.

    The memory stays deleted either way — that is checked here too, so a future
    fix cannot make this test green by breaking the deletion.
    """
    from bio_agent_os.cognitive.projection_control import drain

    _forget_and_drain(queued, "audit-hygiene")
    assert _holding(queued) == 0, "RESURRECTION — không được đánh đổi"

    row = queued.events.conn.execute(
        "SELECT status FROM projection_outbox WHERE status NOT IN "
        "('completed','skipped')").fetchone()
    assert row is None, (
        f"job bị bia mộ chưa được cho nghỉ: còn {row['status'] if row else ''}")

    result = drain(queued, timeout_seconds=2, worker_id="audit-drain")
    assert result["drained"] is True, (
        f"hàng đợi không rút hết sau một lần xoá: {result['queue_after']}")
