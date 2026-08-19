"""Bằng chứng đã quên không được sống lại — và probe phải chứng minh được là nó
thực sự đang đo điều đó.

Probe đầu tiên cho câu Lê Sơn **vô hiệu**, và nó vô hiệu một cách rất dễ đọc
nhầm thành thành công: nó gọi `forget_derived(subject=…, needle=…)` với một
needle rộng, xoá sạch mọi thứ về người đó, rồi báo `spans = 0` ở **cả hai
nhánh** và `bằng chứng sống lại = 0`. Không có gì sống lại vì không còn gì để
sống lại.

    Một cú forget quá rộng và một hệ thống quên đúng cách cho ra cùng một con số.

Nên probe này có ba claim chứ không phải một, và hai trong ba là **chứng**:

    A  đúng thứ cần quên, xoá theo `memory_id` chứ không theo chuỗi
    B  cùng chủ thể, khác vị từ  — BẮT BUỘC sống
    C  chủ thể khác, giá trị tương tự — BẮT BUỘC sống

Hai lớp hỏng khác nhau, và gộp chúng lại là cách probe cũ đã nói dối:

    B hoặc C chết  ->  FORGET SCOPE INVALID, không phải "không có resurrection"
    A quay lại     ->  RESURRECTION
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.projection_intent import MemoryProjectionIntent

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

A_TEXT = "Lê Sơn đang giữ chức nhân viên kinh doanh."
B_TEXT = "Lê Sơn sống ở Hà Nội."
C_TEXT = "Phạm Vy đang giữ chức nhân viên kinh doanh."
#: Sau A: một khẳng định mới và một đính chính mang cùng mệnh đề — đúng hình
#: dạng mà #7 tồn tại để xử lý, nên probe chạy trên thế giới thật của nó.
D_TEXT = "Lê Sơn đang giữ chức trưởng nhóm."
E_TEXT = ("Đính chính: thông tin trước là sai, Lê Sơn đang giữ chức "
          "trưởng nhóm.")


@pytest.fixture()
def world(tmp_path: Path):
    """Thế giới mà projection THẬT SỰ được nợ.

    Trước RC-0 fixture này ghi event không kèm quyết định nào, nên replay xếp
    hàng mọi thứ và không gì vật chất hoá — hai test rebuild xanh vì cơ chế
    đứng yên. Giờ event mang intent và store chạy OUTBOX, nên đường phục hồi
    có thật thứ để dựng lại; nếu tombstone hở, A SẼ quay lại."""
    memory_os = MemoryOS(tmp_path / "resurrect.db", projection_mode="outbox")
    ids = {}
    for label, text, at in (
            ("A", A_TEXT, "2026-01-10T00:00:00+00:00"),
            ("B", B_TEXT, "2026-01-11T00:00:00+00:00"),
            ("C", C_TEXT, "2026-01-12T00:00:00+00:00"),
            ("D", D_TEXT, "2026-03-10T00:00:00+00:00"),
            ("E", E_TEXT, "2026-05-10T00:00:00+00:00")):
        event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                                  content=text, workspace_id="w1",
                                  observed_at=at,
                                  projection_intent=MemoryProjectionIntent(
                                      memory_type=MemoryType.SEMANTIC.value,
                                      confidence=0.9))
        ids[label] = memory_os.remember(
            event=event, memory_type=MemoryType.SEMANTIC, content=text,
            confidence=0.9).memory_id
    yield memory_os, ids
    memory_os.close()


def _present(memory_os) -> set[str]:
    return {str(row[0]) for row in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories")}


def _assert_controls_alive(memory_os, stage: str) -> None:
    """B và C là chứng. Nếu chúng chết thì phép đo hỏng, không phải hệ hỏng."""
    present = _present(memory_os)
    assert B_TEXT in present, (
        f"[{stage}] PROBE INVALID — chứng B (cùng chủ thể, khác vị từ) đã chết "
        f"theo A. Đây là forget quá rộng, không phải 'không có resurrection'.")
    assert C_TEXT in present, (
        f"[{stage}] PROBE INVALID — chứng C (chủ thể khác, giá trị tương tự) "
        f"đã chết theo A.")


def test_the_probe_is_valid_before_it_measures_anything(world):
    """Điều kiện tiên quyết, tách thành test riêng: nếu ca này đỏ thì mọi kết
    luận về resurrection bên dưới đều vô nghĩa."""
    memory_os, ids = world
    present = _present(memory_os)
    assert {A_TEXT, B_TEXT, C_TEXT} <= present

    forgetting.forget_derived(memory_os, memory_id=ids["A"])

    present = _present(memory_os)
    assert A_TEXT not in present, "A chưa bị xoá — chưa đo được gì"
    _assert_controls_alive(memory_os, "sau forget")


#: Lớp hỏng thứ ba, khác cả hai lớp đã đặt tên.
#:
#:     B hoặc C chết   -> FORGET SCOPE INVALID
#:     A quay lại      -> RESURRECTION
#:     cơ chế không chạm tới được  -> cái này
#:
#: `ProjectionReplayEngine.replay(dry_run=False)` chỉ **xếp hàng** vào
#: `projection_outbox` (`enqueued=5, skipped_tombstoned=0`). Không có gì trong
#: `bio_agent_os/` tiêu thụ outbox đó để dựng lại memory, nên A không thể quay
#: lại — kể cả khi tombstone bị bỏ qua hoàn toàn.
#:
#: Hệ quả phải nói thẳng: hai test rebuild dưới đây **chưa chứng minh gì**. Một
#: hệ thống quên đúng và một cơ chế chưa từng chạy cho ra cùng một con số, đúng
#: cái bẫy đã làm hỏng probe trước. Đánh dấu strict để ngày ai đó thêm consumer
#: thì chúng đỏ lên và được viết lại thành phép đo thật, thay vì im lặng xanh.
#: RC-0 (19/08/2026) — TIỀN ĐỀ NÀY ĐÃ BIẾN MẤT, và đúng theo cách nó tự hẹn:
#: "ngày ai đó thêm consumer thì chúng đỏ lên và được viết lại thành phép đo
#: thật". `ReconciliationWorker` LÀ consumer đó. Hai test dưới không còn quan
#: sát một cơ chế đứng yên — chúng chạy replay RỒI drain bằng chính worker của
#: sản phẩm, nên "A không quay lại" cuối cùng cũng là một phép đo.
#:
#: Giữ nguyên nhận định cũ ở đây làm hồ sơ, không xoá: nó là lý do hai test này
#: từng KHÔNG chứng minh gì, và là hình dạng của cái bẫy cần nhớ.


def test_forgotten_evidence_stays_gone_through_replay_and_rebuild(world):
    memory_os, ids = world
    forgetting.forget_derived(memory_os, memory_id=ids["A"])
    _assert_controls_alive(memory_os, "sau forget")

    for stage, action in (("replay", "replay"), ("rebuild", "rebuild")):
        _rebuild(memory_os)
        present = _present(memory_os)
        _assert_controls_alive(memory_os, stage)
        assert A_TEXT not in present, (
            f"[{stage}] RESURRECTION — bằng chứng đã quên quay lại sau {stage}")
        assert _materialised(memory_os), (
            f"[{stage}] cơ chế chưa chạy: outbox được xếp hàng nhưng "
            f"không gì vật chất hoá nó. Không có gì quay lại vì không "
            f"có gì được dựng lại — OPEN, không phải PASS.")


def test_the_correction_that_number_seven_rescues_is_not_the_forgotten_one(
        world):
    """Ca Lê Sơn, nguyên hình dạng của nó.

    #7 làm một đính chính mang cùng mệnh đề với một khẳng định trước đó tồn tại
    như một sự kiện riêng. Câu hỏi mà probe cũ không trả lời được: sự kiện được
    cứu đó có phải chính là thứ vừa bị quên không?

    Không. Cái bị quên là `nhân viên kinh doanh`; cái được cứu là đính chính về
    `trưởng nhóm`. Hai mệnh đề khác nhau, hai nhóm khác nhau."""
    from bio_agent_os.cognitive import temporal_operator as T

    memory_os, ids = world
    forgetting.forget_derived(memory_os, memory_id=ids["A"])
    _assert_controls_alive(memory_os, "sau forget")

    spans = T.claim_history(memory_os, subject="Lê Sơn", aspect="job_title",
                            predicate="job_title", context=CTX)
    contents = " ".join(s.content for s in spans)
    assert "nhân viên kinh doanh" not in contents, "RESURRECTION"
    assert any(s.event_kind == T.CORRECT for s in spans), (
        "đính chính không mở span — #7 không chạy trên thế giới này, nên test "
        "chưa chứng minh gì về tương tác giữa #7 và forget")


def test_replay_gate_is_not_the_only_guard(world, monkeypatch):
    """HAI CỔNG ĐỘC LẬP, không phải một.

    Đo được khi RC-0 cho probe chạm tới cơ chế thật: tắt cổng bia mộ ở
    `projection_engine` là replay reset đủ 5 job, worker vẫn TỪ CHỐI dựng A —
    vì `ReconciliationWorker.process` kiểm `buried()` một lần nữa trên chính
    job nó sắp chạy. Một lớp phòng thủ hỏng không đủ để ký ức đã quên sống
    lại; điều đó phải được ghi thành tính chất, không để rơi vào may mắn."""
    memory_os, ids = world
    forgetting.forget_derived(memory_os, memory_id=ids["A"])
    assert A_TEXT not in _present(memory_os)

    from bio_agent_os.cognitive import projection_engine

    monkeypatch.setattr(projection_engine, "buried_among",
                        lambda conn, ids: set())
    report = _rebuild(memory_os)
    assert report.reset == 5, f"replay chưa chạm tới job nào: {report.reset}"
    assert A_TEXT not in _present(memory_os), (
        "cổng thứ hai (worker) không giữ — phòng thủ chỉ còn một lớp")
    _assert_controls_alive(memory_os, "một-cổng-hỏng")


def test_the_mutant_resurrects_and_this_test_must_catch_it(world, monkeypatch):
    """IGNORE_TOMBSTONE_EVERYWHERE.

    Nếu mutant KHÔNG làm A quay lại thì probe vẫn chưa đo resurrection thật —
    nó chỉ đang quan sát một hệ thống chưa từng bị thử. Nên mutant phải tắt
    CẢ HAI cổng: cổng của replay và cổng của worker."""
    memory_os, ids = world
    forgetting.forget_derived(memory_os, memory_id=ids["A"])
    assert A_TEXT not in _present(memory_os)

    from bio_agent_os.cognitive import projection_engine
    from bio_agent_os.cognitive import reconciliation_worker

    monkeypatch.setattr(projection_engine, "buried_among",
                        lambda conn, ids: set())
    monkeypatch.setattr(reconciliation_worker, "buried",
                        lambda conn, event_id: False)
    report = _rebuild(memory_os)

    assert A_TEXT in _present(memory_os), (
        f"mutant bỏ qua tombstone mà A vẫn không quay lại (report={report!r}) "
        f"— probe chưa chạm được vào cơ chế nó định đo, nên kết quả "
        f"'resurrection = 0' ở các test trên chưa chứng minh gì")


def _rebuild(memory_os) -> "object":
    """Đường phục hồi THẬT của hệ, không phải một bản dựng lại viết tay.

    Bản đầu của probe này tự chèn SQL để mô phỏng rebuild. Nó hỏng vì thiếu một
    cột NOT NULL — và đó là điều may, vì nếu chạy được thì nó đã đo chính SQL
    của tôi chứ không đo `ProjectionEngine`. Một probe dựng lại cơ chế mà nó
    định kiểm tra sẽ luôn kết luận rằng cơ chế đó đúng.
    """
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine
    from bio_agent_os.cognitive.reconciliation_worker import worker_for

    report = ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)
    # RC-0: và CHẠY consumer thật. Không có bước này, "không có gì quay lại"
    # chỉ nói rằng không có gì được dựng lại.
    worker_for(memory_os, lease_seconds=300.0).run_once(batch_size=50)
    return report


def _materialised(memory_os) -> bool:
    """Đã có ai biến outbox thành memory chưa?

    Điều kiện để hai test rebuild ở trên là một phép đo, chứ không phải việc
    quan sát một cơ chế đứng yên. Ngày nào nó đúng, `xfail(strict=True)` sẽ đỏ
    và bắt viết lại chúng cho tử tế."""
    try:
        pending = memory_os.memories.conn.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE status IN "
            "('pending','in_progress')").fetchone()[0]
        built = memory_os.memories.conn.execute(
            "SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
        return pending == 0 and built > 0
    except Exception:                                       # noqa: BLE001
        return False
