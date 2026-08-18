"""Một cái chuông kêu đúng giờ nhưng sai lý do vẫn là chuông hỏng.

`xfail` là ngày hết hạn của một defect đã biết. Nó chỉ giữ được nghĩa đó khi
**tiền đề** của nó còn xảy ra. Nếu tiền đề biến mất, ca vẫn báo `xfail`, vẫn
nằm ngoài danh sách đỏ, và vẫn trông y hệt "defect còn nguyên" — trong khi sự
thật là defect đã được sửa và phép đo thì đã mù.

Chuyện đó đã xảy ra thật, không phải giả định:

    `test_p4_a_deterministic_poison_job_never_converges` mang
    `xfail(strict=True)` với bảng số "12/12 lượt giao đều về job độc". Khi
    fairness vào, nó KHÔNG XPASS như dự đoán. Nó vẫn `xfail` — vì tiền đề
    `deliveries >= 3` không còn đạt nữa. Sổ giao việc lúc đó: mỗi job đúng
    **1 lượt**. Đọc danh sách đỏ thì kết luận "P4 chưa sửa"; đọc sổ thì thấy
    P4 đã sửa xong.

Hai khoá dưới đây, không phải một framework tổng quát.
"""
from __future__ import annotations

import ast
import pathlib
import time

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

TESTS = pathlib.Path(__file__).parent

# Mỗi `xfail` phải khai ra TIỀN ĐỀ giữ nó sống. Thêm một ca xfail mới mà không
# đăng ký ở đây thì khoá này đỏ — đó là chủ đích: người thêm phải nói ra điều
# gì còn phải đúng thì cái chuông mới còn nghĩa.
REGISTERED_EXPECTED_FAILURES = {
    "test_name_boundaries.py::test_a_lowercased_family_name_mid_clause_is_still_a_name":
        "họ viết thường giữa mệnh đề vẫn chưa được nhận là tên",
    "test_retrieval_relevance_floor.py::test_an_unrelated_query_returns_nothing":
        "sàn liên quan chưa có, truy vấn lạc đề vẫn trả về thứ gì đó",
    "test_retrieval_relevance_floor.py::test_nonsense_does_not_outrank_silence":
        "sàn liên quan chưa có",
    "test_retrieval_relevance_floor.py::test_confidence_cannot_rescue_an_irrelevant_memory":
        "confidence vẫn kéo được ký ức lạc đề lên",
    "test_structured_slots.py::test_a_wrong_slot_does_not_hide_a_memory_without_an_embedder":
        "không có embedder thì slot sai vẫn giấu được ký ức",
}


def _expected_failures() -> dict:
    found = {}
    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any("xfail" in ast.unparse(d) for d in node.decorator_list):
                found[f"{path.name}::{node.name}"] = True
    return found


def test_every_expected_failure_declares_the_premise_that_keeps_it_alive():
    """Không có `xfail` nào được tồn tại mà không khai tiền đề của nó."""
    found = set(_expected_failures())
    registered = set(REGISTERED_EXPECTED_FAILURES)

    unregistered = found - registered
    assert not unregistered, (
        f"xfail chưa đăng ký tiền đề: {sorted(unregistered)}. Một xfail không "
        f"nói ra điều gì còn phải đúng thì không ai biết lúc nào nó hết nghĩa.")

    retired = registered - found
    assert not retired, (
        f"đã đăng ký nhưng không còn trong suite: {sorted(retired)}. Xoá khỏi "
        f"registry, và ghi vào report vì sao nó ra đi.")


def test_the_retired_p4_premise_really_is_gone(tmp_path):
    """Khoá riêng cho P4: tiền đề cũ phải KHÔNG còn xảy ra.

    Tiền đề cũ, đo được trước H1.3 trên harness tất định:

        12 vòng batch_size=1
        POISON      attempts=12   ← nhận 12/12 lượt giao
        HEALTHY_A   attempts=0    ← chưa từng được giao
        HEALTHY_B   attempts=0    ← chưa từng được giao

    Ca này chạy lại đúng kịch bản đó và khẳng định điều ngược lại. Nếu fairness
    bị gỡ, nó đỏ — và lần này nó đỏ THẬT, không lặng lẽ trượt sang `xfail`.
    """
    memory_os = MemoryOS(tmp_path / "p4-premise.db", projection_mode="outbox")
    try:
        texts = ["Số điện thoại của Hoàng Yến là 0977123456.",
                 "Bùi Cường sống ở Hà Nội.",
                 "Phạm Vy đang giữ chức trưởng nhóm."]
        for text in texts:
            memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
        conn = memory_os.memories.conn
        for offset, row in enumerate(
                list(conn.execute("SELECT job_id FROM projection_outbox "
                                  "ORDER BY created_at"))):
            conn.execute("UPDATE projection_outbox SET created_at=?, "
                         "available_at=? WHERE job_id=?",
                         (1000.0 + offset, 1000.0 + offset, row[0]))
        conn.commit()

        events = {str(e) for e, p in memory_os.events.conn.execute(
            "SELECT event_id, payload_json FROM cognitive_events")
            if "0977123456" in str(p or "")}
        poison_id = next(str(dict(r)["job_id"]) for r in conn.execute(
            "SELECT job_id, event_id FROM projection_outbox")
            if str(dict(r)["event_id"]) in events)

        worker = worker_for(memory_os, lease_seconds=0)
        real = worker.process
        worker.process = lambda job: (
            None if job.job_id == poison_id else real(job))

        now, delivered = time.time(), []
        for _ in range(12):
            for job in worker.outbox.claim(worker_id="w", limit=1, now=now,
                                           lease_seconds=0):
                delivered.append(job.job_id)
                worker.process(job)
            row = conn.execute(
                "SELECT MIN(available_at) FROM projection_outbox "
                "WHERE status IN ('pending', 'in_progress')").fetchone()
            if row and row[0] is not None:
                now = max(now, float(row[0])) + 1e-6

        assert delivered, "không job nào được giao — ca này chưa đo được gì"
        share = delivered.count(poison_id) / len(delivered)
        assert share < 1.0, (
            f"tiền đề cũ VẪN xảy ra: job độc nhận {share:.0%} số lượt giao. "
            f"Nếu đúng vậy thì fairness không có hiệu lực, và mọi ca đang xanh "
            f"trong lane này đang xanh vì lý do khác: {delivered}")
        assert len(set(delivered)) == 3, (
            f"không phải job nào cũng tới lượt — tiền đề cũ mới chỉ yếu đi chứ "
            f"chưa mất: {delivered}")
    finally:
        memory_os.close()
