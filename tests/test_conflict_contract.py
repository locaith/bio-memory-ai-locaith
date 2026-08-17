"""CONFLICT là một kết quả có cấu trúc, không phải một câu văn dài hơn.

Bất biến khoảng (`test_unresolved_conflict_interval.py`) làm cho hai claim cùng
còn hiệu lực. Hệ quả là `answer_temporal` bắt đầu trả về hai câu nối nhau — đúng
hướng, nhưng là **side effect**: không có kiểu dữ liệu, không có provenance,
không có gì để caller rẽ nhánh, và không có gì phân biệt "hai nguồn mâu thuẫn"
với "một câu trả lời dài".

Ở đây nó thành hợp đồng.

CONFLICT **không phải**:

    claim cũ        chọn nó là đúng cái quyết định im lặng cũ, chỉ đổi chiều
    claim mới       thứ tự đến không phải bằng chứng
    lời từ chối     "không biết" vứt đi hai sự thật hệ thống đang giữ, và không
                    phân biệt được với việc không có ký ức nào

Và `world_truth_known = False` là **cố ý**: ledger có thể đang giữ một giá trị,
hệ thống không nhìn thấy được cái nào. Đó chính là chỗ tách — một hệ thống có
thể **sai theo world-truth mà đúng theo epistemic**, và hai thứ đó phải chấm
riêng, nếu không cái thứ hai sẽ bị tối ưu mất.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

ASSERT = "Đặng Khanh đang giữ chức trưởng phòng."
CONTRADICT = "Theo một nguồn khác, Đặng Khanh đang giữ chức phó giám đốc."
SUPERSEDE = "Từ hôm nay, Đặng Khanh đang giữ chức giám đốc."
REPEAT = "Nhắc lại, Đặng Khanh đang giữ chức trưởng phòng."

T1, T2, T3 = ("2026-01-10T00:00:00+00:00", "2026-02-10T00:00:00+00:00",
              "2026-04-10T00:00:00+00:00")
ASK = "Vào ngày 2026-03-15, Đặng Khanh đang giữ chức vụ gì?"


def _store(tmp_path: Path, name: str, texts):
    memory_os = MemoryOS(tmp_path / f"{name}.db")
    for text, at in texts:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1",
                                  observed_at=at)
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.9)
    return memory_os


@pytest.fixture()
def contested(tmp_path: Path):
    memory_os = _store(tmp_path, "contested", [(ASSERT, T1), (CONTRADICT, T2)])
    T.reset_execution()
    yield memory_os
    memory_os.close()


# ---------------------------------------------------------------------------
# the contract
# ---------------------------------------------------------------------------

def test_a_contested_slot_returns_a_conflict_object(contested):
    result = T.answer_temporal(contested, ASK, context=CTX)

    assert result.executed
    assert result.conflict is not None, (
        "hai claim không tương thích cùng còn hiệu lực mà vẫn trả về một giá "
        "trị — hợp đồng chưa chạy")
    assert result.conflict.as_dict()["state"] == "CONFLICT"


def test_the_conflict_carries_both_claims_and_their_provenance(contested):
    conflict = T.answer_temporal(contested, ASK, context=CTX).conflict

    joined = " ".join(c.content for c in conflict.claims)
    assert "trưởng phòng" in joined and "phó giám đốc" in joined
    assert len(conflict.provenance) == len(conflict.claims) == 2
    assert conflict.predicate == "job_title"
    assert conflict.reason


def test_the_conflict_does_not_claim_to_know_world_truth(contested):
    """Ledger có thể đang giữ một giá trị. Hệ thống không thấy cái nào."""
    assert T.answer_temporal(contested, ASK,
                             context=CTX).conflict.world_truth_known is False


def test_it_picks_neither_side(contested):
    """Chọn claim cũ và chọn tin đồn đều là cùng một quyết định im lặng."""
    text = T.answer_temporal(contested, ASK, context=CTX).answer_text
    assert "trưởng phòng" in text and "phó giám đốc" in text
    assert "mâu thuẫn" in text


def test_it_is_not_a_refusal(contested):
    """Từ chối vứt đi hai sự thật hệ thống đang giữ, và không phân biệt được
    với việc không có ký ức nào."""
    result = T.answer_temporal(contested, ASK, context=CTX)
    assert result.executed
    assert not result.stage_failed
    assert result.conflict.claims


def test_the_rendering_is_deterministic(contested):
    """Không có model nào ở giữa một trạng thái epistemic đã biết chính xác và
    câu mô tả nó."""
    a = T.answer_temporal(contested, ASK, context=CTX).answer_text
    b = T.answer_temporal(contested, ASK, context=CTX).answer_text
    assert a == b


def test_the_counter_fires(contested):
    T.reset_execution()
    T.answer_temporal(contested, ASK, context=CTX)
    report = T.execution_report()
    assert report["conflict_detected"] >= 1
    assert report["silent_conflict_resolution"] == 0


# ---------------------------------------------------------------------------
# and the three shapes that are NOT a conflict
# ---------------------------------------------------------------------------

def test_a_lone_disputed_claim_is_not_a_silently_resolved_conflict(tmp_path):
    """Cái bẫy mà chính counter này đã rơi vào một lần.

    Bản đầu suy vi phạm từ *span nào được chọn*, nên nó nổ khi chỉ có một claim
    "theo một nguồn khác" đơn độc — không có gì để mâu thuẫn, không có gì bị
    quyết ngầm. Integration Gate báo `silent_conflict_resolution = 1` cho một
    sự kiện chưa từng xảy ra.

    Một metric mục-tiêu-0 mà kêu oan sẽ bị bỏ qua ngay lần đầu, rồi đúng ngày
    nó nói thật thì không còn ai tin. Nên nó đo thẳng tính chất trên **kết
    quả**: không khoảng nào được kết thúc đúng chỗ một claim tranh chấp bắt
    đầu."""
    memory_os = _store(tmp_path, "lone", [(CONTRADICT, T1)])
    try:
        T.reset_execution()
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert "phó giám đốc" in result.answer_text
        assert T.execution_report()["silent_conflict_resolution"] == 0
        assert T.execution_report()["conflict_detected"] == 0
    finally:
        memory_os.close()


def test_the_audit_fires_when_the_mutant_comes_back(contested, monkeypatch):
    """UNRESOLVED_CONFLICT_CLOSES_PREVIOUS_INTERVAL, tái sinh.

    Nếu counter không nổ ở đây thì nó là code chết, và một bất biến không có
    người canh là một bất biến sẽ lặng lẽ mất."""
    real = T._closers
    monkeypatch.setattr(T, "_closers", lambda live: list(live))
    T.reset_execution()
    T.answer_temporal(contested, ASK, context=CTX)
    monkeypatch.setattr(T, "_closers", real)

    assert T.execution_report()["silent_conflict_resolution"] >= 1, (
        "mutant sống lại mà không ai báo — counter là code chết")


def test_the_audit_is_blind_if_disputation_detection_breaks(contested,
                                                           monkeypatch):
    """Giới hạn của chính bộ canh, ghi lại chứ không giấu.

    Audit hỏi "có khoảng nào kết thúc đúng chỗ một claim tranh chấp bắt đầu
    không". Nếu `_is_disputed` ngừng nhận ra tranh chấp thì không claim nào
    tranh chấp, tập rỗng, và audit im lặng — đúng lúc bất biến đã mất.

    Nó được canh bởi thứ khác: `_is_disputed` đọc chính
    `ALTERNATIVE_SOURCE_MARKERS` mà `classify_relation` đọc, và
    `test_the_relation_enum_already_knows_what_unresolved_means` giữ đầu kia.
    Một bộ canh không tự canh được mình; điều phải tránh là tưởng nó có."""
    monkeypatch.setattr(T, "_is_disputed", lambda content: False)
    T.reset_execution()
    T.answer_temporal(contested, ASK, context=CTX)

    assert T.execution_report()["silent_conflict_resolution"] == 0, (
        "nếu ca này bắt đầu nổ thì audit đã mạnh hơn tài liệu này — sửa lại "
        "tài liệu, đừng sửa test")


def test_a_restatement_is_agreement_not_conflict(tmp_path):
    """`_core` đã biết hai câu này là một claim. Gọi nó là mâu thuẫn thì
    `false_conflict_rate` khác 0 ngay từ ca dễ nhất."""
    memory_os = _store(tmp_path, "repeat", [(ASSERT, T1), (REPEAT, T2)])
    try:
        T.reset_execution()
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert result.conflict is None
        assert T.execution_report()["conflict_detected"] == 0
    finally:
        memory_os.close()


def test_a_resolved_supersession_is_not_conflict(tmp_path):
    """Một quan hệ CÓ ngã ngũ thì đóng khoảng, nên tại thời điểm hỏi chỉ còn
    một claim. Bất biến không biến mọi thứ thành tranh chấp."""
    memory_os = _store(tmp_path, "superseded",
                       [(ASSERT, T1), (SUPERSEDE, T2)])
    try:
        T.reset_execution()
        result = T.answer_temporal(
            memory_os, "Vào ngày 2026-03-15, Đặng Khanh đang giữ chức vụ gì?",
            context=CTX)
        assert result.conflict is None
        assert "giám đốc" in result.answer_text
    finally:
        memory_os.close()


def test_a_belief_question_is_never_a_conflict(contested):
    """"Lúc đó anh nghĩ gì" hỏi hệ thống đã giữ gì, và nó giữ một thứ tại một
    thời điểm theo đúng cấu tạo."""
    result = T.answer_temporal(
        contested, "Trước đây hệ thống nghĩ Đặng Khanh giữ chức gì?",
        context=CTX)
    if result.executed:
        assert result.conflict is None


def test_a_many_valued_predicate_is_not_a_conflict(tmp_path):
    """`project` giữ nhiều giá trị cùng lúc một cách hợp lệ. Gọi đó là mâu
    thuẫn là dương tính giả, và là cách nhanh nhất để biến một hợp đồng đúng
    thành phiền nhiễu."""
    from bio_agent_os.cognitive.relations import Cardinality, semantics_for

    if semantics_for("project").cardinality is not Cardinality.MANY:
        pytest.skip("'project' không phải predicate đa trị trong bảng hiện tại")

    memory_os = _store(tmp_path, "many", [
        ("Đặng Khanh phụ trách dự án Alpha.", T1),
        ("Đặng Khanh phụ trách dự án Beta.", T2)])
    try:
        result = T.answer_temporal(
            memory_os, "Vào ngày 2026-03-15, Đặng Khanh phụ trách dự án nào?",
            context=CTX)
        assert result.conflict is None
    finally:
        memory_os.close()
