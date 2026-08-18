"""H1.4 — hai worker thật cùng chạm hàng đợi.

Mục tiêu **không** phải "chạy song song rồi xem có sập không". Mục tiêu là ép
đúng cái cửa sổ đua xảy ra, chứng minh nó đã xảy ra, rồi mới hỏi bất biến có
giữ hay không.

Nguyên tắc thiết kế, rút ra từ bốn phép đo hỏng trong lane H1 — mỗi lần đều là
một vị từ ĐÚNG đặt lên SAI tập hợp:

    một khẳng định chỉ có giá trị khi
      1. vị từ đúng
      2. tập đích đúng
      3. đường nguy hiểm ĐÃ thực sự chạy
      4. control chứng minh đường đó chạy được

Nên mỗi ca ở đây trả về một `RaceWitness`. Không chứng minh được interleaving
thì ca là **INVALID**, không phải PASS. Một ca xanh mà hai worker tình cờ không
gặp nhau thì chỉ nói rằng chúng không gặp nhau.

**Đồng hồ không được dùng làm nhân chứng.** `time.time()` trên máy này có
resolution 15.625 ms và đã hai lần làm hỏng kết luận trong lane này. Hai dấu
thời gian gần nhau không chứng minh gì. Nhân chứng phải là sự kiện đồng bộ
thật: A đã qua SELECT, B đã xong claim, A mới chạy tiếp UPDATE.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest
from dataclasses import dataclass, field
from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.outbox import ProjectionOutbox

TENANT = "t1"
POISON = "Số điện thoại của Hoàng Yến là 0977123456."
HEALTHY_A = "Bùi Cường sống ở Hà Nội."
HEALTHY_B = "Phạm Vy đang giữ chức trưởng nhóm."


@dataclass
class RaceWitness:
    """Biên bản của một lần đua. Thiếu nhân chứng thì ca INVALID."""

    race_point: str
    worker_a_reached: bool = False
    worker_b_reached: bool = False
    interleaving_confirmed: bool = False
    order: list = field(default_factory=list)
    final_state: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return (self.worker_a_reached and self.worker_b_reached
                and self.interleaving_confirmed)

    def require(self) -> None:
        assert self.valid, (
            f"INVALID — interleaving chưa được chứng minh tại {self.race_point}: "
            f"{self.__dict__}. Một ca xanh mà hai worker không gặp nhau chỉ nói "
            f"rằng chúng không gặp nhau.")


class _Cursor:
    """Proxy để chặn ĐÚNG sau khi hàng đã được đọc, trước khi UPDATE chạy."""

    def __init__(self, cursor, on_fetch):
        self._cursor, self._on_fetch = cursor, on_fetch

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._on_fetch()
        return rows

    def fetchone(self):
        # Phải móc CẢ `fetchone`. Bản đầu chỉ móc `fetchall`, nên hàng rào đặt
        # lên câu đếm `waiting` — vốn dùng `fetchone()` — không bao giờ bắn:
        # `__getattr__` chuyển thẳng xuống cursor thật. A chạy trọn vẹn, và ca
        # đua trông như đã diễn ra trong khi nó chưa từng diễn ra.
        row = self._cursor.fetchone()
        self._on_fetch()
        return row

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class BarrierConn:
    """Bọc connection thật để đặt một hàng rào tại một câu SQL cụ thể.

    Bọc chứ không sao chép `claim()`: nếu viết lại logic trong test thì thứ
    được đo là bản sao, không phải sản phẩm. Hàng rào nằm giữa `SELECT` ứng
    viên và `UPDATE` giành quyền — đúng cửa sổ mà một quan sát cũ có thể bị
    commit đè lên việc peer vừa làm.
    """

    def __init__(self, conn: sqlite3.Connection, marker: str, hook):
        self._conn, self._marker, self._hook = conn, marker, hook
        self.armed = True

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        if self.armed and self._marker in " ".join(sql.split()):
            self.armed = False                       # chỉ chặn lần đầu
            return _Cursor(cur, self._hook)
        return cur

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _store(tmp_path: Path, name: str, texts) -> Path:
    """Dựng store rồi ĐÓNG lại: mỗi worker sau đó mở connection riêng."""
    path = tmp_path / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    for text in texts:
        memory_os.observe(tenant_id=TENANT, actor="a", source="u",
                          content=text, workspace_id="w1")
    conn = memory_os.memories.conn
    for offset, row in enumerate(list(conn.execute(
            "SELECT job_id FROM projection_outbox ORDER BY created_at"))):
        conn.execute("UPDATE projection_outbox SET created_at=?, available_at=? "
                     "WHERE job_id=?", (1000.0 + offset, 1000.0 + offset, row[0]))
    conn.commit()
    memory_os.close()
    return path


def _open(path: Path) -> sqlite3.Connection:
    """Connection ĐỘC LẬP — production không dùng chung một đối tượng."""
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT job_id, status, attempts, locked_by, available_at "
        "FROM projection_outbox ORDER BY created_at")]


# ---------------------------------------------------------------------------
# 01 — hai worker cùng thu hồi một lease đã hết hạn
# ---------------------------------------------------------------------------

def test_case_01_double_reclaim_of_the_same_expired_job(tmp_path):
    """Bất biến: `double_claim_rate = 0`.

    Cửa sổ đua: A đọc xong danh sách ứng viên; B giành được job; A chạy tiếp
    `UPDATE` dựa trên quan sát đã cũ. Nếu `UPDATE` của A thành công thì cùng
    một job có hai chủ.
    """
    path = _store(tmp_path, "c01", [POISON])
    setup = _open(path)
    ProjectionOutbox(setup).claim(worker_id="da-chet", lease_seconds=0,
                                  tenant_id=TENANT)
    setup.commit()
    setup.close()

    witness = RaceWitness(race_point="claim: giữa SELECT ứng viên và UPDATE giành quyền")
    b_done = threading.Event()
    conn_a = _open(path)
    result = {}

    def a_paused_after_select():
        witness.worker_a_reached = True
        witness.order.append("A đọc xong ứng viên")
        b_done.wait(timeout=10.0)
        witness.order.append("A chạy tiếp UPDATE")

    def run_b():
        # Connection của B mở TRONG thread của B: sqlite không cho một
        # connection đi qua thread, và một worker thật cũng không làm thế.
        conn_b = _open(path)
        try:
            got = ProjectionOutbox(conn_b).claim(worker_id="B", lease_seconds=0,
                                                 tenant_id=TENANT)
            witness.worker_b_reached = True
            witness.order.append(f"B claim xong ({len(got)} job)")
            result["b"] = [j.job_id for j in got]
        finally:
            conn_b.close()
            b_done.set()

    # A phải tới hàng rào trước, nếu không thì không có đua nào cả.
    barrier_conn = BarrierConn(conn_a, "SELECT * FROM projection_outbox",
                               a_paused_after_select)
    thread_b = threading.Thread(target=lambda: (threading.Event().wait(0.05),
                                                run_b()))
    thread_b.start()
    got_a = ProjectionOutbox(barrier_conn).claim(worker_id="A", lease_seconds=0,
                                                 tenant_id=TENANT)
    thread_b.join(timeout=15.0)
    result["a"] = [j.job_id for j in got_a]

    witness.interleaving_confirmed = (
        witness.order[:1] == ["A đọc xong ứng viên"]
        and any("B claim xong" in o for o in witness.order)
        and witness.order[-1] == "A chạy tiếp UPDATE")
    witness.final_state = {"a": result.get("a"), "b": result.get("b"),
                           "rows": _rows(conn_a)}
    conn_a.close()

    witness.require()
    owners = [w for w, jobs in (("A", result["a"]), ("B", result["b"])) if jobs]
    assert len(owners) == 1, (
        f"double_claim_rate != 0 — cùng một job có hai chủ: {witness.final_state}")
    rows = witness.final_state["rows"]
    assert len(rows) == 1 and rows[0]["locked_by"] in ("A", "B"), rows


# ---------------------------------------------------------------------------
# 02 — nhường-lượt đụng claim đồng thời
# ---------------------------------------------------------------------------

def _same_job_race(tmp_path, name: str):
    """Dựng ca đua CÙNG MỘT JOB X, và tự chứng minh nó đã đúng hình.

    Khó nhất ở đây: **mọi `claim()` đều tự chạy bước nhường**, nên nếu B cũng
    là worker toàn cục thì B sẽ nhường X thay vì giành nó, và ca đua đi lạc
    sang một job khác. Bản đầu đúng như vậy — interleaving có xảy ra nhưng
    đường nguy hiểm không chạy, và ca xanh một cách vô nghĩa.

    Cách ép: X thuộc `t1`, Y thuộc `t2`. A là drain toàn cục nên nhìn thấy Y và
    quyết định nhường X. B scoped `t1` nên **không có ai để nhường**, và giành
    thẳng X. Fixture khẳng định `B lấy đúng X` trước khi cho A chạy tiếp; thiếu
    guard đó thì ca lại tự lừa mình.

    Hàng rào đặt SAU câu đếm `waiting` — tức sau khi A đã *quyết định* nhường,
    trước khi A *ghi*. Đó mới là cửa sổ đua thật.
    """
    path = tmp_path / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    memory_os.observe(tenant_id="t1", actor="a", source="u", content=POISON,
                      workspace_id="w1")
    memory_os.observe(tenant_id="t2", actor="a", source="u", content=HEALTHY_A,
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
    ProjectionOutbox(setup).claim(worker_id="da-chet", lease_seconds=0,
                                  tenant_id="t1")
    setup.commit()
    setup.close()

    witness = RaceWitness(
        race_point="_yield_expired_leases: sau khi quyết định nhường X, trước khi ghi")
    b_done = threading.Event()
    conn_a = _open(path)
    claimed_by_b: dict = {}

    def a_decided():
        witness.worker_a_reached = True
        witness.order.append("A đã quyết định nhường X")
        b_done.wait(timeout=10.0)
        witness.order.append("A chạy tiếp stale UPDATE trên X")

    def run_b():
        conn_b = _open(path)
        try:
            got = ProjectionOutbox(conn_b).claim(worker_id="B-SONG",
                                                 lease_seconds=0, tenant_id="t1")
            claimed_by_b["ids"] = [j.job_id for j in got]
            witness.worker_b_reached = True
            witness.order.append(f"B claim xong: {claimed_by_b['ids']}")
        finally:
            conn_b.close()
            b_done.set()

    barrier = BarrierConn(conn_a, "SELECT COUNT(*) FROM projection_outbox",
                          a_decided)
    thread_b = threading.Thread(
        target=lambda: (threading.Event().wait(0.05), run_b()))
    thread_b.start()
    ProjectionOutbox(barrier).claim(worker_id="A-DRAIN", lease_seconds=0,
                                    tenant_id=None)
    thread_b.join(timeout=15.0)

    witness.interleaving_confirmed = (
        witness.order[:1] == ["A đã quyết định nhường X"]
        and any("B claim xong" in o for o in witness.order)
        and witness.order[-1] == "A chạy tiếp stale UPDATE trên X")
    witness.require()
    # GUARD: đường nguy hiểm chỉ tồn tại nếu B giành ĐÚNG X.
    assert claimed_by_b.get("ids") == [job_x], (
        f"INVALID — B không giành đúng X, nên stale UPDATE của A không nhắm vào "
        f"lease sống nào: {claimed_by_b} vs X={job_x}")

    row = dict(conn_a.execute(
        "SELECT status, locked_by, locked_at, available_at FROM projection_outbox "
        "WHERE job_id=?", (job_x,)).fetchone())
    conn_a.close()
    witness.final_state = row
    return witness, row


def test_case_02_a_stale_yield_cannot_destroy_a_live_lease(tmp_path):
    """Bất biến: `stolen_lease_rate = 0`.

    Ca này từng là `skip` mang một câu hỏi mở, rồi thành một P0 có tái hiện
    sạch. Đo được TRƯỚC bản vá, ba bước có nhân chứng đồng bộ:

        1. A đã quyết định nhường X
        2. B claim xong X            ← lease sống
        3. A chạy tiếp stale UPDATE

        X sau khi A ghi:  status=pending  locked_by=None  available_at=<tương lai>

    B vẫn tin X là của mình và đang xử lý, trong khi hàng đợi đã coi X vô chủ.
    Bản vá là compare-and-set tại chính điểm chuyển trạng thái — cùng khuôn với
    `claim()`, không mở transaction lớn.
    """
    witness, row = _same_job_race(tmp_path, "c02-clean")

    assert row["status"] == "in_progress", (
        f"lease sống của B bị stale UPDATE phá: {row} | {witness.order}")
    assert row["locked_by"] == "B-SONG", (
        f"quyền sở hữu của B bị cướp: {row}")
    assert row["available_at"] == 1000.0, (
        f"`available_at` của một lease sống bị đẩy đi: {row}")


def test_mutant_yield_ignores_current_claim_state(tmp_path, monkeypatch):
    """YIELD_IGNORES_CURRENT_CLAIM_STATE — bỏ vị từ bảo vệ thì lease sống vỡ.

    Mutant tháo đúng thứ bản vá thêm vào: `UPDATE` quay lại ghi theo mỗi
    `job_id`. Nếu ca sạch ở trên xanh mà mutant này không phá được gì, thì ca
    đó đang xanh vì lý do khác — không phải vì CAS.
    """
    from bio_agent_os.cognitive import outbox as outbox_module

    def unguarded_yield(self, now, stale_before, tenant_id, *,
                        yield_base=1.0, yield_cap=60.0):
        """Bản trước khi vá: ghi theo mỗi `job_id`, không hỏi hàng còn nguyên."""
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
        out = []
        for row in expired:
            wait = min(yield_base * max(1, int(row["attempts"])), yield_cap)
            self.conn.execute(
                "UPDATE projection_outbox SET status='pending', locked_by=NULL, "
                "locked_at=NULL, available_at=? WHERE job_id=?",
                (now + wait, row["job_id"]))
            out.append(str(row["job_id"]))
        self.conn.commit()
        return out

    monkeypatch.setattr(outbox_module.ProjectionOutbox,
                        "_yield_expired_leases", unguarded_yield)
    witness, row = _same_job_race(tmp_path, "c02-mutant")
    monkeypatch.undo()

    assert row["status"] == "pending" and row["locked_by"] is None, (
        f"mutant KHÔNG phá được lease sống — ca sạch đang xanh vì lý do khác, "
        f"không phải vì compare-and-set: {row} | {witness.order}")
