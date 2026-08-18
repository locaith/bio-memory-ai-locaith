"""H1.4 — 01/02 ở mức TIẾN TRÌNH, không phải thread.

Khoá file của SQLite là chuyện của tiến trình, không phải của thread: hai
thread trong cùng interpreter chia sẻ page cache và một phần trạng thái khoá,
nên một ca xanh ở mức thread chưa nói gì về mức process. Nếu thread PASS mà
process FAIL thì đó là phát hiện thật về ranh giới runtime, không phải test
lệch nhau.

Đồng bộ bằng `multiprocessing.Event`, không bằng `sleep`. Nhân chứng là sự kiện
đồng bộ đã xảy ra, không phải hai dấu thời gian gần nhau — đồng hồ ở máy này có
resolution 15.625 ms và đã hai lần làm hỏng kết luận trong lane này.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS

POISON = "Số điện thoại của Hoàng Yến là 0977123456."
HEALTHY_A = "Bùi Cường sống ở Hà Nội."


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------
# thân của hai tiến trình — phải ở mức module để Windows `spawn` import lại được
# --------------------------------------------------------------------------

def _worker_a(path, tenant, a_ready, b_committed, out):
    """A: dừng lại ngay sau khi đọc xong, chờ B, rồi mới ghi."""
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

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

    def at_barrier():
        # Ghi riêng: `a_ready` còn được set trong `finally` để B không treo khi
        # A hỏng sớm, nên nó KHÔNG chứng minh hàng rào đã bắn. Cờ này thì có.
        out["a_hit_barrier"] = True
        a_ready.set()
        b_committed.wait(timeout=30.0)
        out["a_resumed_after_b"] = True

    conn = _open(path)
    try:
        wrapped = _Conn(conn, "SELECT * FROM projection_outbox", at_barrier)
        got = ProjectionOutbox(wrapped).claim(worker_id="A", lease_seconds=0,
                                              tenant_id=tenant)
        out["a_claimed"] = [j.job_id for j in got]
    finally:
        conn.close()
        a_ready.set()                     # không để B treo nếu A hỏng sớm


def _worker_b(path, tenant, a_ready, b_committed, out):
    """B: chờ A qua hàng rào rồi mới giành, và commit trước khi A chạy tiếp."""
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    conn = _open(path)
    try:
        a_ready.wait(timeout=30.0)
        got = ProjectionOutbox(conn).claim(worker_id="B", lease_seconds=0,
                                           tenant_id=tenant)
        conn.commit()
        out["b_claimed"] = [j.job_id for j in got]
    finally:
        conn.close()
        b_committed.set()


def _build(tmp_path: Path, name: str, specs) -> Path:
    path = tmp_path / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    for content, tenant in specs:
        memory_os.observe(tenant_id=tenant, actor="a", source="u",
                          content=content, workspace_id="w1")
    conn = memory_os.memories.conn
    for offset, row in enumerate(list(conn.execute(
            "SELECT job_id FROM projection_outbox ORDER BY created_at"))):
        conn.execute("UPDATE projection_outbox SET created_at=?, available_at=? "
                     "WHERE job_id=?", (1000.0 + offset, 1000.0 + offset, row[0]))
    conn.commit()
    memory_os.close()
    return path


@pytest.mark.skipif(mp.get_start_method(allow_none=True) not in (None, "spawn"),
                    reason="cần start method mặc định của nền tảng")
def test_case_01_process_level_double_reclaim(tmp_path):
    """Bất biến ở mức tiến trình: `double_claim_ack_rate = 0`.

    Không chỉ đọc hàng cuối. Hàng cuối chỉ giữ MỘT chủ, nhưng câu hỏi thật là
    có hai tiến trình nào cùng được hệ thống nói "job này là của anh" hay
    không — tức hai `claim()` cùng trả về job đó.
    """
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    path = _build(tmp_path, "p01", [(POISON, "t1")])
    setup = _open(path)
    ProjectionOutbox(setup).claim(worker_id="da-chet", lease_seconds=0,
                                  tenant_id="t1")
    setup.commit()
    setup.close()

    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    out = manager.dict()
    a_ready, b_committed = ctx.Event(), ctx.Event()

    proc_a = ctx.Process(target=_worker_a,
                         args=(str(path), "t1", a_ready, b_committed, out))
    proc_b = ctx.Process(target=_worker_b,
                         args=(str(path), "t1", a_ready, b_committed, out))
    proc_a.start()
    proc_b.start()
    proc_a.join(timeout=60)
    proc_b.join(timeout=60)

    result = dict(out)
    # Nhân chứng: cả hai đã tới hàng rào của mình.
    assert result.get("a_hit_barrier"), (
        f"INVALID — A chưa từng dừng ở hàng rào, nên không có cuộc đua nào: "
        f"{result}")
    assert result.get("a_resumed_after_b"), (
        f"INVALID — A không chạy tiếp SAU khi B commit: {result}")
    assert b_committed.is_set(), f"INVALID — B chưa commit: {result}"
    assert "a_claimed" in result and "b_claimed" in result, (
        f"INVALID — một tiến trình không hoàn thành: {result}")

    acks = [w for w in ("a_claimed", "b_claimed") if result.get(w)]
    assert len(acks) == 1, (
        f"double_claim_ack_rate != 0 — hai tiến trình cùng được cấp quyền xử "
        f"lý: {result}")

    conn = _open(path)
    rows = [dict(r) for r in conn.execute(
        "SELECT status, locked_by, attempts FROM projection_outbox")]
    conn.close()
    assert len(rows) == 1 and rows[0]["locked_by"] in ("A", "B"), rows
