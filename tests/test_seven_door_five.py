"""#7 Door 5, đo lại bằng đường materialization thật.

Câu hỏi **hẹp**: #7 có làm yếu tombstone/materialization safety không?

KHÔNG phải: "Bio-Memory đã replay-safe toàn cục chưa?" Đó là câu khác, hiện
`PARTIALLY VERIFIED`, và trộn hai câu lại là cách một bản vá vô tình mang theo
một lời hứa nó chưa bao giờ đưa ra.

Door 5 từng là `NOT_EVALUABLE` vì tôi kết luận không có consumer nào vật chất
hoá outbox. Sai: `ReconciliationWorker` claim outbox và ghi `cognitive_memories`,
có trong cây từ 04/08. Grep của tôi trượt vì consumer không bao giờ gõ tên bảng
trên đường tiêu thụ. Giờ đường nguy hiểm chạy được, nên câu hỏi trả lời được.

**Cô lập.** HEAD chứa cả P0-A và bản vá `:335`. So HEAD với commit trước #7 sẽ
lẫn ba thay đổi và không quy trách nhiệm được cho cái nào — đúng lỗi
`STALE BASELINE != CAUSAL DELTA` ở quy mô nhỏ. Nên hai nhánh ở đây khác nhau
**duy nhất** ở ngữ nghĩa #7, bật tắt trong cùng một tiến trình.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting, temporal_operator as T
from bio_agent_os.cognitive import reconciliation_worker
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.tombstones import buried

TEXT = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"
SUBJECT = "Hoàng Yến"


def _pre_seven_key(content: str) -> str:
    """`_core` như trước #7: chỉ strip restatement và correction."""
    text = T._RESTATEMENT.sub("", T._fold(content))
    text = T._CORRECTION.sub("", text)
    return re.sub(r"^[\s,;:]+", "", text).lower().rstrip(" .!?")


@pytest.fixture(params=["pre-#7", "#7"])
def arm(request, monkeypatch):
    """Hai nhánh, khác nhau DUY NHẤT ở ngữ nghĩa #7."""
    if request.param == "pre-#7":
        monkeypatch.setattr(T, "_proposition_key", _pre_seven_key)
        monkeypatch.setattr(T, "_core", _pre_seven_key)
        monkeypatch.setattr(T, "_folds_into", lambda kind: True)
    return request.param


@pytest.fixture()
def queued(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "door5.db", projection_mode="outbox")
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=TEXT, workspace_id="w1")
    yield memory_os, event
    memory_os.close()


def _holding(memory_os) -> int:
    return sum(1 for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories") if NEEDLE in str(r[0]))


def _forget_and_drain(memory_os, cycles: int = 3):
    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
    worker = worker_for(memory_os)
    return [worker.run_once() for _ in range(cycles)]


# ---------------------------------------------------------------------------

def test_the_control_materialises_in_both_arms(arm, queued):
    """Chứng. Nếu đường bình thường không chạy ở một nhánh thì so sánh vô nghĩa
    — hai nhánh sẽ 'an toàn' vì hai lý do khác nhau."""
    memory_os, _ = queued
    worker = worker_for(memory_os)
    for _ in range(3):
        worker.run_once()
    assert _holding(memory_os) == 1, f"[{arm}] control không materialize"


def test_the_clean_implementation_blocks_in_both_arms(arm, queued):
    """Câu hỏi của Door 5, phát biểu trực tiếp."""
    memory_os, event = queued
    _forget_and_drain(memory_os)

    assert buried(memory_os.memories.conn, event.event_id), (
        f"[{arm}] không bia mộ nào được đặt")
    assert _holding(memory_os) == 0, f"[{arm}] RESURRECTION"


def test_the_mutant_resurrects_in_both_arms(arm, queued, monkeypatch):
    """IGNORE_RECONCILIATION_WORKER_BURIED_CHECK.

    Phải hồi sinh ở **cả hai** nhánh. Nếu nó chỉ hồi sinh ở một nhánh thì hai
    nhánh không tương đương về an toàn, và Door 5 không thể PASS. Nếu nó không
    hồi sinh ở nhánh nào thì ca sạch phía trên chưa chứng minh gì."""
    memory_os, _ = queued
    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

    monkeypatch.setattr(reconciliation_worker, "buried",
                        lambda conn, event_id: False)
    worker = worker_for(memory_os)
    for _ in range(3):
        worker.run_once()

    assert _holding(memory_os) == 1, (
        f"[{arm}] mutant làm mù chốt buried mà nội dung vẫn không quay lại — "
        f"nhánh này chưa chạm tới cơ chế nó định đo")


def test_seven_changes_proposition_grouping_and_nothing_about_burial(arm):
    """#7 đụng vào cái gì, phát biểu thành assertion.

    Hai nhánh phải khác nhau ở nhóm mệnh đề — nếu không thì fixture không thực
    sự bật tắt #7 và ba ca trên so hai bản sao của cùng một thứ."""
    assert_text = "Lương của Phạm Nam là 18 triệu."
    correct_text = ("Đính chính: thông tin trước là sai, lương của Phạm Nam "
                    "là 18 triệu.")
    same_proposition = (T._proposition_key(assert_text)
                        == T._proposition_key(correct_text))
    assert same_proposition, "cùng mệnh đề ở cả hai nhánh — đó là tiền đề"

    folds = T._folds_into(T.CORRECT)
    if arm == "pre-#7":
        assert folds, "nhánh pre-#7 không tái tạo được hành vi gập"
    else:
        assert not folds, "nhánh #7 không áp dụng luật gập mới"
