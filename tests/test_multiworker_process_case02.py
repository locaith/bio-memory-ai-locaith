"""H1.4 — case 02 ở mức TIẾN TRÌNH: stale yield không được phá lease sống.

Đóng nốt phần dư đã khai trong `H1_4_MULTIWORKER_REPORT.md`: cơ chế CAS của
bước nhường trùng shape với `claim()` (đã process-proven ở case 01), nhưng đó
là suy luận đồng dạng — file này biến nó thành phép đo riêng, trước lần đầu
tiên runtime chạm store thật.

Kịch bản, hai tiến trình thật, connection riêng, barrier bằng
`multiprocessing.Event` — không sleep, không suy từ timestamp:

    A (drain toàn cục) đọc xong danh sách lease hết hạn, QUYẾT ĐỊNH nhường X
    B (scoped t1)      giành đúng X với lease sống, commit
    A                  chạy tiếp stale UPDATE trên X

Sạch: CAS của A trả rowcount 0, lease của B nguyên vẹn.
Mutant (bỏ vị từ bảo vệ, tiêm trong chính tiến trình A): lease B bị phá.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS

POISON = "Số điện thoại của Hoàng Yến là 0977123456."
HEALTHY = "Bùi Cường sống ở Hà Nội."
LEASE = 300.0


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def _build(tmp_path: Path, name: str):
    """X thuộc t1 (lease hết hạn), Y thuộc t2 (pending) — A toàn cục thấy Y
    nên quyết định nhường X; B scoped t1 không có ai để nhường nên giành X."""
    path = tmp_path / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    memory_os.observe(tenant_id="t1", actor="a", source="u", content=POISON,
                      workspace_id="w1")
    memory_os.observe(tenant_id="t2", actor="a", source="u", content=HEALTHY,
                      workspace_id="w1")
    conn = memory_os.memories.conn
    job_x = next(dict(r)["job_id"] for r in conn.execute(
        "SELECT job_id, tenant_id FROM projection_outbox")
        if dict(r)["tenant_id"] == "t1")
    conn.execute("UPDATE projection_outbox SET created_at=1000, available_at=1000 "
                 "WHERE tenant_id='t1'")
    conn.execute("UPDATE projection_outbox SET created_at=1001, available_at=1001 "
                 "WHERE tenant_id='t2'")
    conn.commit()
    memory_os.close()

    setup = _open(path)
    from bio_agent_os.cognitive.outbox import ProjectionOutbox
    ProjectionOutbox(setup).claim(worker_id="da-chet", lease_seconds=LEASE,
                                  tenant_id="t1")
    setup.commit()
    locked_at = setup.execute(
        "SELECT locked_at FROM projection_outbox WHERE locked_by='da-chet'"
    ).fetchone()[0]
    setup.close()
    race_now = float(locked_at) + LEASE + 1.0
    assert race_now > float(locked_at) + LEASE
    return path, job_x, race_now


# --------------------------------------------------------------------------
# thân tiến trình — mức module cho Windows spawn
# --------------------------------------------------------------------------

def _proc_a(path, race_now, mutant, a_decided, b_done, out):
    from bio_agent_os.cognitive import outbox as outbox_module
    from bio_agent_os.cognitive.outbox import JobStatus, ProjectionOutbox

    if mutant:
        # YIELD_IGNORES_CURRENT_CLAIM_STATE — bản trước bản vá CAS, tiêm ngay
        # trong tiến trình A (monkeypatch không đi qua ranh giới process).
        def unguarded(self, now, stale_before, tenant_id, *,
                      yield_base=1.0, yield_cap=60.0):
            clause = " AND tenant_id = ?" if tenant_id is not None else ""
            args = (tenant_id,) if tenant_id is not None else ()
            expired = self.conn.execute(
                f"SELECT job_id, attempts FROM projection_outbox "
                f"WHERE status = 'in_progress' "
                f"AND (locked_at IS NULL OR locked_at <= ?) {clause}",
                (stale_before, *args)).fetchall()
            if not expired:
                return []
            waiting = self.conn.execute(
                f"SELECT COUNT(*) FROM projection_outbox "
                f"WHERE status = 'pending' AND available_at <= ? {clause}",
                (now, *args)).fetchone()[0]
            if not waiting:
                return []
            out_ids = []
            for row in expired:
                wait = min(yield_base * max(1, int(row["attempts"])), yield_cap)
                self.conn.execute(
                    "UPDATE projection_outbox SET status='pending', "
                    "locked_by=NULL, locked_at=NULL, available_at=? "
                    "WHERE job_id=?", (now + wait, row["job_id"]))
                out_ids.append(str(row["job_id"]))
            self.conn.commit()
            return out_ids

        outbox_module.ProjectionOutbox._yield_expired_leases = unguarded

    class _Cur:
        def __init__(self, cur, hook):
            self._cur, self._hook = cur, hook

        def fetchall(self):
            rows = self._cur.fetchall()
            self._hook()
            return rows

        def fetchone(self):
            row = self._cur.fetchone()
            self._hook()
            return row

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class _Conn:
        def __init__(self, conn, marker, hook):
            self._conn, self._marker, self._hook = conn, marker, hook
            self.armed = True

        def execute(self, sql, params=()):
            cur = self._conn.execute(sql, params)
            if self.armed and self._marker in " ".join(sql.split()):
                self.armed = False
                return _Cur(cur, self._hook)
            return cur

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def decided():
        out["a_decided"] = True
        a_decided.set()
        b_done.wait(timeout=30.0)
        out["a_resumed_after_b"] = True

    conn = _open(path)
    try:
        # Hàng rào đặt SAU câu đếm `waiting` — sau khi A đã QUYẾT ĐỊNH nhường,
        # trước khi A GHI.
        wrapped = _Conn(conn, "SELECT COUNT(*) FROM projection_outbox", decided)
        ProjectionOutbox(wrapped).claim(worker_id="A-DRAIN",
                                        lease_seconds=LEASE,
                                        tenant_id=None, now=race_now)
    finally:
        conn.close()
        a_decided.set()


def _proc_b(path, race_now, a_decided, b_done, out):
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    conn = _open(path)
    try:
        a_decided.wait(timeout=30.0)
        got = ProjectionOutbox(conn).claim(worker_id="B-SONG",
                                           lease_seconds=LEASE,
                                           tenant_id="t1", now=race_now)
        conn.commit()
        out["b_claimed"] = [j.job_id for j in got]
    finally:
        conn.close()
        b_done.set()


def _race(tmp_path, name: str, *, mutant: bool):
    path, job_x, race_now = _build(tmp_path, name)
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    out = manager.dict()
    a_decided, b_done = ctx.Event(), ctx.Event()
    pa = ctx.Process(target=_proc_a,
                     args=(str(path), race_now, mutant, a_decided, b_done, out))
    pb = ctx.Process(target=_proc_b,
                     args=(str(path), race_now, a_decided, b_done, out))
    pa.start()
    pb.start()
    pa.join(timeout=60)
    pb.join(timeout=60)
    result = dict(out)

    # admissibility — thiếu nhân chứng nào thì INVALID, không phải PASS
    assert result.get("a_decided"), f"INVALID — A chưa quyết định nhường: {result}"
    assert result.get("a_resumed_after_b"), (
        f"INVALID — A không chạy tiếp SAU B: {result}")
    assert result.get("b_claimed") == [job_x], (
        f"INVALID — B không giành đúng X: {result} vs {job_x}")

    conn = _open(path)
    row = dict(conn.execute(
        "SELECT status, locked_by, available_at FROM projection_outbox "
        "WHERE job_id=?", (job_x,)).fetchone())
    conn.close()
    return row


def test_case_02_process_level_stale_yield_cannot_destroy_live_lease(tmp_path):
    row = _race(tmp_path, "c02p-clean", mutant=False)
    assert row["status"] == "in_progress" and row["locked_by"] == "B-SONG", (
        f"lease sống của B bị stale yield phá ở mức TIẾN TRÌNH: {row}")
    assert row["available_at"] == 1000.0, f"available_at bị đẩy đi: {row}"


def test_case_02_process_level_mutant_destroys_live_lease(tmp_path):
    row = _race(tmp_path, "c02p-mutant", mutant=True)
    assert row["status"] == "pending" and row["locked_by"] is None, (
        f"mutant bỏ vị từ mà lease B vẫn nguyên — ca sạch xanh vì lý do khác: "
        f"{row}")
