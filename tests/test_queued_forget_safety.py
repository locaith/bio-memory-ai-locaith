"""Một lệnh quên phải đi nhanh hơn ký ức trên mọi representation của nó.

    A DELETE OF CURRENT STATE != A REVOCATION OF FUTURE STATE

`forget` resolve từ tầng phục vụ. Một ký ức logic chỉ tồn tại dưới dạng việc
xếp hàng thì không có dòng nào ở đó, nên `_resolve` trả rỗng và hàm thoát sớm
với ghi chú *"không tìm thấy ký ức nào khớp"* — về đúng một ký ức mà worker sắp
ghi ra. Tái hiện tất định trước khi sửa:

    observe()          outbox pending, memories 0, tombstones 0
    forget_derived()   verified_clean=False, tombstones 0, note="không tìm thấy"
    worker.run_once()  completed=1, memories 1        <- quay lại, nguyên văn

Verifier **đã nói thật** suốt quá trình: nó báo `verified_clean=False` vì nội
dung vẫn tìm thấy được. Thứ thiếu không phải hiểu biết, mà là tầm với.

Danh tính nối mọi representation là **event**, và nó có mặt trước, trong và sau
khi materialize. Bia mộ khoá theo `event_id` với `memory_id` nullable — nên bia
mộ cho một ký ức chưa materialize là hình dạng schema vốn đã đỡ được.

**Không xoá dòng khỏi hàng đợi.** Một job biến mất khỏi outbox là một quyết định
không audit nào nhìn thấy, và bất kỳ producer nào enqueue lại sẽ mang nội dung
trở về. Bia mộ mới là sự thật bền.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting, tombstones
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.reconciliation_worker import worker_for

SECRET = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"
SUBJECT = "Hoàng Yến"


@pytest.fixture()
def queued(tmp_path: Path):
    """Một ký ức tồn tại CHỈ dưới dạng việc xếp hàng.

    Không gọi `remember()`: nó materialize ngay và che toàn bộ hình dạng lỗi —
    đúng thứ đã làm lần tái hiện đầu tiên của tôi báo "không tái hiện được"."""
    memory_os = MemoryOS(tmp_path / "queued.db", projection_mode="outbox")
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=SECRET, workspace_id="w1")
    yield memory_os, event
    memory_os.close()


def _memories(memory_os) -> list[str]:
    return [str(r[0]) for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories")]


def _jobs(memory_os) -> list[tuple]:
    try:
        return list(memory_os.memories.conn.execute(
            "SELECT event_id, status FROM projection_outbox"))
    except sqlite3.OperationalError:
        return []


def _drain(memory_os, cycles: int = 3):
    worker = worker_for(memory_os)
    return [worker.run_once() for _ in range(cycles)]


def _assert_probe_valid(memory_os, stage: str) -> None:
    """Ký ức phải thực sự đang ở hàng đợi, chưa materialize.

    Nếu điều kiện này hỏng thì mọi 'không có resurrection' bên dưới chỉ chứng
    minh rằng không có gì để hồi sinh."""
    assert not _memories(memory_os), (
        f"[{stage}] PROBE INVALID — đã materialize rồi, không còn là hàng đợi")
    assert _jobs(memory_os), f"[{stage}] PROBE INVALID — không job nào đang chờ"


# ---------------------------------------------------------------------------
# E — chứng. Không có nó thì bốn ca kia không chứng minh gì.
# ---------------------------------------------------------------------------

def test_e_control_without_forget_materialises(queued):
    """Bắt buộc chạy. Một ma trận an toàn mà đường bình thường cũng không chạy
    được thì mọi ô đều xanh vì cùng một lý do sai."""
    memory_os, _ = queued
    _assert_probe_valid(memory_os, "control")

    _drain(memory_os)

    assert any(NEEDLE in m for m in _memories(memory_os)), (
        "MA TRẬN VÔ HIỆU — không forget mà cũng không materialize được")


# ---------------------------------------------------------------------------
# A–D
# ---------------------------------------------------------------------------

def test_a_enqueue_then_forget_then_drain(queued):
    memory_os, event = queued
    _assert_probe_valid(memory_os, "A")

    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

    assert tombstones.buried(memory_os.memories.conn, event.event_id), (
        "không bia mộ nào được đặt cho sự kiện đang chờ chiếu")
    _drain(memory_os)
    assert not [m for m in _memories(memory_os) if NEEDLE in m], "RESURRECTION"


def test_a_the_tombstone_names_an_event_with_no_memory(queued):
    """Hình dạng schema mà bản vá dựa vào: `memory_id` NULL."""
    memory_os, event = queued
    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

    (recorded,) = tombstones.tombstones(memory_os.memories.conn,
                                        event_id=event.event_id)
    assert recorded["memory_id"] is None
    assert recorded["event_id"] == event.event_id


def test_b_forget_then_replay_never_materialises(queued):
    """Bia mộ đặt trước, rồi mới enqueue lại. Chốt tại-enqueue phải chặn."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    memory_os, event = queued
    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

    ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)
    _drain(memory_os)

    assert not [m for m in _memories(memory_os) if NEEDLE in m], "RESURRECTION"


