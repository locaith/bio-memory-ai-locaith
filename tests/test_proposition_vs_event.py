"""Danh tính mệnh đề ≠ danh tính sự kiện vòng đời.

Năm câu dưới đây mang **cùng một mệnh đề** và là **năm sự kiện khác nhau**:

    Lương của Phạm Nam là 18 triệu.                        ASSERT
    Nhắc lại, lương của Phạm Nam là 18 triệu.              REPEAT
    Đính chính: … sai, lương của Phạm Nam là 18 triệu.     CORRECT
    Từ hôm nay, lương của Phạm Nam là 18 triệu.            SUPERSEDE
    Theo một nguồn khác, lương của Phạm Nam là 18 triệu.   CONFLICT

`_core` cũ strip marker đính chính và nhắc lại — và chỉ hai cái đó — rồi dùng
kết quả làm khoá khử trùng lặp. Nên một câu đính chính có giá trị trùng với một
câu khẳng định trước đó sẽ gập vào chính nó, chỉ cộng `confirmations`, và không
bao giờ mở span. Giá trị đúng khi ấy **không có span sống nào trong toàn store**;
chọn khoảng không tìm thấy gì và câu trả lời rơi xuống một tin đồn. Một cú gập,
ba tầng lỗi.

Hai bất đối xứng đứng sau nó, và cả hai được đóng cùng lúc: marker supersession
và marker "nguồn khác" **chưa từng được strip**, nên cùng một mệnh đề chuẩn hoá
ra hai kết quả khác nhau tuỳ khung câu nó đến.

Bốn mutant, mỗi cái chết bằng một tính chất — không cái nào bằng so chuỗi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")
VALUE = "Lương của Phạm Nam là 18 triệu."
FRAMES = {
    T.ASSERT: VALUE,
    T.REPEAT: "Nhắc lại, lương của Phạm Nam là 18 triệu.",
    T.CORRECT: ("Đính chính: thông tin trước là sai, lương của Phạm Nam là "
                "18 triệu."),
    T.SUPERSEDE: "Từ hôm nay, lương của Phạm Nam là 18 triệu.",
    T.CONFLICT: "Theo một nguồn khác, lương của Phạm Nam là 18 triệu.",
}
T1, T2 = "2026-01-10T00:00:00+00:00", "2026-03-10T00:00:00+00:00"


def _store(tmp_path: Path, name: str, texts):
    memory_os = MemoryOS(tmp_path / f"{name}.db")
    for text, at in texts:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1",
                                  observed_at=at)
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.9)
    return memory_os


def _history(memory_os):
    return T.claim_history(memory_os, subject="Phạm Nam", aspect="salary",
                           predicate="salary", context=CTX)


# ---------------------------------------------------------------------------
# the two layers
# ---------------------------------------------------------------------------

def test_one_proposition_five_events():
    """Trục mệnh đề: tất cả bằng nhau. Trục sự kiện: tất cả khác nhau."""
    keys = {kind: T._proposition_key(text) for kind, text in FRAMES.items()}
    assert len(set(keys.values())) == 1, (
        f"cùng một mệnh đề mà chuẩn hoá ra {len(set(keys.values()))} khoá: "
        f"{keys}")

    kinds = {kind: T._event_kind(text) for kind, text in FRAMES.items()}
    assert kinds == {k: k for k in FRAMES}, kinds


def test_the_asymmetry_is_closed():
    """Defect phụ: `_core` strip correction/restatement mà không strip
    supersession. Sửa riêng correction sẽ để nguyên bất đối xứng."""
    assert (T._proposition_key(FRAMES[T.SUPERSEDE])
            == T._proposition_key(FRAMES[T.REPEAT])
            == T._proposition_key(FRAMES[T.CONFLICT]))


# ---------------------------------------------------------------------------
# the four mutants
# ---------------------------------------------------------------------------

def test_correction_does_not_collapse_into_the_claim_it_corrects(tmp_path):
    """CORRECTION_COLLAPSES_INTO_ASSERT.

    Tính chất: sau một đính chính, giá trị được đính chính phải **tồn tại như
    một span sống**. Không assert chuỗi — assert rằng lịch sử có chỗ cho nó."""
    memory_os = _store(tmp_path, "correct", [
        ("Lương của Phạm Nam là 55 triệu.", T1),
        (FRAMES[T.CORRECT], T2)])
    try:
        spans = _history(memory_os)
        live = [s for s in spans if not s.retracted_by]
        assert any(s.event_kind == T.CORRECT for s in spans), (
            "câu đính chính không mở span nào — nó đã gập vào chính claim nó sửa")
        assert live, "đính chính xong không còn gì sống"
        assert any("18 triệu" in s.content for s in live)
    finally:
        memory_os.close()


def test_a_correction_matching_an_earlier_value_still_opens_a_span(tmp_path):
    """Ca gây ra #7: giá trị của đính chính TRÙNG với một khẳng định trước đó.

    Đây là chỗ khoá-theo-văn-bản không thể phân biệt, và là lý do phải có hai
    lớp chứ không phải một regex khéo hơn."""
    memory_os = _store(tmp_path, "same_value", [
        (VALUE, T1),
        ("Lương của Phạm Nam là 55 triệu.", T2),
        (FRAMES[T.CORRECT], "2026-05-10T00:00:00+00:00")])
    try:
        spans = _history(memory_os)
        corrections = [s for s in spans if s.event_kind == T.CORRECT]
        assert corrections, (
            "đính chính mang cùng mệnh đề với câu đầu nên bị gập — đúng lỗi #7")
        assert any(s.holds_at("2026-06-01T00:00:00+00:00")
                   and "18 triệu" in s.content for s in spans), (
            "giá trị được khôi phục không có span sống nào phủ thời điểm sau đó")
    finally:
        memory_os.close()


def test_repeat_does_not_open_a_new_span(tmp_path):
    """REPEAT_OPENS_NEW_SPAN.

    Nửa còn lại. Nếu bản vá làm mọi thứ mở span thì nó không tách hai lớp, nó
    chỉ tắt việc khử trùng lặp — và mỗi lần nhắc lại sẽ phát minh một biên
    khoảng."""
    memory_os = _store(tmp_path, "repeat", [(VALUE, T1),
                                            (FRAMES[T.REPEAT], T2)])
    try:
        spans = _history(memory_os)
        assert len(spans) == 1, (
            f"nhắc lại mở thêm span: {[s.content for s in spans]}")
        assert spans[0].confirmations == 1
        assert spans[0].valid_to is None, (
            "một lần nhắc lại đã đóng khoảng của chính claim nó nhắc lại")
    finally:
        memory_os.close()


def test_supersede_does_not_collapse_into_assert(tmp_path):
    """SUPERSEDE_COLLAPSES_INTO_ASSERT."""
    memory_os = _store(tmp_path, "supersede", [(VALUE, T1),
                                               (FRAMES[T.SUPERSEDE], T2)])
    try:
        spans = _history(memory_os)
        assert any(s.event_kind == T.SUPERSEDE for s in spans), (
            "một tuyên bố có mốc thời gian bị gập thành một lần nhắc lại")
        assert len(spans) == 2
    finally:
        memory_os.close()


def test_conflict_does_not_collapse_into_assert(tmp_path):
    """CONFLICT_COLLAPSES_INTO_ASSERT.

    Nếu một nguồn cạnh tranh gập vào claim nó tranh chấp thì sự bất đồng biến
    mất khỏi lịch sử — và biến mất một cách im lặng, đúng thứ bất biến khoảng
    tồn tại để chặn."""
    memory_os = _store(tmp_path, "conflict", [(VALUE, T1),
                                              (FRAMES[T.CONFLICT], T2)])
    try:
        spans = _history(memory_os)
        disputed = [s for s in spans if s.event_kind == T.CONFLICT]
        assert disputed, "tranh chấp bị gập vào claim nó tranh chấp"
        assert disputed[0].disputed is True
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# and the guard against fixing this by turning dedup off
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [T.CORRECT, T.SUPERSEDE, T.CONFLICT])
def test_only_the_two_that_assert_nothing_new_may_fold(kind):
    assert not T._folds_into(kind)


@pytest.mark.parametrize("kind", [T.ASSERT, T.REPEAT])
def test_a_bare_reassertion_is_still_agreement(kind):
    assert T._folds_into(kind)
