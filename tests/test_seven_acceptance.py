"""Sáu cửa của #7, viết thành test thay vì thành một dòng trong báo cáo.

Cửa 5 — resurrection qua rebuild — là **NOT_EVALUABLE**, không phải PASS và
không phải FAIL. `replay(dry_run=False)` xếp hàng vào `projection_outbox` và
không có gì trong `bio_agent_os/` tiêu thụ nó, nên đường nguy hiểm không chạm
tới được. Một consumer viết vội để biến xfail thành xanh là làm cái cân theo
đúng quả cân mình muốn đo.

    UNREACHABLE SAFE PATH != VERIFIED SAFETY

Cửa đó không chặn #7 vô thời hạn vì một lý do đo được, không phải vì sốt ruột:
`B2..C1` không đụng một dòng nào của hệ thống forget/replay. Không có đường
nhân quả nào từ #7 tới resurrection, và test đầu tiên dưới đây là thứ chứng
minh điều đó — chứ không phải một câu trong changelog.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.relations import Cardinality, semantics_for

CTX = AccessContext(tenant_id="t1", workspace_id="w1")
_REPO = Path(__file__).resolve().parents[1]

#: Hai đầu của thí nghiệm #7.
B2, C1 = "494d197", "1fc3398"

#: Hệ thống mà #7 phải không được chạm vào, vì cửa 5 chỉ được phép để mở khi
#: không có đường nhân quả nào từ bản vá tới nó.
UNTOUCHABLE = ("forgetting", "tombstone", "projection_engine", "outbox",
               "erase", "replay", "materiali")


def _git(*args: str) -> str:
    """UTF-8 explicitly. The diff being inspected is full of Vietnamese, and a
    check that crashes on the text it is checking proves nothing about it."""
    out = subprocess.run(("git", "-C", str(_REPO)) + args,
                         capture_output=True, timeout=60)
    if out.returncode != 0:
        return ""
    return (out.stdout or b"").decode("utf-8", errors="replace")


@pytest.mark.skipif(not _git("cat-file", "-t", C1).strip(),
                    reason="C1 chưa tồn tại trong kho này")
def test_the_patch_does_not_touch_forget_or_replay_infrastructure():
    """CỬA 5 để mở được là nhờ ca này.

    Nếu #7 có sửa dù một dòng của tombstone/forget/replay thì "chưa đo được
    resurrection" lập tức đổi nghĩa từ *một khoảng trống hạ tầng* thành *một
    rủi ro chưa kiểm chứng*, và #7 phải chờ."""
    changed = [f for f in _git("diff", "--name-only",
                               f"{B2}..{C1}").splitlines() if f.strip()]
    assert changed, "không đọc được diff B2..C1"
    for name in changed:
        for forbidden in UNTOUCHABLE:
            assert forbidden not in name.lower(), (
                f"#7 chạm vào {name} — cửa 5 không còn được phép để mở")

    diff = _git("diff", f"{B2}..{C1}", "--", "bio_agent_os/")
    touched = [line for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))
               and any(word in line.lower() for word in UNTOUCHABLE)]
    assert not touched, f"diff nhắc tới hệ thống bị cấm: {touched[:3]}"


# ---------------------------------------------------------------------------
# CỬA 4b — bất biến cấu trúc của CONFLICT
# ---------------------------------------------------------------------------

ASSERT_A = "Đặng Khanh đang giữ chức trưởng phòng."
CONTRADICT_B = "Theo một nguồn khác, Đặng Khanh đang giữ chức phó giám đốc."
REPEAT_A = "Nhắc lại, Đặng Khanh đang giữ chức trưởng phòng."
CORRECT_A = ("Đính chính: thông tin trước là sai, Đặng Khanh đang giữ chức "
             "trưởng phòng.")
ASK = "Vào ngày 2026-06-15, Đặng Khanh đang giữ chức vụ gì?"
T1, T2, T3 = ("2026-01-10T00:00:00+00:00", "2026-03-10T00:00:00+00:00",
              "2026-05-10T00:00:00+00:00")


def _store(tmp_path: Path, name: str, texts):
    memory_os = MemoryOS(tmp_path / f"{name}.db")
    for text, at in texts:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1",
                                  observed_at=at)
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.9)
    return memory_os


def test_a_real_conflict_is_still_reported(tmp_path):
    """Chứng dương. Nếu ba mutant dưới chết chỉ vì CONFLICT không bao giờ nổ
    nữa thì bất biến đã được cài thành công tắt."""
    memory_os = _store(tmp_path, "real", [(ASSERT_A, T1), (CONTRADICT_B, T2)])
    try:
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert result.conflict is not None
        assert len(result.conflict.claims) >= 2
    finally:
        memory_os.close()


def test_single_claim_conflict_mutant_dies(tmp_path):
    """SINGLE_CLAIM_CONFLICT.

    Một CONFLICT chứa đúng một claim là một claim đã bị xoá khoác áo mâu
    thuẫn — đúng thứ đã xảy ra trước Nhóm A, khi khoảng của claim chính chủ bị
    đóng bằng dấu thời gian tin đồn tới."""
    memory_os = _store(tmp_path, "lone", [(CONTRADICT_B, T1)])
    try:
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert result.conflict is None, (
            "một claim đơn độc vẫn được báo là mâu thuẫn")
    finally:
        memory_os.close()


