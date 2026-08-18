"""H1.3 — hợp đồng công bằng của projection queue.

Hợp đồng này **không** đòi strict FIFO, **không** đòi hàng đợi về 0, và
**không** tự động kết thúc một job dựa trên bộ đếm nào. Nó đòi đúng chín điều
dưới đây, mỗi điều một ca.

Vì sao không có "poison isolation" trong danh sách:
`H1_2_ABANDONMENT_ATTRIBUTION_AUDIT.md` đo 11 tín hiệu trong runtime và thấy 2
tín hiệu bền, cả hai chỉ là định danh công việc. Hạ tầng chập chờn và payload
độc để lại cùng một dấu vết. Cách ly một job dựa trên bộ đếm không phân biệt
được hai nguyên nhân là mã hoá một sự chắc chắn mà hệ thống không có — nên một
job chưa giải quyết được vẫn RETRYABLE, chỉ là không được chiếm cả con đường.
"""
from __future__ import annotations

from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

CLOCK0, STEP, LEASE = 2_000.0, 1.0, 0.5
YIELD = {"yield_base": 1.0, "yield_cap": 5.0}

POISON = "Số của Hoàng Yến là 0977123456."
HEALTHY_1 = "Bùi Cường sống ở Hà Nội."
HEALTHY_2 = "Lê Dung sống ở Đà Nẵng."
NEEDLES = {POISON: "0977123456", HEALTHY_1: "Hà Nội", HEALTHY_2: "Đà Nẵng"}


def _store(tmp_path: Path, name: str, specs: list[tuple[str, str]]):
    """Hàng đợi có thứ tự xác định, tự chứng minh trước khi đo.

    `created_at` được ghim vì `time.time()` có resolution 15.625 ms trên máy
    này: hai `observe()` liên tiếp trùng dấu thời gian 5/8 lần, và
    `ORDER BY created_at` không tiebreaker thì thứ tự trả về không xác định.
    Ghim không phải để test dễ hơn — để "job nào vào trước" là sự thật hiển
    ngôn thay vì đúng một cách tình cờ.
    """
    memory_os = MemoryOS(tmp_path / f"{name}.db", projection_mode="outbox")
    for content, tenant in specs:
        memory_os.observe(tenant_id=tenant, actor="a", source="u",
                          content=content, workspace_id="w1")
    rows = list(memory_os.memories.conn.execute(
        "SELECT job_id, status, attempts FROM projection_outbox"))
    assert len(rows) == len(specs), rows
    assert all(r[1] == "pending" and r[2] == 0 for r in rows), rows

    ids = {}
    for offset, (content, _tenant) in enumerate(specs):
        needle = NEEDLES[content]
        events = {str(e) for e, p in memory_os.events.conn.execute(
            "SELECT event_id, payload_json FROM cognitive_events")
            if needle in str(p or "")}
        job_id = next(str(dict(r)["job_id"]) for r in
                      memory_os.memories.conn.execute(
                          "SELECT job_id, event_id FROM projection_outbox")
                      if str(dict(r)["event_id"]) in events)
        memory_os.memories.conn.execute(
            "UPDATE projection_outbox SET created_at=?, available_at=? "
            "WHERE job_id=?", (1000.0 + offset, 1000.0 + offset, job_id))
        ids[needle] = job_id
    memory_os.memories.conn.commit()
    assert len(set(ids.values())) == len(specs)
    return memory_os, ids


def _rows(memory_os) -> dict:
    return {str(r[0]): dict(zip(("status", "attempts", "locked_by",
                                 "available_at"), r[1:]))
            for r in memory_os.memories.conn.execute(
                "SELECT job_id, status, attempts, locked_by, available_at "
                "FROM projection_outbox")}


