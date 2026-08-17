"""Một quan hệ chưa ngã ngũ không được đóng khoảng hiệu lực của claim nào.

Bốn trong bảy lỗi `historical` còn lại đều là một hình dạng. Ba thành phần đồng ý
rằng sự thật đang bị tranh chấp:

    `lifetime_world`      CONTRADICT cố ý không đụng ledger
    `classify_relation`   "Theo một nguồn khác" -> Relation.CONFLICT
    `lifecycle_runtime`   không có nhánh CONFLICT, và nói rõ là cố ý
    database              valid_to = NULL, không cửa sổ nào được ghi

Thành phần thứ tư — `claim_history`, `zip(live, live[1:])` — suy khoảng từ **thứ
tự đến** và lấy thời điểm tin đồn làm biên. Nó lặng lẽ chọn phe.

Đây không phải tuning. Đây là **quyền sở hữu ngữ nghĩa**:

    tầng write/lifecycle  SỞ HỮU chuyển đổi hiệu lực
    tầng read             TIÊU THỤ hiệu lực
    tầng read             KHÔNG ĐƯỢC phát minh ra một lần đóng

Điều bất biến này **không** nói "A thắng mãi mãi". Nó chỉ nói CONFLICT không có
quyền tự đóng A. Trả lời truy vấn thế nào trong vùng tranh chấp là một quyết định
khác và không được quyết ở đây.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.relations import Relation

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

ASSERT = "Bùi Cường đang giữ chức trưởng phòng."
CONTRADICT = "Theo một nguồn khác, Bùi Cường đang giữ chức phó giám đốc."
SUPERSEDE = "Từ hôm nay, Bùi Cường đang giữ chức giám đốc."


def _remember(memory_os: MemoryOS, text: str, *, at: str) -> str:
    """Same shape as `tests/test_temporal_operator._remember`. Observation time
    is set on the event; the memory inherits it."""
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1", observed_at=at)
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


@pytest.fixture()
def disputed(tmp_path: Path):
    """A claim, then somebody else says otherwise. The world did not change."""
    memory_os = MemoryOS(tmp_path / "disputed.db")
    _remember(memory_os, ASSERT, at="2026-01-10T00:00:00+00:00")
    _remember(memory_os, CONTRADICT, at="2026-03-10T00:00:00+00:00")
    yield memory_os
    memory_os.close()


def _history(memory_os, subject="Bùi Cường"):
    return T.claim_history(memory_os, subject=subject, aspect="job_title",
                           predicate="job_title", context=CTX)


def _span_for(spans, needle):
    matches = [s for s in spans if needle in s.content]
    assert matches, f"không thấy span chứa {needle!r} trong {len(spans)} span"
    return matches[0]


# ---------------------------------------------------------------------------
# the invariant
# ---------------------------------------------------------------------------

def test_the_relation_enum_already_knows_what_unresolved_means():
    """The predicate this invariant needs was written months ago and never
    asked. `claim_history` derives everything itself instead."""
    assert not Relation.CONFLICT.resolves
    assert not Relation.UNKNOWN.resolves
    assert Relation.SUPERSEDE.resolves
    assert Relation.CORRECT.resolves


def test_a_contradiction_does_not_close_the_claim_it_disputes(disputed):
    """UNRESOLVED_CONFLICT_CLOSES_PREVIOUS_INTERVAL dies here.

    `valid_to` on the asserted claim must not be synthesised from the moment
    the contradiction arrived. Nothing established that the claim stopped
    holding — somebody merely disagreed."""
    spans = _history(disputed)
    asserted = _span_for(spans, "trưởng phòng")

    assert asserted.valid_to != "2026-03-10T00:00:00+00:00", (
        "khoảng hiệu lực bị đóng đúng lúc tin đồn tới — read path tự phát minh "
        "ra một lần đóng mà không tầng nào cho phép")
    assert asserted.valid_to is None, (
        f"không gì đóng claim này, nhưng valid_to = {asserted.valid_to!r}")


def test_the_disputed_claim_still_holds_after_the_contradiction(disputed):
    """The consequence that matters to a user: asking about a date after the
    contradiction must not silently return the other value."""
    asserted = _span_for(_history(disputed), "trưởng phòng")
    assert asserted.holds_at("2026-06-01T00:00:00+00:00"), (
        "claim bị thu hồi bởi một bất đồng, không phải bởi một chuyển đổi")


def test_the_contradiction_is_not_deleted_either(disputed):
    """Somebody did say it. The invariant is about closure, not about hiding
    the dispute — a history that drops the contradiction is a history that has
    picked the other side just as silently."""
    spans = _history(disputed)
    assert _span_for(spans, "phó giám đốc") is not None
    assert len(spans) == 2


def test_a_real_supersession_still_closes(tmp_path):
    """The half that must not break. SUPERSEDE resolves, so it closes — and if
    this passes only because nothing closes anything any more, the invariant
    has been implemented as an off switch."""
    memory_os = MemoryOS(tmp_path / "superseded.db")
    try:
        _remember(memory_os, ASSERT, at="2026-01-10T00:00:00+00:00")
        _remember(memory_os, SUPERSEDE, at="2026-05-10T00:00:00+00:00")

        asserted = _span_for(_history(memory_os), "trưởng phòng")
        assert asserted.valid_to == "2026-05-10T00:00:00+00:00"
        assert asserted.kind == "superseded"
    finally:
        memory_os.close()


def test_a_contradiction_between_two_claims_closes_neither(tmp_path):
    """Assert, dispute, then a genuine supersession. The dispute in the middle
    must not become the boundary, and the supersession must still be one — so
    the asserted claim closes at the *supersession*, skipping the rumour."""
    memory_os = MemoryOS(tmp_path / "three.db")
    try:
        _remember(memory_os, ASSERT, at="2026-01-10T00:00:00+00:00")
        _remember(memory_os, CONTRADICT, at="2026-03-10T00:00:00+00:00")
        _remember(memory_os, SUPERSEDE, at="2026-05-10T00:00:00+00:00")

        spans = _history(memory_os)
        asserted = _span_for(spans, "trưởng phòng")
        assert asserted.valid_to == "2026-05-10T00:00:00+00:00", (
            f"đóng tại {asserted.valid_to!r} — nếu đó là 03-10 thì tin đồn vẫn "
            f"đang làm biên")
        assert asserted.holds_at("2026-04-01T00:00:00+00:00"), (
            "khoảng giữa tranh chấp và chuyển đổi thật vẫn thuộc về claim cũ")
    finally:
        memory_os.close()


def test_the_invariant_is_not_a_licence_for_the_old_claim_to_win_forever(
        tmp_path):
    """Anh's caution, written as a test.

    The invariant says a conflict may not close A. It does not say A is true
    for ever, and it does not decide how the conflict region is answered. What
    it must guarantee is only this: the closure, when it comes, comes from
    something that resolves."""
    memory_os = MemoryOS(tmp_path / "forever.db")
    try:
        _remember(memory_os, ASSERT, at="2026-01-10T00:00:00+00:00")
        _remember(memory_os, CONTRADICT, at="2026-03-10T00:00:00+00:00")
        _remember(memory_os, SUPERSEDE, at="2026-05-10T00:00:00+00:00")

        spans = _history(memory_os)
        asserted = _span_for(spans, "trưởng phòng")
        assert not asserted.holds_at("2026-09-01T00:00:00+00:00"), (
            "một chuyển đổi thật đã xảy ra và claim cũ vẫn giữ — bất biến đã "
            "bị cài thành công tắt")
    finally:
        memory_os.close()
