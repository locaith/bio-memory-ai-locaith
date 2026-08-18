"""Hợp đồng miền của `lease_seconds` — biên số học, và câu hỏi shadow-drain.

Nửa đầu: `claim()` phải từ chối mọi độ dài lease vô nghĩa TRƯỚC khi chạm vào
trạng thái. Không phải vì đã thấy bug ở NaN — vì đây là biên miền của một đại
lượng thời gian, và NaN nói riêng biến mọi phép so sánh thành False, tức một
loại ma khác trong hàng đợi.

Nửa sau: zero lease cũ có thể đã che một giả định trong định nghĩa "shadow
drain xong". Vòng lặp của `run_shadow_comparison` kết thúc khi một chu kỳ không
giành thêm được gì — mà "không có gì giành được LÚC NÀY" không đồng nghĩa
"hàng đợi đã cạn": một lease đang sống làm `claim()` trả rỗng ngay cả khi việc
còn nguyên đó.
"""
from __future__ import annotations

import math

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.outbox import validate_lease_seconds
from bio_agent_os.cognitive.reconciliation_worker import worker_for

POISON = "Số điện thoại của Hoàng Yến là 0977123456."


# ---------------------------------------------------------------------------
# biên miền
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [0, 0.0, -1, -0.001,
                                 float("nan"), float("inf"), float("-inf")])
def test_invalid_lease_is_rejected_before_any_state_mutation(tmp_path, bad):
    memory_os = MemoryOS(tmp_path / "m.db", projection_mode="outbox")
    try:
        memory_os.observe(tenant_id="t1", actor="a", source="u",
                          content=POISON, workspace_id="w1")
        outbox = worker_for(memory_os).outbox
        before = [tuple(r) for r in memory_os.memories.conn.execute(
            "SELECT job_id, status, attempts, locked_by FROM projection_outbox")]

        with pytest.raises(ValueError):
            outbox.claim(worker_id="w", lease_seconds=bad, tenant_id="t1")

        after = [tuple(r) for r in memory_os.memories.conn.execute(
            "SELECT job_id, status, attempts, locked_by FROM projection_outbox")]
        assert after == before, (
            f"cấu hình bị từ chối mà trạng thái vẫn đổi: {before} -> {after}")
    finally:
        memory_os.close()


def test_a_positive_lease_is_accepted(tmp_path):
    memory_os = MemoryOS(tmp_path / "p.db", projection_mode="outbox")
    try:
        memory_os.observe(tenant_id="t1", actor="a", source="u",
                          content=POISON, workspace_id="w1")
        got = worker_for(memory_os).outbox.claim(
            worker_id="w", lease_seconds=0.001, tenant_id="t1")
        assert len(got) == 1
    finally:
        memory_os.close()


def test_the_validator_is_a_single_shared_rule():
    """Một luật, không phải hai luật viết hai nơi."""
    for bad in (0, -5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            validate_lease_seconds(bad)
    assert validate_lease_seconds(0.5) == 0.5
    assert math.isfinite(validate_lease_seconds(300))


# ---------------------------------------------------------------------------
# shadow drain — câu hỏi mở, đo trực tiếp, không suy từ ba ca fault-injection
# ---------------------------------------------------------------------------

def test_shadow_drain_does_not_mistake_a_live_lease_for_an_empty_queue(tmp_path):
    """Dựng đúng shape: một job `in_progress` với lease CÒN SỐNG, rồi hỏi
    `run_shadow_comparison` có kết luận nhầm là đã drain xong không.

    Nếu report coi mọi thứ ổn trong khi một job còn chưa được build, thì
    "không giành được gì chu kỳ này" đang bị đánh đồng với "hàng đợi đã cạn" —
    và zero lease trước đây đã che khuyết tật đó bằng cách làm cho hai mệnh đề
    ấy trùng nhau về mặt sự kiện.
    """
    from bio_agent_os.cognitive.shadow_runner import (run_shadow_comparison,
                                                      shadow_worker)

    memory_os = MemoryOS(tmp_path / "s.db", projection_mode="shadow")
    try:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=POISON, workspace_id="w1")
        # Một worker khác đang GIỮ job — lease dương, chưa hết hạn.
        holder = shadow_worker(memory_os, worker_id="dang-giu",
                               lease_seconds=3600)
        held = holder.outbox.claim(worker_id="dang-giu", lease_seconds=3600,
                                   tenant_id="t1")
        assert held, "không dựng được shape: job phải đang bị giữ"

        report = run_shadow_comparison(memory_os, [event.event_id])

        built = memory_os.shadow_memories.count()

        assert built == 0, "lease đang sống mà job vẫn được build — lease vô nghĩa"
        # Phát biểu trung tâm: report KHÔNG được coi trạng thái này là sạch.
        assert not report.clean, (
            f"SHADOW DRAIN BUG: một job còn nguyên trong hàng đợi (lease sống, "
            f"chưa build) mà report kết luận mọi thứ ổn — 'không claim được gì "
            f"lúc này' đang bị đánh đồng với 'đã drain xong': built={built} "
            f"statuses={report.by_status()}")
    finally:
        memory_os.close()
