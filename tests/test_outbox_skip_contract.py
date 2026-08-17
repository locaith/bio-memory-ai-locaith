"""Một job bị chặn phải đến được trạng thái kết thúc.

`reconciliation_worker` gọi `skip(job.job_id, "<lý do>")` vào chữ ký
`skip(event_id, projection_type, *, reason)`. Vị trí thứ nhất là `event_id`,
thứ hai là `projection_type` — nên nó truyền job_id vào chỗ event_id và một câu
tiếng Việt vào chỗ projection_type. Mệnh đề `WHERE` khớp **0 dòng**, giá trị trả
về bị vứt, và `metrics.tombstoned` vẫn tăng.

Job báo là đã skip và ở lại `in_progress` **mãi mãi**: được lease lại sau mỗi
lần hết hạn, `attempts` tăng không giới hạn, không bao giờ dead-letter vì
`fail()` không nằm trên nhánh này.

Hệ quả xa nhất, và là lý do nó không chỉ là chuyện vệ sinh hàng đợi:
`drain()` **không bao giờ về được 0** trên bất kỳ store nào từng xoá thứ gì. Nó
đốt trọn timeout rồi trả `timed_out`. `docs/v082/ROLLBACK_RUNBOOK.md:71` dùng
`projection drain` làm cổng hoàn tất của quy trình rollback — nên quy trình
rollback không thể đóng trên một store đã từng quên.

Hai call site, một hợp đồng, và chỉ một cái đúng. Đó là lý do bản vá **kiểm giá
trị trả về** thay vì vứt nó đi lần thứ hai.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting, projection_control
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

TEXT = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"


@pytest.fixture()
def queued(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "skip.db", projection_mode="outbox")
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=TEXT, workspace_id="w1")
    yield memory_os, event
    memory_os.close()


def _statuses(memory_os) -> dict[str, int]:
    try:
        rows = memory_os.memories.conn.execute(
            "SELECT status, COUNT(*) FROM projection_outbox GROUP BY status")
    except sqlite3.OperationalError:
        return {}
    return {str(s): int(n) for s, n in rows}


def _attempts(memory_os) -> list[int]:
    try:
        return [int(r[0]) for r in memory_os.memories.conn.execute(
            "SELECT attempts FROM projection_outbox")]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# mini-matrix
# ---------------------------------------------------------------------------

def test_a_normal_job_materialises_and_terminates(queued):
    """Chứng. Nếu đường bình thường không kết thúc được thì ba ca kia không nói
    gì về việc chặn."""
    memory_os, _ = queued
    worker = worker_for(memory_os)
    for _ in range(3):
        worker.run_once()

    assert any(NEEDLE in str(r[0]) for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories"))
    assert _statuses(memory_os) == {"completed": 1}


def test_a_buried_job_reaches_a_terminal_state(queued):
    """Chính bug. Trước bản vá: `in_progress` mãi, attempts 1→8 qua tám vòng."""
    memory_os, _ = queued
    forgetting.forget_derived(memory_os, subject="Hoàng Yến", needle=NEEDLE)

    worker = worker_for(memory_os)
    for _ in range(8):
        worker.run_once()

    statuses = _statuses(memory_os)
    assert statuses.get("in_progress", 0) == 0, (
        f"job bị chặn vẫn ghim in_progress: {statuses}")
    assert statuses.get("pending", 0) == 0
    assert set(statuses) <= {"skipped", "dead_letter"}, statuses
    assert max(_attempts(memory_os), default=0) <= 2, (
        f"attempts tăng không giới hạn: {_attempts(memory_os)}")


def test_retry_semantics_are_unchanged(queued):
    """Nửa phải không được vỡ: một job hỏng tạm thời vẫn phải được thử lại."""
    memory_os, _ = queued
    worker = worker_for(memory_os)
    claimed = worker.outbox.claim(worker_id="w")
    assert claimed, "không claim được job nào — ca này chưa đo được gì"
    job = claimed[0] if isinstance(claimed, list) else claimed

    worker.outbox.fail(job.job_id, "lỗi tạm thời", max_attempts=5)
    statuses = _statuses(memory_os)
    assert statuses.get("pending", 0) == 1, statuses


# ---------------------------------------------------------------------------
# cổng hoàn tất của ROLLBACK_RUNBOOK — không phải unit-test skip()
# ---------------------------------------------------------------------------

def test_the_rollback_runbook_completion_gate_closes(queued):
    """`docs/v082/ROLLBACK_RUNBOOK.md:71` — `projection drain --timeout 300`.

    Chạy đúng hàm mà lệnh đó gọi, trên một store đã từng quên. Trước bản vá nó
    đốt trọn timeout và trả `timed_out=True` với một job còn lại."""
    memory_os, _ = queued
    forgetting.forget_derived(memory_os, subject="Hoàng Yến", needle=NEEDLE)

    report = projection_control.drain(memory_os, timeout_seconds=6.0)

    assert report.get("drained") is True, (
        f"cổng hoàn tất của runbook không đóng được: {report}")
    assert report.get("timed_out") is not True
    assert report.get("remaining", 0) == 0, report


# ---------------------------------------------------------------------------
# mutant
# ---------------------------------------------------------------------------

def test_mutant_skip_using_job_id(queued, monkeypatch):
    """SKIP_USING_JOB_ID — chính code đang ship trước bản vá.

    Nếu mutant này không tái tạo được timeout thì ba ca trên chưa chứng minh
    bản vá sửa gì."""
    memory_os, _ = queued
    forgetting.forget_derived(memory_os, subject="Hoàng Yến", needle=NEEDLE)

    from bio_agent_os.cognitive import outbox as outbox_module

    real_skip = outbox_module.ProjectionOutbox.skip

    def broken(self, event_id, projection_type="", *, reason=""):
        # The shipped call: job_id into event_id, the reason into
        # projection_type. Matches nothing.
        return real_skip(self, event_id, "sự kiện đã bị đặt bia mộ",
                         reason=reason)

    monkeypatch.setattr(outbox_module.ProjectionOutbox, "skip", broken)

    worker = worker_for(memory_os)
    for _ in range(4):
        worker.run_once()

    statuses = _statuses(memory_os)
    assert statuses.get("in_progress", 0) >= 1, (
        f"mutant không tái tạo được job ghim in_progress: {statuses}")

    report = projection_control.drain(memory_os, timeout_seconds=3.0)
    assert report.get("drained") is not True, (
        f"mutant không tái tạo được drain timeout: {report}")