def test_duplicate_value_conflict_mutant_dies(tmp_path):
    """DUPLICATE_VALUE_CONFLICT. Hai claim cùng giá trị là củng cố, không phải
    bất đồng."""
    memory_os = _store(tmp_path, "dup", [(ASSERT_A, T1), (REPEAT_A, T2)])
    try:
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert result.conflict is None, (
            "nhắc lại cùng một giá trị bị đọc thành mâu thuẫn")
    finally:
        memory_os.close()


def test_coexist_as_conflict_mutant_dies(tmp_path):
    """COEXIST_AS_CONFLICT. Một vị từ giữ nhiều giá trị cùng lúc một cách hợp
    lệ thì hai giá trị khác nhau không phải mâu thuẫn."""
    many = [key for key in ("project", "employer", "city", "phone", "salary",
                            "job_title", "birthday")
            if semantics_for(key).cardinality is Cardinality.MANY]
    if not many:
        pytest.skip("bảng vị từ hiện không có vị từ đa trị nào")
    memory_os = _store(tmp_path, "coexist", [
        ("Đặng Khanh phụ trách dự án Alpha.", T1),
        ("Đặng Khanh phụ trách dự án Beta.", T2)])
    try:
        result = T.answer_temporal(
            memory_os, "Vào ngày 2026-06-15, Đặng Khanh phụ trách dự án nào?",
            context=CTX)
        assert result.conflict is None
    finally:
        memory_os.close()


def test_a_conflict_is_not_permanent(tmp_path):
    """Chứng dương thứ hai, và là điều anh dặn: bất biến không được biến
    CONFLICT thành một trạng thái vĩnh viễn.

        ASSERT A, CONTRADICT B          -> CONFLICT
        rồi CORRECT nói B sai           -> biết lại là A
    """
    memory_os = _store(tmp_path, "resolved",
                       [(ASSERT_A, T1), (CONTRADICT_B, T2), (CORRECT_A, T3)])
    try:
        result = T.answer_temporal(memory_os, ASK, context=CTX)
        assert result.conflict is None, (
            "một đính chính đã ngã ngũ mà mâu thuẫn vẫn được giữ — CONFLICT "
            "đang là trạng thái vĩnh viễn")
        assert "trưởng phòng" in result.answer_text.lower()
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# CỬA 4c — ca Phạm Nam, khoá theo CƠ CHẾ chứ không theo câu trả lời
# ---------------------------------------------------------------------------

NAM_ASSERT = "Lương của Phạm Nam là 18 triệu."
NAM_OTHER = "Từ hôm nay, lương của Phạm Nam là 55 triệu."
NAM_CORRECT = ("Đính chính: thông tin trước là sai, lương của Phạm Nam là "
               "18 triệu.")


def test_a_correction_event_cannot_be_deduplicated_into_the_claim_it_corrects(
        tmp_path):
    """Chuỗi, không phải đáp án.

    Một test chỉ assert "18 triệu" sẽ vẫn xanh nếu ngày mai giá trị đó quay lại
    vì một lý do hoàn toàn khác. Ca này khoá từng mắt:

        cùng proposition identity
        khác lifecycle-event identity
        đính chính sống sót qua khử trùng lặp
        có span sống phủ thời điểm được hỏi
        STATE_AT chọn đúng nó
    """
    assert T._proposition_key(NAM_ASSERT) == T._proposition_key(NAM_CORRECT), (
        "hai câu này phải cùng một mệnh đề — nếu không, ca đã mất hình dạng "
        "gây ra lỗi và không còn kiểm được gì")
    assert T._event_kind(NAM_ASSERT) != T._event_kind(NAM_CORRECT)
    assert T._event_kind(NAM_CORRECT) == T.CORRECT

    memory_os = _store(tmp_path, "nam", [
        (NAM_ASSERT, T1), (NAM_OTHER, T2), (NAM_CORRECT, T3)])
    try:
        spans = T.claim_history(memory_os, subject="Phạm Nam", aspect="salary",
                                predicate="salary", context=CTX)
        corrections = [s for s in spans if s.event_kind == T.CORRECT]
        assert corrections, (
            "đính chính bị gập vào khẳng định mang cùng mệnh đề — đúng lỗi #7")

        when = "2026-06-13T00:00:00+00:00"
        holding = [s for s in spans if s.holds_at(when)]
        assert holding, "không span nào phủ thời điểm được hỏi"
        assert any("18 triệu" in s.content for s in holding), (
            f"giá trị đúng không có span sống tại {when}: "
            f"{[s.content for s in holding]}")

        answer = T.answer_temporal(
            memory_os, "Vào ngày 2026-06-13, Lương của Phạm Nam là bao nhiêu?",
            context=CTX)
        assert "18" in answer.answer_text
    finally:
        memory_os.close()