def _done(memory_os, needle: str) -> bool:
    return any(needle in str(r[0]) for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories"))


def _spin(memory_os, dying: set, rounds: int, *, batch_size: int = 1,
          tenant_id=None, waiting_for: tuple = ()) -> list:
    """Quay `rounds` vòng. Job trong `dying` làm worker chết TRƯỚC `fail()`.

    Không dùng `worker.run_once` vì nó không nhận đồng hồ; ở đây cần đồng hồ
    ảo để backoff đo được mà không phải `sleep` thật.

    Mỗi lượt giao được ghi kèm cờ **đang có tranh chấp** — tức còn việc trong
    `waiting_for` chưa xong. Bản đầu của ca "không độc chiếm" đo tỉ lệ trên
    TOÀN bộ lần chạy và đỏ ở 93%: hai job lành xong trong 4 vòng đầu, rồi job
    độc nhận mọi lượt còn lại vì **không còn ai để chia**. Đó không phải độc
    chiếm. Độc chiếm chỉ là một mệnh đề khi có việc khác đang đợi, nên cửa sổ
    đo phải là cửa sổ tranh chấp.
    """
    worker = worker_for(memory_os, lease_seconds=LEASE)
    now, trace = CLOCK0, []
    for _ in range(rounds):
        contended = any(not _done(memory_os, n) for n in waiting_for)
        jobs = worker.outbox.claim(worker_id="w", limit=batch_size, now=now,
                                   lease_seconds=LEASE, tenant_id=tenant_id,
                                   **YIELD)
        for job in jobs:
            trace.append((job.job_id, contended))
            if job.job_id in dying:
                continue                    # worker biến mất, fail() không chạy
            worker.process(job)
            if _rows(memory_os)[job.job_id]["status"] == "in_progress":
                worker.outbox.complete(job.job_id, now=now)
        now += STEP
    return trace


# ---------------------------------------------------------------------------
# chín thuộc tính
# ---------------------------------------------------------------------------

def test_1_healthy_work_eventually_progresses(tmp_path):
    memory_os, ids = _store(tmp_path, "p1", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        assert _done(memory_os, "Hà Nội")
        assert _done(memory_os, "Đà Nẵng")
    finally:
        memory_os.close()


def test_2_no_acquisition_monopoly(tmp_path):
    """Job chưa giải quyết được vẫn được thử lại — nhưng không chiếm cả đường."""
    memory_os, ids = _store(tmp_path, "p2", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        poison = ids["0977123456"]
        trace = _spin(memory_os, {poison}, 30,
                      waiting_for=("Hà Nội", "Đà Nẵng"))
        contended = [j for j, c in trace if c]
        assert contended, "không có vòng nào thực sự tranh chấp"
        share = contended.count(poison) / len(contended)
        assert share < 0.9, f"vẫn độc chiếm khi có việc đợi: {share:.0%} {contended}"
        assert any(j == poison for j, _c in trace), "job độc bị bỏ rơi hẳn"
    finally:
        memory_os.close()


def test_3_transient_crash_recovers(tmp_path):
    """Mất worker vài lần rồi thôi thì việc vẫn xong, không cần ai can thiệp."""
    memory_os, ids = _store(tmp_path, "p3", [(HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        flaky = ids["Hà Nội"]
        worker = worker_for(memory_os, lease_seconds=LEASE)
        now, deaths = CLOCK0, 0
        for _ in range(30):
            for job in worker.outbox.claim(worker_id="w", limit=1, now=now,
                                           lease_seconds=LEASE, **YIELD):
                if job.job_id == flaky and deaths < 3:
                    deaths += 1
                    continue
                worker.process(job)
                if _rows(memory_os)[job.job_id]["status"] == "in_progress":
                    worker.outbox.complete(job.job_id, now=now)
            now += STEP
        assert deaths == 3
        assert _done(memory_os, "Hà Nội"), (
            "job chỉ gặp trục trặc hạ tầng mà không xong")
        assert _done(memory_os, "Đà Nẵng")
    finally:
        memory_os.close()


def test_4_no_silent_data_loss(tmp_path):
    """Hạ tầng chết không được làm mất việc: không dead_letter, không skipped."""
    memory_os, ids = _store(tmp_path, "p4", [(POISON, "t1"), (HEALTHY_1, "t1")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        state = _rows(memory_os)[ids["0977123456"]]
        assert state["status"] in ("pending", "in_progress"), state
    finally:
        memory_os.close()


def test_5_tenant_fairness_holds(tmp_path):
    """Job độc của t1 không được chặn hàng đợi của t2."""
    memory_os, ids = _store(tmp_path, "p5", [(POISON, "t1"), (HEALTHY_1, "t2"),
                                             (HEALTHY_2, "t2")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        assert _done(memory_os, "Hà Nội")
        assert _done(memory_os, "Đà Nẵng")
    finally:
        memory_os.close()


def test_6_no_permanent_lease(tmp_path):
    """Nhường lượt không được biến thành mắc kẹt `in_progress` vĩnh viễn."""
    memory_os, ids = _store(tmp_path, "p6", [(POISON, "t1"), (HEALTHY_1, "t1")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        rows = _rows(memory_os)
        stuck = [j for j, r in rows.items()
                 if r["status"] == "in_progress" and r["locked_by"]
                 and j != ids["0977123456"]]
        assert not stuck, rows
    finally:
        memory_os.close()


def test_7_drain_returns_a_bounded_outcome(tmp_path):
    """Drain phải KẾT LUẬN, kể cả khi hàng đợi không về 0."""
    from bio_agent_os.cognitive import projection_control as pc

    memory_os, _ids = _store(tmp_path, "p7", [(HEALTHY_1, "t1")])
    try:
        result = pc.drain(memory_os, timeout_seconds=10.0)
        assert result["outcome"] == pc.DRAIN_COMPLETE
    finally:
        memory_os.close()


def test_8_zero_automatic_false_quarantine(tmp_path):
    """Không cơ chế nào ở đây tự đưa job sang trạng thái kết thúc."""
    memory_os, ids = _store(tmp_path, "p8", [(POISON, "t1"), (HEALTHY_1, "t1")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        terminal = {j: r for j, r in _rows(memory_os).items()
                    if r["status"] in ("dead_letter", "skipped")}
        assert not terminal, terminal
    finally:
        memory_os.close()


def test_9_no_operator_needed_for_healthy_progress(tmp_path):
    """Không ai can thiệp trong suốt 30 vòng, việc lành vẫn phải xong.

    Bake-off đo được: hợp đồng cách ly đạt 15/18 ở mọi chân trời vì ba job lành
    bị nhốt nằm đó tới khi có người. Đó là khác biệt về LOẠI, không phải về
    MỨC — và scheduler cùng unattended worker đang TẮT.
    """
    memory_os, ids = _store(tmp_path, "p9", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        _spin(memory_os, {ids["0977123456"]}, 30)
        assert _done(memory_os, "Hà Nội") and _done(memory_os, "Đà Nẵng")
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# mutants — mỗi con phải giết đúng một thuộc tính
# ---------------------------------------------------------------------------

def test_mutant_abandoned_job_never_yields(tmp_path, monkeypatch):
    """ABANDONED_JOB_NEVER_YIELDS → job lành chết đói.

    Đây chính là hành vi trước H1.3. Nếu ca này không đỏ khi tắt bước nhường,
    thì thuộc tính 1 và 2 đang xanh vì lý do khác, không vì cơ chế này.
    """
    from bio_agent_os.cognitive import outbox as outbox_module

    memory_os, ids = _store(tmp_path, "m1", [(POISON, "t1"), (HEALTHY_1, "t1")])
    try:
        monkeypatch.setattr(outbox_module.ProjectionOutbox,
                            "_yield_expired_leases", lambda *a, **k: [])
        _spin(memory_os, {ids["0977123456"]}, 30)
        assert not _done(memory_os, "Hà Nội"), (
            "tắt bước nhường mà job lành vẫn xong — thuộc tính 1 và 2 đang "
            "xanh vì lý do khác")
    finally:
        memory_os.close()


def test_mutant_poison_monopolizes_acquisition(tmp_path, monkeypatch):
    """POISON_MONOPOLIZES_ACQUISITION → tỉ lệ chiếm lượt vọt lên."""
    from bio_agent_os.cognitive import outbox as outbox_module

    memory_os, ids = _store(tmp_path, "m2", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        monkeypatch.setattr(outbox_module.ProjectionOutbox,
                            "_yield_expired_leases", lambda *a, **k: [])
        poison = ids["0977123456"]
        trace = _spin(memory_os, {poison}, 30,
                      waiting_for=("Hà Nội", "Đà Nẵng"))
        contended = [j for j, c in trace if c]
        share = contended.count(poison) / max(1, len(contended))
        assert share >= 0.9, f"mutant không tái hiện được độc chiếm: {share:.0%}"
    finally:
        memory_os.close()


def test_mutant_infra_crash_auto_quarantines(tmp_path):
    """INFRA_CRASH_AUTO_QUARANTINES_JOB → job lành bị nhốt nhầm.

    Mô phỏng đúng bản vá mà H1.2 bác bỏ: cách ly khi lease mất đủ N lần, dùng
    một bộ đếm không phân biệt được hạ tầng với payload. Job dưới đây chỉ gặp
    trục trặc hạ tầng ba lần rồi lành hẳn — và nó vẫn bị nhốt.
    """
    memory_os, ids = _store(tmp_path, "m3", [(HEALTHY_1, "t1"),
                                             (HEALTHY_2, "t1")])
    try:
        flaky = ids["Hà Nội"]
        worker = worker_for(memory_os, lease_seconds=LEASE)
        now, deaths, quarantined = CLOCK0, 0, set()
        for _ in range(30):
            for job_id, row in _rows(memory_os).items():
                if (row["attempts"] >= 3 and job_id not in quarantined
                        and not _done(memory_os, "Hà Nội")
                        and job_id == flaky):
                    quarantined.add(job_id)      # <- chính sách bị bác bỏ
            for job in worker.outbox.claim(worker_id="w", limit=1, now=now,
                                           lease_seconds=LEASE, **YIELD):
                if job.job_id in quarantined:
                    continue
                if job.job_id == flaky and deaths < 3:
                    deaths += 1
                    continue
                worker.process(job)
                if _rows(memory_os)[job.job_id]["status"] == "in_progress":
                    worker.outbox.complete(job.job_id, now=now)
            now += STEP
        assert flaky in quarantined and not _done(memory_os, "Hà Nội"), (
            "mutant không tái hiện được việc nhốt nhầm — ca 3 và ca 8 đang "
            "xanh vì lý do khác")
    finally:
        memory_os.close()


def test_mutant_drain_waits_forever(tmp_path):
    """DRAIN_WAITS_FOREVER_FOR_UNRESOLVED_JOB → không có kết luận hữu hạn."""
    from bio_agent_os.cognitive import projection_control as pc

    memory_os, _ids = _store(tmp_path, "m4", [(POISON, "t1")])
    try:
        # Lease nằm ở tương lai xa: không thu hồi được, không xử lý được.
        memory_os.memories.conn.execute(
            "UPDATE projection_outbox SET status='in_progress', "
            "locked_by='worker-da-chet', locked_at=?", (1e12,))
        memory_os.memories.conn.commit()
        result = pc.drain(memory_os, timeout_seconds=2.0, poll_seconds=0.05)
        assert result["outcome"] == pc.DRAIN_INCOMPLETE_UNRESOLVED
        assert result["unresolved"] == 1
    finally:
        memory_os.close()
