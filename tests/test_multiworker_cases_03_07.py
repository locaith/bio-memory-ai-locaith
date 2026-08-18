"""H1.4 — các ca 03/04/05/07/06: hai worker thật, miền lease hợp lệ.

Mỗi ca đi qua admissibility gate trước khi được nói PASS: đúng population,
treatment đã chạy, đúng mục tiêu, interleaving có nhân chứng, đường quan sát
hoạt động, trạng thái cấm chạm được dưới mutant, và code sạch chặn nó.

Nhân chứng đồng thời ở đây là CẤU TRÚC, không phải đồng hồ: hai worker bị ép
vào cùng một cửa sổ vòng bằng `threading.Barrier` — cả hai `claim()` xảy ra
giữa hai lần barrier — nên "chúng chạy song song" là sự kiện đồng bộ đã xảy
ra, không phải hai timestamp gần nhau.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for

LEASE = 300.0
POISON = "Số điện thoại của Hoàng Yến là 0977123456."
HEALTHY_1 = "Bùi Cường sống ở Hà Nội."
HEALTHY_2 = "Phạm Vy đang giữ chức trưởng nhóm."
NEEDLE = {POISON: "0977123456", HEALTHY_1: "Hà Nội", HEALTHY_2: "trưởng nhóm"}


def _build(tmp_path: Path, name: str, specs) -> tuple[Path, dict]:
    """(nội dung, tenant) → path + map needle→job_id, created_at ghim."""
    path = tmp_path / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    for content, tenant in specs:
        memory_os.observe(tenant_id=tenant, actor="a", source="u",
                          content=content, workspace_id="w1")
    conn = memory_os.memories.conn
    ids = {}
    for offset, (content, _t) in enumerate(specs):
        needle = NEEDLE[content]
        events = {str(e) for e, p in memory_os.events.conn.execute(
            "SELECT event_id, payload_json FROM cognitive_events")
            if needle in str(p or "")}
        job_id = next(str(dict(r)["job_id"]) for r in conn.execute(
            "SELECT job_id, event_id FROM projection_outbox")
            if str(dict(r)["event_id"]) in events)
        conn.execute("UPDATE projection_outbox SET created_at=?, available_at=? "
                     "WHERE job_id=?", (1000.0 + offset, 1000.0 + offset, job_id))
        ids[needle] = job_id
    conn.commit()
    memory_os.close()
    assert len(ids) == len(specs)
    return path, ids


def _rows(path) -> dict:
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        return {str(r["job_id"]): dict(r) for r in conn.execute(
            "SELECT job_id, status, attempts, locked_by, locked_at, "
            "available_at, tenant_id FROM projection_outbox")}
    finally:
        conn.close()


def _done(path, needle: str) -> int:
    conn = sqlite3.connect(str(path), timeout=15.0)
    try:
        return sum(1 for r in conn.execute(
            "SELECT content FROM cognitive_memories") if needle in str(r[0]))
    finally:
        conn.close()


class RoundWorkers:
    """Hai worker thật chạy theo vòng đồng bộ, đồng hồ ảo chung.

    Mỗi vòng: barrier → CẢ HAI claim tại cùng `now` (cửa sổ chồng nhau — nhân
    chứng interleaving) → xử lý → barrier → điều phối viên đẩy `now` qua mốc
    đủ-điều-kiện kế (gồm CẢ `locked_at + lease`, không chỉ `available_at`).
    """

    def __init__(self, path, *, rounds: int, dying: set,
                 tenants=(None, None)):
        self.path, self.rounds, self.dying = path, rounds, dying
        self.tenants = tenants
        self.now = [time.time() + LEASE + 1]     # vượt hạn setup ngay từ đầu
        self.barrier = threading.Barrier(3)
        self.trace: list[tuple] = []             # (round, worker, job_id)
        self.lock = threading.Lock()
        self.overlap_rounds = 0

    def _worker(self, index: int, tenant):
        memory_os = MemoryOS(self.path, projection_mode="outbox")
        try:
            worker = worker_for(memory_os, worker_id=f"W{index}",
                                lease_seconds=LEASE, tenant_id=tenant)
            for round_no in range(self.rounds):
                self.barrier.wait(timeout=30)
                jobs = worker.outbox.claim(worker_id=f"W{index}", limit=1,
                                           now=self.now[0], lease_seconds=LEASE,
                                           tenant_id=tenant)
                with self.lock:
                    # Ghi cả LƯỢT THỬ — hai worker cùng SELECT một đầu hàng thì
                    # kẻ thua CAS nhận []; "cùng thử trong một cửa sổ" mới là
                    # nhân chứng interleaving, "cùng thắng" thì không ai đảm bảo.
                    self.trace.append((round_no, f"W{index}",
                                       jobs[0].job_id if jobs else None))
                for job in jobs:
                    if job.job_id in self.dying:
                        continue                  # worker chết, lease treo
                    worker.process(job)
                    row = _rows(self.path)[job.job_id]
                    if row["status"] == "in_progress":
                        worker.outbox.complete(job.job_id, now=self.now[0])
                self.barrier.wait(timeout=30)
        finally:
            memory_os.close()

    def run(self):
        threads = [threading.Thread(target=self._worker, args=(i, t))
                   for i, t in enumerate(self.tenants)]
        for t in threads:
            t.start()
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        try:
            for round_no in range(self.rounds):
                self.barrier.wait(timeout=30)     # mở cửa sổ claim chung
                self.barrier.wait(timeout=30)     # cả hai đã claim xong
                with self.lock:
                    attempted = {w for r, w, _ in self.trace if r == round_no}
                if len(attempted) == 2:
                    self.overlap_rounds += 1
                row = conn.execute(
                    "SELECT MIN(CASE WHEN status='pending' THEN available_at "
                    " ELSE MAX(COALESCE(locked_at,0)+?, available_at) END) "
                    "FROM projection_outbox "
                    "WHERE status IN ('pending','in_progress')",
                    (LEASE,)).fetchone()
                if row and row[0] is not None:
                    self.now[0] = max(self.now[0], float(row[0])) + 1e-6
        finally:
            conn.close()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "worker treo"
        return self


# ---------------------------------------------------------------------------
# 03 — poison + healthy, hai worker
# ---------------------------------------------------------------------------

def test_case_03_healthy_progress_with_two_workers_and_a_poison_job(tmp_path):
    path, ids = _build(tmp_path, "c03", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                         (HEALTHY_2, "t1")])
    run = RoundWorkers(path, rounds=8, dying={ids["0977123456"]}).run()

    # admissibility: cả hai worker đã nhận việc, và có vòng chồng cửa sổ
    workers_used = {w for _r, w, j in run.trace if j is not None}
    assert workers_used == {"W0", "W1"}, f"chỉ một worker chạy: {run.trace}"
    assert run.overlap_rounds >= 1, "không có vòng nào hai worker cùng nhận việc"

    assert _done(path, "Hà Nội") == 1 and _done(path, "trưởng nhóm") == 1, (
        f"job lành không tiến triển: {_rows(path)}")
    poison = _rows(path)[ids["0977123456"]]
    assert poison["status"] in ("pending", "in_progress"), (
        f"job độc bị kết thúc tự động: {poison}")
    # không độc chiếm: trong các vòng CÒN tranh chấp, poison không ăn hết lượt
    contended = [j for r, _w, j in run.trace if r < 4 and j is not None]
    assert set(contended) != {ids["0977123456"]}, (
        f"poison độc chiếm mọi lượt giao khi còn tranh chấp: {run.trace}")


def test_case_03_mutant_ignoring_live_leases_starves_healthy(
        tmp_path, monkeypatch):
    """ELIGIBILITY_IGNORES_LEASES → job lành chết đói.

    Hai phát hiện trên đường dựng mutant này, cả hai đáng giữ:

    1. Mutant "gỡ nhường lượt" KHÔNG giết được ca 03: với HAI worker, kẻ thua
       thấy poison vừa bị khoá tươi nên SELECT rơi xuống job lành. Starvation
       kiểu no-yield là hiện tượng batch-1 ĐƠN-worker (mutant đó sống ở
       test_fairness_contract).
    2. Mutant "dịch CẢ HAI đồng hồ đi xa" cũng KHÔNG giết được: dịch đều bảo
       toàn thứ tự tương đối, lock tươi tại `now` không bao giờ thoả
       `locked_at <= now - lease` với chính `now` đó.

    2b. Cả mutant "MỘT worker lệch đồng hồ" cũng không giết được: kẻ lệch
        cướp poison và chết, nhưng kẻ tỉnh vẫn thấy lock tươi nên rơi xuống
        job lành — MỘT worker đúng đắn là đủ để giữ tiến triển.

    Thuộc tính hai-worker đứng trên đúng một cột: ELIGIBILITY tôn trọng lease.
    Gỡ cột đó ở CẢ HAI worker — mọi hàng chưa kết thúc đều coi như giành được,
    UPDATE không vị từ — thì đầu hàng (poison) được giao cho mọi worker ở mọi
    vòng, và job lành chết đói thật."""
    from bio_agent_os.cognitive import outbox as outbox_module

    path, ids = _build(tmp_path, "c03m", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                          (HEALTHY_2, "t1")])
    real = outbox_module.ProjectionOutbox.claim

    from bio_agent_os.cognitive.outbox import JobStatus

    def eligibility_ignores_leases(self, worker_id, *, limit=1, now=None,
                                   lease_seconds=300.0, tenant_id=None, **kw):
        # Mọi hàng chưa kết thúc đều "đủ điều kiện", bất kể lease sống hay
        # chết — và UPDATE không vị từ bảo vệ. ĐẦU hàng (poison, created_at
        # nhỏ nhất) vì thế được giao cho MỌI worker ở MỌI vòng.
        import time as _t
        base = now if now is not None else _t.time()
        rows = self.conn.execute(
            "SELECT * FROM projection_outbox "
            "WHERE status IN ('pending', 'in_progress') "
            "ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        claimed = []
        for row in rows:
            self.conn.execute(
                "UPDATE projection_outbox SET status=?, locked_by=?, "
                "locked_at=? WHERE job_id=?",
                (JobStatus.IN_PROGRESS.value, worker_id, base, row["job_id"]))
            claimed.append(self._row(row, status=JobStatus.IN_PROGRESS.value,
                                     locked_by=worker_id, locked_at=base,
                                     attempts=row["attempts"] + 1))
        self.conn.commit()
        return claimed

    monkeypatch.setattr(outbox_module.ProjectionOutbox, "claim",
                        eligibility_ignores_leases)
    RoundWorkers(path, rounds=8, dying={ids["0977123456"]}).run()
    assert _done(path, "Hà Nội") == 0 and _done(path, "trưởng nhóm") == 0, (
        "mutant coi mọi lease như hết hạn mà job lành vẫn xong — thuộc tính 03 "
        "xanh vì lý do khác")


# ---------------------------------------------------------------------------
# 04 — hai tenant, hai worker scoped, claim đồng thời
# ---------------------------------------------------------------------------

def test_case_04_concurrent_scoped_workers_never_cross_tenants(tmp_path):
    path, ids = _build(tmp_path, "c04", [(POISON, "t1"), (HEALTHY_1, "t2"),
                                         (HEALTHY_2, "t2")])
    run = RoundWorkers(path, rounds=8, dying={ids["0977123456"]},
                       tenants=("t1", "t2")).run()

    assert run.overlap_rounds >= 1, "không có vòng claim chồng nhau"
    rows = _rows(path)
    # cross_tenant_claim_rate = 0 — đo trên SỔ GIAO VIỆC, không suy từ hàng cuối
    for round_no, worker, job_id in run.trace:
        if job_id is None:
            continue
        expected = "t1" if worker == "W0" else "t2"
        assert rows[job_id]["tenant_id"] == expected, (
            f"CROSS-TENANT: {worker} nhận job của {rows[job_id]['tenant_id']} "
            f"ở vòng {round_no}: {run.trace}")
    assert _done(path, "Hà Nội") == 1 and _done(path, "trưởng nhóm") == 1, (
        f"poison của t1 chặn hàng đợi t2: {rows}")


def test_case_04_mutant_dropping_tenant_scope_crosses_the_boundary(
        tmp_path, monkeypatch):
    """SKIP_TENANT_SCOPE_DURING_CONCURRENT_CLAIM → worker t1 chạm việc t2."""
    from bio_agent_os.cognitive import outbox as outbox_module

    path, ids = _build(tmp_path, "c04m", [(POISON, "t1"), (HEALTHY_1, "t2"),
                                          (HEALTHY_2, "t2")])
    real = outbox_module.ProjectionOutbox.claim

    def unscoped(self, worker_id, *, limit=1, now=None,
                 lease_seconds=300.0, tenant_id=None, **kw):
        return real(self, worker_id, limit=limit, now=now,
                    lease_seconds=lease_seconds, tenant_id=None, **kw)

    monkeypatch.setattr(outbox_module.ProjectionOutbox, "claim", unscoped)
    run = RoundWorkers(path, rounds=6, dying={ids["0977123456"]},
                       tenants=("t1", "t2")).run()
    rows = _rows(path)
    crossed = [(w, j) for _r, w, j in run.trace if j is not None
               and rows[j]["tenant_id"] != ("t1" if w == "W0" else "t2")]
    assert crossed, ("mutant bỏ scope mà không có claim xuyên tenant nào — "
                     "ca sạch 04 chưa chạm tới biên nó định đo")


# ---------------------------------------------------------------------------
# 05 — crash rồi peer thu hồi: đúng một lần, và việc XONG
# ---------------------------------------------------------------------------

def test_case_05_crash_then_peer_reclaims_exactly_once_and_completes(tmp_path):
    path, ids = _build(tmp_path, "c05", [(HEALTHY_1, "t1")])
    job_id = ids["Hà Nội"]

    setup = sqlite3.connect(str(path), timeout=15.0)
    setup.row_factory = sqlite3.Row
    memory_os = MemoryOS(path, projection_mode="outbox")
    try:
        worker = worker_for(memory_os, lease_seconds=LEASE)
        held = worker.outbox.claim(worker_id="se-chet", lease_seconds=LEASE,
                                   tenant_id="t1")
        assert [j.job_id for j in held] == [job_id], "chưa dựng được crash"
        locked_at = _rows(path)[job_id]["locked_at"]
        # ... worker chết tại đây, không fail(), không complete() ...

        race_now = float(locked_at) + LEASE + 1.0
        acks = []
        for peer in ("B", "C"):
            got = worker.outbox.claim(worker_id=peer, lease_seconds=LEASE,
                                      tenant_id="t1", now=race_now)
            acks.extend(got)
            for job in got:
                worker.process(job)
                if _rows(path)[job.job_id]["status"] == "in_progress":
                    worker.outbox.complete(job.job_id, now=race_now)

        assert len(acks) == 1, f"thu hồi không đúng-một-lần: {len(acks)}"
        assert _done(path, "Hà Nội") == 1, "thu hồi được mà việc không xong"
        row = _rows(path)[job_id]
        assert row["status"] == "completed", f"lease vĩnh viễn? {row}"
    finally:
        memory_os.close()
        setup.close()


# ---------------------------------------------------------------------------
# 07 — forget rơi vào giữa cửa sổ thu hồi (safety-critical)
# ---------------------------------------------------------------------------

def test_case_07_forget_landing_inside_the_reclaim_window_still_wins(tmp_path):
    """A đọc xong ứng viên thu hồi; forget đổ bộ; A tiếp tục và xử lý job.
    Chốt trước-khi-ghi phải chặn materialization của nội dung đã quên."""
    from bio_agent_os.cognitive import forgetting
    from test_multiworker_concurrency import BarrierConn

    path, ids = _build(tmp_path, "c07", [(POISON, "t1")])
    job_id = ids["0977123456"]

    memory_os = MemoryOS(path, projection_mode="outbox")
    try:
        worker = worker_for(memory_os, lease_seconds=LEASE)
        worker.outbox.claim(worker_id="da-chet", lease_seconds=LEASE,
                            tenant_id="t1")
        locked_at = _rows(path)[job_id]["locked_at"]
        race_now = float(locked_at) + LEASE + 1.0

        order = []

        def forget_mid_window():
            order.append("A đọc xong ứng viên thu hồi")
            report = forgetting.forget_derived(memory_os, subject="Hoàng Yến",
                                               needle="0977123456")
            order.append(f"forget đổ bộ giữa cửa sổ (note={report.note!r})")

        wrapped = BarrierConn(memory_os.memories.conn,
                              "SELECT * FROM projection_outbox",
                              forget_mid_window)
        outbox = worker.outbox
        real_conn = outbox.conn
        outbox.conn = wrapped
        try:
            got = outbox.claim(worker_id="A", lease_seconds=LEASE,
                               tenant_id="t1", now=race_now)
        finally:
            outbox.conn = real_conn
        order.append(f"A thu hồi {len(got)} job và xử lý")
        for job in got:
            worker.process(job)

        # admissibility: interleaving đúng thứ tự, và A THẬT SỰ xử lý job
        assert order[0].startswith("A đọc xong") and "forget đổ bộ" in order[1], order
        assert got and got[0].job_id == job_id, f"A không thu hồi đúng X: {order}"

        assert _done(path, "0977123456") == 0, (
            f"RESURRECTION: nội dung đã quên được materialize sau forget giữa "
            f"cửa sổ thu hồi: {order}")
    finally:
        memory_os.close()


def test_case_07_mutant_blind_burial_resurrects(tmp_path, monkeypatch):
    """IGNORE_RECONCILIATION_WORKER_BURIED_CHECK → phải hồi sinh. Nếu không,
    ca sạch 07 không có tải."""
    from bio_agent_os.cognitive import forgetting, reconciliation_worker

    path, ids = _build(tmp_path, "c07m", [(POISON, "t1")])
    memory_os = MemoryOS(path, projection_mode="outbox")
    try:
        worker = worker_for(memory_os, lease_seconds=LEASE)
        worker.outbox.claim(worker_id="da-chet", lease_seconds=LEASE,
                            tenant_id="t1")
        locked_at = _rows(path)[ids["0977123456"]]["locked_at"]
        forgetting.forget_derived(memory_os, subject="Hoàng Yến",
                                  needle="0977123456")
        monkeypatch.setattr(reconciliation_worker, "buried",
                            lambda conn, event_id: False)
        race_now = float(locked_at) + LEASE + 1.0
        for job in worker.outbox.claim(worker_id="A", lease_seconds=LEASE,
                                       tenant_id="t1", now=race_now):
            worker.process(job)
        assert _done(path, "0977123456") >= 1, (
            "mutant mù chốt chôn mà không hồi sinh — đường nguy hiểm của ca 07 "
            "không chạm được, ca sạch INVALID")
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# 06 — drain đồng thời (cuối cùng, vì phụ thuộc mọi primitive trên)
# ---------------------------------------------------------------------------

def test_case_06_concurrent_drains_materialize_exactly_once_and_conclude(tmp_path):
    from bio_agent_os.cognitive import projection_control as pc

    path, ids = _build(tmp_path, "c06", [(POISON, "t1"), (HEALTHY_1, "t1"),
                                         (HEALTHY_2, "t1")])
    # poison bị bỏ rơi với lease CÒN SỐNG suốt bài đo — không giải quyết được
    memory_os = MemoryOS(path, projection_mode="outbox")
    memory_os.events.outbox.claim("da-chet", lease_seconds=3600,
                                  tenant_id="t1")
    memory_os.close()

    results, started = {}, threading.Barrier(2)

    def drain_in_thread(tag):
        runtime = MemoryOS(path, projection_mode="outbox")
        try:
            started.wait(timeout=15)          # nhân chứng: hai drain cùng vào
            results[tag] = pc.drain(runtime, timeout_seconds=6.0,
                                    poll_seconds=0.05, worker_id=f"drain-{tag}")
        finally:
            runtime.close()

    threads = [threading.Thread(target=drain_in_thread, args=(t,))
               for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert set(results) == {"A", "B"}, f"một drain không hoàn thành: {results}"

    # exactly-once: mỗi nội dung lành đúng MỘT bản, đếm hàng thật
    assert _done(path, "Hà Nội") == 1, "double materialization"
    assert _done(path, "trưởng nhóm") == 1, "double materialization"
    assert _done(path, "0977123456") == 0

    # bounded outcome: KẾT LUẬN tường minh, không phải timeout-nghĩa-là-hỏng.
    # poison còn lease sống nên cả hai phải nói INCOMPLETE_UNRESOLVED.
    for tag, report in results.items():
        assert report["outcome"] == pc.DRAIN_INCOMPLETE_UNRESOLVED, (tag, report)
        assert report["unresolved"] == 1, (tag, report)


def test_case_06_duplicate_detector_actually_detects(tmp_path):
    """Mutant cho ĐƯỜNG QUAN SÁT: cấy một bản sao thật vào cognitive_memories
    và đòi máy đo của chính ca 06 nhìn thấy nó. Một bất biến exactly-once đo
    bằng một máy đếm mù thì xanh vô nghĩa."""
    path, _ids = _build(tmp_path, "c06m", [(HEALTHY_1, "t1")])
    memory_os = MemoryOS(path, projection_mode="outbox")
    try:
        worker = worker_for(memory_os, lease_seconds=LEASE)
        for job in worker.outbox.claim(worker_id="w", lease_seconds=LEASE,
                                       tenant_id="t1"):
            worker.process(job)
            worker.outbox.complete(job.job_id)
        assert _done(path, "Hà Nội") == 1
        conn = memory_os.memories.conn
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(cognitive_memories)")]
        select_list = ", ".join("'dup-1'" if c == "memory_id" else c
                                for c in cols)
        conn.execute(f"INSERT INTO cognitive_memories "
                     f"SELECT {select_list} FROM cognitive_memories LIMIT 1")
        conn.commit()
        assert _done(path, "Hà Nội") == 2, (
            "máy đếm không thấy bản sao được cấy — bất biến exactly-once của "
            "ca 06 đang đo bằng một nhân chứng mù")
    finally:
        memory_os.close()