def test_c_forget_while_worker_holds_the_lease(queued):
    """Worker đã claim job, forget đến giữa chừng, worker chạy tiếp.

    Bia mộ đáp xuống như một sự thật bền; chốt ngay-trước-khi-ghi của worker là
    thứ chặn job đó. Hàng đợi sống lâu hơn quyết định đã tạo ra nó."""
    memory_os, event = queued
    worker = worker_for(memory_os)
    claimed = worker.outbox.claim(worker_id="test-lease")
    assert claimed, "không claim được job — ca này chưa đo được gì"

    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
    assert tombstones.buried(memory_os.memories.conn, event.event_id)

    _drain(memory_os, cycles=4)
    assert not [m for m in _memories(memory_os) if NEEDLE in m], "RESURRECTION"


def test_d_duplicate_jobs_neither_materialises(queued):
    """Hai job tương đương cho cùng một sự kiện. Bia mộ khoá theo event nên nó
    phủ cả hai — một cách sửa khoá theo job sẽ chỉ chặn được một."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    memory_os, event = queued
    ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)

    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
    _drain(memory_os, cycles=5)

    assert not [m for m in _memories(memory_os) if NEEDLE in m], "RESURRECTION"


# ---------------------------------------------------------------------------
# Hai mutant. Mỗi cái phải THỰC SỰ đi qua đường nguy hiểm.
# ---------------------------------------------------------------------------

def test_mutant_ignore_reconciliation_worker_buried_check(queued, monkeypatch):
    """IGNORE_RECONCILIATION_WORKER_BURIED_CHECK.

    Chốt tại-thời-điểm-ghi. Audit đã chứng minh nó chịu tải; ca này giữ điều đó
    đúng. Nếu mutant không hồi sinh được gì thì bốn ca trên chưa chạm tới cơ chế
    chúng định đo."""
    from bio_agent_os.cognitive import reconciliation_worker as rw

    memory_os, event = queued
    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

    monkeypatch.setattr(rw, "buried", lambda conn, event_id: False)
    _drain(memory_os, cycles=3)

    assert [m for m in _memories(memory_os) if NEEDLE in m], (
        "mutant làm mù chốt buried mà nội dung vẫn không quay lại — ma trận "
        "chưa đi qua đường nguy hiểm, nên 'resurrection = 0' chưa chứng minh gì")


def test_mutant_forget_ignores_pending_projection(queued, monkeypatch):
    """FORGET_IGNORES_PENDING_PROJECTION — chính bug này, tái sinh.

    Đây là code đang ship trước bản vá."""
    memory_os, event = queued
    monkeypatch.setattr(forgetting, "_events_awaiting_projection",
                        lambda *a, **k: set())

    forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
    assert not tombstones.buried(memory_os.memories.conn, event.event_id)

    _drain(memory_os, cycles=3)
    assert [m for m in _memories(memory_os) if NEEDLE in m], (
        "mutant không tái tạo được lỗi — bản vá chưa chứng minh được nó sửa gì")


# ---------------------------------------------------------------------------
# và nửa phải không được vỡ
# ---------------------------------------------------------------------------

def test_forget_still_reaches_a_materialised_memory(tmp_path):
    """Bản vá mở rộng tầm với, không được thay thế tầm với cũ."""
    memory_os = MemoryOS(tmp_path / "materialised.db")
    try:
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=SECRET, workspace_id="w1")
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=SECRET, confidence=0.9)
        assert any(NEEDLE in m for m in _memories(memory_os))

        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

        assert not [m for m in _memories(memory_os) if NEEDLE in m]
    finally:
        memory_os.close()


def test_a_store_with_nothing_queued_is_unaffected(tmp_path):
    """Không outbox, không job: hàm phải trả về đúng như trước và không nổ."""
    memory_os = MemoryOS(tmp_path / "empty.db")
    try:
        report = forgetting.forget_derived(memory_os, subject="Không Ai",
                                           needle="không có gì")
        assert "không tìm thấy" in (report.note or "")
    finally:
        memory_os.close()
