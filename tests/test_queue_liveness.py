"""H1 — hàng đợi có tiến triển hữu hạn, hay chỉ quay vòng vô hạn?

**Liveness là thuộc tính của HÀNG ĐỢI, không phải của một hàng.** Nên mọi ca ở
đây đặt một job lành phía sau job có vấn đề và hỏi: job lành có xong không?

Và không dùng `no permanent in_progress` một mình, vì một job có thể rất ngoan:

    in_progress → lease hết hạn → reclaimable → in_progress → ...

và sống đời luân hồi vô tận mà mọi ảnh chụp đều thấy nó "đang tiến triển".

Ba khái niệm bị gộp trong một bộ đếm, và đó là điều H1 phải đo trước khi phán:

    delivery_attempts    số lần job được giao cho worker      <- claim() tăng
    processing_failures  số lần handler chạy và báo thất bại  <- fail() muốn đọc
    worker_losses        số lần mất lease vì worker chết

`attempts` tăng ở `claim()`, nên về nghĩa thực nó là **delivery_attempts**. Còn
`fail()` đọc **chính bộ đếm đó** để quyết dead-letter và để tính backoff. Tên
biến là lời khai của lập trình viên; execution trace mới là nhân chứng.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.outbox import DEFAULT_MAX_ATTEMPTS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

TENANT = "t1"
POISON = "Số điện thoại của Hoàng Yến là 0977123456."
HEALTHY_A = "Bùi Cường sống ở Hà Nội."
HEALTHY_B = "Phạm Vy đang giữ chức trưởng nhóm."


@dataclass
class ClaimObservation:
    """Một dòng cho mỗi lần giao việc. Đọc lại từ store, không tin metric."""

    claim_seq: int
    job_id: str
    tenant_id: str
    attempts_before: int
    attempts_after: int
    processing_started: bool = False
    processing_failed: bool = False
    lease_lost: bool = False
    terminal_state: str | None = None


@dataclass
class LivenessLedger:
    observations: list = field(default_factory=list)

    def deliveries(self, job_id: str) -> int:
        return sum(1 for o in self.observations if o.job_id == job_id)

    def as_dict(self) -> dict:
        return {"claims": len(self.observations),
                "per_job": {job: self.deliveries(job)
                            for job in {o.job_id for o in self.observations}}}


def _store(tmp_path: Path, name: str, texts: list[str]):
    """Hàng đợi có thứ tự XÁC ĐỊNH, và tự chứng minh điều đó trước khi đo.

    `ProjectionOutbox` đặt `created_at = time.time()`, mà `time.time()` trên
    Windows có resolution 15.625 ms. Hai lần `observe()` liên tiếp mất ~1 ms,
    nên 5/8 lần chúng nhận CÙNG một `created_at`. `claim()` sắp bằng
    `ORDER BY created_at` không có tiebreaker, nên với hai hàng bằng nhau thứ
    tự trả về là không xác định — và ca test đảo chiều giữa các lần chạy mà
    sản phẩm không đổi một dòng nào.

    Đo được: ép trùng → job lành xong 6/6; ép tách → job lành đứng yên 6/6.
    Cái cứu job lành là một tai nạn của đồng hồ, không phải một cơ chế.

    Nên fixture pin `created_at` thành các mốc cách nhau 1 giây theo đúng thứ
    tự nạp. Đây không phải làm test dễ hơn: nó làm cho "poison vào hàng trước"
    thành sự thật hiển ngôn thay vì đúng một cách tình cờ.
    """
    memory_os = MemoryOS(tmp_path / f"{name}.db", projection_mode="outbox")
    for text in texts:
        memory_os.observe(tenant_id=TENANT, actor="a", source="u",
                          content=text, workspace_id="w1")

    # GUARD 2 — trạng thái ban đầu phải đúng như ca test tưởng, trước khi đo.
    rows = _rows(memory_os)
    assert len(rows) == len(texts), (
        f"chờ {len(texts)} job, thấy {len(rows)}: {rows}")
    assert all(r["status"] == "pending" and r["attempts"] == 0 for r in rows), (
        f"hàng đợi không sạch lúc bắt đầu: {rows}")

    # Thứ tự nạp là thứ tự FIFO mà ca test dựa vào; ghim nó lại.
    for offset, text in enumerate(texts):
        row = _row_for(memory_os, _needle_of(text))
        assert row is not None, f"không định vị được job cho {text!r}"
        memory_os.memories.conn.execute(
            "UPDATE projection_outbox SET created_at=? WHERE job_id=?",
            (1000.0 + offset, row["job_id"]))
    memory_os.memories.conn.commit()

    ordered = [r["job_id"] for r in memory_os.memories.conn.execute(
        "SELECT job_id FROM projection_outbox ORDER BY created_at")]
    assert len(set(ordered)) == len(ordered)
    return memory_os


def _needle_of(text: str) -> str:
    """Mẩu định danh một nội dung — GUARD 1: ca test phải nói ra nó đang
    nhìn job nào, thay vì tin vào vị trí ngầm trong hàng đợi."""
    for needle in ("0977123456", "Hà Nội", "Phạm Vy"):
        if needle in text:
            return needle
    return text[:20]


def _rows(memory_os) -> list[dict]:
    # `event_id` phải có mặt: nó là khoá duy nhất nối job với nội dung của nó,
    # và bỏ nó ra khỏi SELECT làm mọi phép định vị trả về None — trông y hệt
    # "không có job nào khớp".
    return [dict(r) for r in memory_os.memories.conn.execute(
        "SELECT job_id, event_id, status, attempts, locked_by, available_at, "
        "payload_json FROM projection_outbox")]


def _row_for(memory_os, needle: str) -> dict | None:
    """Định vị job qua SỰ KIỆN của nó, không qua `payload_json`.

    `projection_outbox.payload_json` chỉ mang `{"workspace_id": …}` — nội dung
    nằm ở `cognitive_events`, trên một connection khác. Bản đầu của hàm này tìm
    trong payload, không thấy gì, và suýt biến một lỗi probe thành một "phát
    hiện" về hành vi sản phẩm.
    """
    events = getattr(getattr(memory_os, "events", None), "conn", None)
    if events is None:
        return None
    matching = {str(event_id) for event_id, payload in events.execute(
        "SELECT event_id, payload_json FROM cognitive_events")
        if needle in str(payload or "")}
    for row in _rows(memory_os):
        if str(row.get("event_id")) in matching:
            return row
    return None


def _time_ahead(memory_os) -> float:
    """Một thời điểm vượt qua mọi backoff đang treo.

    Đẩy đồng hồ tới thay vì `sleep`: backoff lên tới 300 giây, và một test ngồi
    chờ thật là một test sẽ bị ai đó tắt."""
    return max((float(r["available_at"] or 0) for r in _rows(memory_os)),
               default=0.0) + 1.0


def _materialised(memory_os, needle: str) -> bool:
    return any(needle in str(r[0]) for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories"))


# ---------------------------------------------------------------------------
# what `attempts` actually counts — measured, not inferred
# ---------------------------------------------------------------------------

def _drain_past_eligibility(memory_os, *, cycles: int = 10) -> dict:
    """Quay hàng đợi, đẩy đồng hồ tới đúng mốc đủ-điều-kiện ĐÃ ĐỌC ĐƯỢC.

    Từ H1.3, `claim()` cho một lease bị bỏ rơi nhường lượt khi còn việc khác
    đang đợi, bằng cách đẩy `available_at` tới. Quay vòng bằng đồng hồ thật thì
    hết trong vài micro giây và không bao giờ chạm mốc đó. Tăng số vòng rồi
    mong thời gian "đủ" là săn kết quả; đọc mốc rồi đẩy tới đúng nó mới là thi
    hành hợp đồng.
    """
    worker = worker_for(memory_os, lease_seconds=0)
    conn = memory_os.memories.conn
    now, waits, delivered = time.time(), [], []
    for _ in range(cycles):
        jobs = worker.outbox.claim(worker_id="w", limit=5, now=now,
                                   lease_seconds=0)
        if jobs:
            for job in jobs:
                delivered.append(job.job_id)
                worker.process(job)
            continue
        row = conn.execute(
            "SELECT MIN(available_at) FROM projection_outbox "
            "WHERE status IN ('pending', 'in_progress')").fetchone()
        if row is None or row[0] is None:
            break
        boundary = float(row[0])
        if boundary <= now:
            break
        waits.append(round(boundary - now, 6))
        now = boundary + 1e-6
    return {"yield_waits": waits, "delivered": delivered}


def test_attempts_counts_deliveries_not_failures(tmp_path):
    """Bộ đếm tăng khi GIAO việc, kể cả khi handler không bao giờ chạy."""
    memory_os = _store(tmp_path, "meaning", [POISON])
    try:
        outbox = worker_for(memory_os).outbox
        for expected in (1, 2, 3):
            outbox.claim(worker_id=f"w{expected}", lease_seconds=0,
                         tenant_id=TENANT)
            assert _rows(memory_os)[0]["attempts"] == expected
        # Không handler nào chạy, không fail() nào được gọi, mà attempts = 3.
        assert _rows(memory_os)[0]["status"] == "in_progress"
    finally:
        memory_os.close()


def test_worker_losses_consume_the_processing_failure_budget(tmp_path):
    """Hệ quả cụ thể của việc gộp ba khái niệm vào một bộ đếm.

    `fail()` đọc chính bộ đếm mà `claim()` tăng. Nên một job bị crash-churn
    `max_attempts` lần rồi mới hỏng **một lần thật** sẽ dead-letter ngay — mất
    toàn bộ ngân sách retry vì hạ tầng, không vì payload."""
    memory_os = _store(tmp_path, "budget", [POISON])
    try:
        outbox = worker_for(memory_os).outbox
        job_id = _rows(memory_os)[0]["job_id"]
        for index in range(DEFAULT_MAX_ATTEMPTS):
            outbox.claim(worker_id=f"crash{index}", lease_seconds=0,
                         tenant_id=TENANT)       # worker chết, không fail()

        status = outbox.fail(job_id, "lần hỏng THẬT đầu tiên")
        assert status == "dead_letter", (
            f"kỳ vọng ngân sách retry đã bị crash-churn ăn hết, nhận {status}")
    finally:
        memory_os.close()


def test_p1_worker_dies_before_the_handler_runs(tmp_path):
    """Job bị bỏ rơi vẫn thu hồi được — nhưng SAU khi đủ điều kiện, không tức thì.

    Hợp đồng cũ ở ca này là "thu hồi ngay", và nó xanh suốt. H1.3 đổi nó, vì
    thu hồi ngay CHÍNH LÀ cơ chế làm job lành chết đói: job bị bỏ rơi quay lại
    đầu hàng ở mỗi vòng và không cần độc hại để chặn cả đoàn.

    Nên "tức thì" không còn là bất biến. "Cuối cùng vẫn thu hồi được" mới là.
    """
    memory_os = _store(tmp_path, "p1", [POISON, HEALTHY_A])
    try:
        outbox = worker_for(memory_os).outbox
        outbox.claim(worker_id="dead", tenant_id=TENANT)   # chết ngay sau claim

        evidence = _drain_past_eligibility(memory_os)

        assert evidence["yield_waits"], (
            f"không quan sát được hàng rào nhường lượt nào — ca này không còn "
            f"đo cái nó định đo: {evidence}")
        assert _materialised(memory_os, "Hà Nội"), "job lành phía sau không chạy"
        assert _materialised(memory_os, "0977123456"), (
            f"job bị bỏ rơi không thu hồi được KỂ CẢ sau mốc đủ điều kiện: "
            f"{evidence}")
    finally:
        memory_os.close()


def test_p3_retry_after_a_real_failure_is_bounded(tmp_path):
    """Hợp đồng retry có chặn trên không — và backoff có tăng không."""
    memory_os = _store(tmp_path, "p3", [POISON])
    try:
        outbox = worker_for(memory_os).outbox
        rows = _rows(memory_os)
        # Một job duy nhất. Với hai job, `claim(limit=1)` lấy cái kia và mọi
        # con số đọc ra là của một hàng khác — đúng cái đã làm probe đầu tiên
        # báo `attempts` đứng yên ở 0 suốt bảy vòng.
        assert len(rows) == 1, f"ca này cần đúng một job: {rows}"
        job_id = rows[0]["job_id"]
        statuses, backoffs = [], []
        for _ in range(DEFAULT_MAX_ATTEMPTS + 2):
            # `now` đẩy tới trước để vượt backoff, thay vì ngồi chờ thật.
            claimed = outbox.claim(worker_id="w", lease_seconds=0,
                                   tenant_id=TENANT, now=_time_ahead(memory_os))
            assert claimed, f"không claim lại được sau backoff: {_rows(memory_os)}"
            statuses.append(outbox.fail(job_id, "hỏng thật"))
            backoffs.append(_rows(memory_os)[0]["available_at"])
            if statuses[-1] == "dead_letter":
                break

        # Đo được: attempts 1→5, dead_letter đúng lần thứ 5. Đường
        # explicit-failure CÓ ngữ nghĩa kết thúc.
        assert statuses[-1] == "dead_letter", statuses
        assert len(statuses) == DEFAULT_MAX_ATTEMPTS, statuses
        assert _rows(memory_os)[0]["attempts"] == DEFAULT_MAX_ATTEMPTS
    finally:
        memory_os.close()


def test_p4_every_job_gets_a_turn_even_with_a_poison_job(tmp_path):
    """**Ca quyết định.** Từng là bằng chứng của defect; giờ là bằng chứng của bản vá.

    Lịch sử pháp y, giữ nguyên không sửa. Trước H1.3, đo trên harness tất định
    (created_at được ghim, 6/6 lần chạy giống hệt nhau):

        12 vòng batch_size=1
        POISON      in_progress  attempts=12   ← nhận 12/12 lượt giao
        HEALTHY_A   pending      attempts=0    ← chưa từng được giao
        HEALTHY_B   pending      attempts=0    ← chưa từng được giao

    Ca này từng là `xfail(strict=True)` mang đúng bảng số đó. Khi fairness vào,
    nó không XPASS như dự đoán — nó vẫn `xfail`, nhưng vì một TIỀN ĐỀ hỏng
    (`deliveries >= 3` không còn đạt), chứ không vì kết luận. Sổ giao việc lúc
    đó cho thấy vì sao: mỗi job đúng 1 lượt. Một cái chuông kêu đúng giờ nhưng
    sai lý do vẫn là chuông hỏng, nên ca được viết lại thành khẳng định tiến
    triển.
    """
    memory_os = _store(tmp_path, "p4", [POISON, HEALTHY_A, HEALTHY_B])
    try:
        poison_row = _row_for(memory_os, "0977123456")
        assert poison_row, "không định vị được job độc"
        poison_id = poison_row["job_id"]

        worker = worker_for(memory_os, lease_seconds=0)
        real_process = worker.process

        def dies_on_poison(job):
            if job.job_id == poison_id:
                raise RuntimeError("worker chết khi chạm job độc")
            return real_process(job)

        worker.process = dies_on_poison
        now, delivered = time.time(), []
        for _ in range(12):
            try:
                for job in worker.outbox.claim(worker_id="w", limit=1, now=now,
                                               lease_seconds=0):
                    delivered.append(job.job_id)
                    worker.process(job)
            except RuntimeError:
                pass                       # đúng: tiến trình chết, lease treo
            row = memory_os.memories.conn.execute(
                "SELECT MIN(available_at) FROM projection_outbox "
                "WHERE status IN ('pending', 'in_progress')").fetchone()
            if row and row[0] is not None:
                now = max(now, float(row[0])) + 1e-6

        healthy_done = (_materialised(memory_os, "Hà Nội")
                        and _materialised(memory_os, "trưởng nhóm"))
        poison = _row_for(memory_os, "0977123456")

        assert healthy_done, f"job lành vẫn chết đói: {_rows(memory_os)}"
        assert len(set(delivered)) == 3, (
            f"không phải job nào cũng tới lượt: {delivered}")
        # Job chưa giải quyết được vẫn RETRYABLE — không dead-letter, không nhốt.
        assert poison["status"] in ("pending", "in_progress"), poison
    finally:
        memory_os.close()


def test_p4b_drain_cannot_converge_with_a_poison_job(tmp_path):
    """Hệ quả vận hành: `drain` có kết thúc được không?

    `ROLLBACK_RUNBOOK` dùng drain-về-0 làm cổng hoàn tất, nên câu này quyết
    định `bounded drain` là PROVIDED hay NOT PROVIDED."""
    from bio_agent_os.cognitive.projection_control import (
        DRAIN_INCOMPLETE_UNRESOLVED, drain)

    memory_os = _store(tmp_path, "p4b", [POISON, HEALTHY_A])
    try:
        outbox = worker_for(memory_os).outbox
        outbox.claim(worker_id="dead-forever", tenant_id=TENANT)

        report = drain(memory_os, timeout_seconds=4.0)
        # Từ H1.3, hợp đồng là một KẾT LUẬN hữu hạn, không phải hai boolean.
        # `bounded outcome` không đòi hàng đợi về 0: một job chưa hiểu, còn
        # nguyên và được báo rõ, tốt hơn một job lành bị nhốt để con số về 0.
        assert report["outcome"] == DRAIN_INCOMPLETE_UNRESOLVED, report
        assert report["unresolved"] >= 1, report
        assert report.get("drained") is False, (
            f"lease bị bỏ rơi mà drain vẫn về 0 — cập nhật ca này: {report}")
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# mutants
# ---------------------------------------------------------------------------

def test_mutant_never_reclaim_expired_lease(tmp_path, monkeypatch):
    """NEVER_RECLAIM_EXPIRED_LEASE — job mắc kẹt `in_progress` vĩnh viễn."""
    from bio_agent_os.cognitive import outbox as outbox_module

    memory_os = _store(tmp_path, "mutant-reclaim", [POISON])
    try:
        real = outbox_module.ProjectionOutbox.claim

        def pending_only(self, worker_id, *, limit=1, now=None,
                         lease_seconds=300.0, tenant_id=None):
            # Bỏ nhánh thu hồi: chỉ nhận `pending`.
            rows = self.conn.execute(
                "SELECT job_id FROM projection_outbox WHERE status='pending'"
            ).fetchall()
            if not rows:
                return []
            return real(self, worker_id, limit=limit, now=now,
                        lease_seconds=lease_seconds, tenant_id=tenant_id)

        worker_for(memory_os).outbox.claim(worker_id="dead", tenant_id=TENANT)
        monkeypatch.setattr(outbox_module.ProjectionOutbox, "claim",
                            pending_only)

        worker = worker_for(memory_os, lease_seconds=0)
        for _ in range(4):
            worker.run_once()

        assert not _materialised(memory_os, "0977123456"), (
            "mutant chặn thu hồi mà job vẫn chạy — ca P1 chưa chạm tới cơ chế "
            "nó định đo")
    finally:
        memory_os.close()


def test_a_poison_job_yields_and_healthy_work_proceeds(tmp_path):
    """Cái chuông đã kêu, và đây là hình dạng mới của nó.

    Lịch sử pháp y — hành vi đo được TRƯỚC H1.3, giữ nguyên:

        vòng 1: POISON in_progress attempts=1   healthy pending attempts=0
        vòng 2: POISON in_progress attempts=2   healthy pending attempts=0
        vòng 3: POISON in_progress attempts=3   healthy pending attempts=0
        vòng 4: POISON in_progress attempts=4   healthy pending attempts=0

    Với `batch_size=1`, `ORDER BY created_at` trả job độc trước MỖI vòng, và
    job lành phía sau không bao giờ tới lượt. Nó không chậm — nó đứng yên.

    Ghép với P3, khoảng trống hiện ra chính xác:

        handler chạy và hỏng   → fail() → attempts được đọc → dead-letter lần 5
        worker chết im lặng    → fail() KHÔNG được gọi → không gì đọc attempts
                               → quay vô hạn VÀ chặn đầu hàng

    Cùng một bộ đếm; một đường có người đọc, một đường không. Ca này giờ khẳng
    định điều ngược lại, và nó vẫn là một cái chuông: gỡ fairness thì nó đỏ.
    """
    memory_os = _store(tmp_path, "starvation", [POISON, HEALTHY_A])
    try:
        outbox = worker_for(memory_os).outbox
        outbox.claim(worker_id="dead", tenant_id=TENANT)

        evidence = _drain_past_eligibility(memory_os)

        assert evidence["yield_waits"], f"job độc không hề nhường lượt: {evidence}"
        assert _materialised(memory_os, "Hà Nội"), (
            f"job lành vẫn không tiến triển: {_rows(memory_os)}")
    finally:
        memory_os.close()


def test_the_ordering_tie_and_not_the_round_count_was_the_divergence(tmp_path):
    """Ca này từng mang một câu hỏi mở. Câu hỏi đã có lời đáp.

    Nghi vấn ban đầu: hành vi đổi giữa 4 vòng và 6 vòng. Sai — số vòng không
    liên quan. Nguyên nhân là `created_at` trùng nhau do resolution 15.625 ms
    của `time.time()`, làm `ORDER BY created_at` mất tính xác định:

        ép TRÙNG created_at  → job lành xong      6/6
        ép TÁCH  created_at  → job lành đứng yên  6/6

    Sau khi `_store` ghim `created_at`, cả file cho kết quả giống hệt nhau qua
    6 lần chạy. Từ H1.3, job lành tiến triển vì fairness, không vì may mắn của
    đồng hồ — và đó chính là điều ca này khẳng định bây giờ.
    """
    memory_os = _store(tmp_path, "divergence", [POISON, HEALTHY_A])
    try:
        outbox = worker_for(memory_os).outbox
        outbox.claim(worker_id="dead", tenant_id=TENANT)
        _drain_past_eligibility(memory_os)
        assert _materialised(memory_os, "Hà Nội")
    finally:
        memory_os.close()
