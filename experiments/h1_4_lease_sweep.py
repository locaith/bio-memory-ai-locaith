"""H1.4 — double-claim ở mức tiến trình có bám theo biên phân giải đồng hồ không?

Giả thuyết dẫn đầu: `lease_seconds` nhỏ hơn hoặc bằng lượng tử đồng hồ khiến
một lease VỪA được lấy vẫn thoả vị từ "đã hết hạn", nên hai tiến trình cùng
được cấp quyền — mà vị từ thì không sai một chữ nào.

Vòng này hoặc đóng root cause, hoặc bác hẳn giả thuyết. Trường bác bỏ là rõ
ràng: nếu có double-claim mà `locked_at_B > stale_before_A` thì clock aliasing
không giải thích được, và phải quay lại điều tra ngữ nghĩa CAS/transaction ở
mức nhiều tiến trình.

Không lấy tài liệu nền tảng làm nguồn sự thật cho đồng hồ — đo trực tiếp.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def clock_capability(samples: int = 200_000) -> dict:
    """Đo đồng hồ thật, không tin thông số công bố."""
    reported = time.get_clock_info("time").resolution
    deltas, equal_pairs = [], 0
    prev = time.time()
    for _ in range(samples):
        cur = time.time()
        if cur == prev:
            equal_pairs += 1
        elif cur > prev:
            deltas.append(cur - prev)
        prev = cur
    return {
        "source": "time.time",
        "reported_resolution": reported,
        "observed_min_positive_delta": min(deltas) if deltas else None,
        "repeated_equal_timestamps": equal_pairs,
        "samples": samples,
    }


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


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


def _record_now(out, key):
    """Bắt đúng `now` mà `claim()` dùng, thay vì lấy một mốc gần đúng bên ngoài.

    `claim()` gọi `time.time()` một lần ở đầu; giá trị ấy thành `now` và
    `stale_before = now - lease`. Đo từ ngoài thì lệch một khoảng không biết,
    mà câu hỏi ở đây tính bằng mili giây.
    """
    from bio_agent_os.cognitive import outbox as outbox_module

    real = outbox_module.time.time
    state = {"first": True}

    class _Time:
        def __getattr__(self, name):
            return getattr(real.__self__ if hasattr(real, "__self__") else time,
                           name)

        def time(self):
            value = real()
            if state["first"]:
                state["first"] = False
                out[key] = value
            return value

    outbox_module.time = _Time()


def worker_a(path, lease, a_ready, b_done, out):
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    _record_now(out, "now_A")

    def at_barrier():
        out["a_hit_barrier"] = True
        a_ready.set()
        b_done.wait(timeout=30.0)
        out["a_resumed_after_b"] = True

    conn = _open(path)
    try:
        wrapped = _Conn(conn, "SELECT * FROM projection_outbox", at_barrier)
        got = ProjectionOutbox(wrapped).claim(worker_id="A", lease_seconds=lease,
                                              tenant_id="t1")
        out["a_ack"] = [j.job_id for j in got]
    except Exception as exc:                                   # noqa: BLE001
        out["a_error"] = repr(exc)
    finally:
        conn.close()
        a_ready.set()


def worker_b(path, lease, a_ready, b_done, out):
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    _record_now(out, "now_B")
    conn = _open(path)
    try:
        a_ready.wait(timeout=30.0)
        got = ProjectionOutbox(conn).claim(worker_id="B", lease_seconds=lease,
                                           tenant_id="t1")
        conn.commit()
        out["b_ack"] = [j.job_id for j in got]
        row = conn.execute(
            "SELECT locked_at FROM projection_outbox").fetchone()
        out["locked_at_B"] = row["locked_at"] if row else None
    except Exception as exc:                                   # noqa: BLE001
        out["b_error"] = repr(exc)
    finally:
        conn.close()
        b_done.set()


def build(tmp: Path, name: str) -> Path:
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.outbox import ProjectionOutbox

    path = tmp / f"{name}.db"
    memory_os = MemoryOS(path, projection_mode="outbox")
    memory_os.observe(tenant_id="t1", actor="a", source="u",
                      content="Số của Hoàng Yến là 0977123456.",
                      workspace_id="w1")
    conn = memory_os.memories.conn
    conn.execute("UPDATE projection_outbox SET created_at=1000, available_at=1000")
    conn.commit()
    memory_os.close()

    setup = _open(path)
    ProjectionOutbox(setup).claim(worker_id="da-chet", lease_seconds=0,
                                  tenant_id="t1")
    setup.commit()
    setup.close()
    return path


def one_run(ctx, manager, tmp: Path, name: str, lease: float) -> dict:
    path = build(tmp, name)
    out = manager.dict()
    a_ready, b_done = ctx.Event(), ctx.Event()
    pa = ctx.Process(target=worker_a, args=(str(path), lease, a_ready, b_done, out))
    pb = ctx.Process(target=worker_b, args=(str(path), lease, a_ready, b_done, out))
    pa.start()
    pb.start()
    pa.join(timeout=60)
    pb.join(timeout=60)

    r = dict(out)
    conn = _open(path)
    row = conn.execute("SELECT locked_by FROM projection_outbox").fetchone()
    conn.close()
    r["final_locked_by"] = row["locked_by"] if row else None

    now_a, locked_b = r.get("now_A"), r.get("locked_at_B")
    r["stale_before_A"] = (now_a - lease) if now_a is not None else None
    r["double_ack"] = bool(r.get("a_ack")) and bool(r.get("b_ack"))
    # Chữ ký nhân quả của giả thuyết: lease B VỪA lấy vẫn thoả vị từ hết hạn
    # của A. Nếu mọi double-ack đều có cờ này, và mọi run sạch đều không, thì
    # giả thuyết có bằng chứng nhân quả chứ không chỉ tương quan.
    r["fresh_lease_misclassified"] = (
        bool(r.get("b_ack")) and locked_b is not None
        and r["stale_before_A"] is not None
        and locked_b <= r["stale_before_A"])
    r["valid"] = bool(r.get("a_hit_barrier")) and bool(r.get("a_resumed_after_b"))
    return r


def main() -> None:
    import tempfile

    cap = clock_capability()
    tick = cap["observed_min_positive_delta"] or 0.015625
    print("=== ĐỒNG HỒ, đo trực tiếp ===")
    for k, v in cap.items():
        print(f"  {k:<28} {v}")
    print(f"  tick dùng cho sweep         {tick:.9f}")

    leases = [("0", 0.0), ("0.5 tick", tick * 0.5), ("1 tick", tick),
              ("2 ticks", tick * 2), ("50ms", 0.05)]
    runs = 25
    ctx = mp.get_context("spawn")
    manager = ctx.Manager()
    root = Path(tempfile.mkdtemp())

    print(f"\n=== SWEEP, {runs} lần mỗi mức ===")
    print(f"{'lease':<10} {'double_ack':<12} {'invalid':<9} "
          f"{'misclassified':<14} khớp chữ ký")
    table = {}
    for label, lease in leases:
        rows = [one_run(ctx, manager, root, f"s{label}{i}".replace(" ", ""), lease)
                for i in range(runs)]
        table[label] = rows
        valid = [r for r in rows if r["valid"]]
        dbl = [r for r in valid if r["double_ack"]]
        mis = [r for r in valid if r["fresh_lease_misclassified"]]
        agree = sum(1 for r in valid
                    if r["double_ack"] == r["fresh_lease_misclassified"])
        print(f"{label:<10} {len(dbl)}/{len(valid):<10} {runs - len(valid):<9} "
              f"{len(mis)}/{len(valid):<12} {agree}/{len(valid)}")

    print("\n=== KIỂM CHỨNG NHÂN QUẢ ===")
    every = [r for rows in table.values() for r in rows if r["valid"]]
    dbl = [r for r in every if r["double_ack"]]
    counter = [r for r in dbl if not r["fresh_lease_misclassified"]]
    clean_but_flagged = [r for r in every
                         if r["fresh_lease_misclassified"] and not r["double_ack"]]
    print(f"  tổng run hợp lệ                    {len(every)}")
    print(f"  double-ack                         {len(dbl)}")
    print(f"  double-ack KHÔNG có chữ ký         {len(counter)}   <- bác giả thuyết")
    print(f"  có chữ ký nhưng KHÔNG double-ack   {len(clean_but_flagged)}")
    if counter:
        print("\n  >>> GIẢ THUYẾT BỊ BÁC: có double-claim với locked_at_B > stale_before_A")
        for r in counter[:3]:
            print(f"      now_A={r.get('now_A')} stale={r.get('stale_before_A')} "
                  f"locked_at_B={r.get('locked_at_B')}")
    elif dbl:
        print("\n  >>> MỌI double-claim đều mang chữ ký sub-clock lease")
    else:
        print("\n  >>> không tái hiện được double-claim trong vòng này")


if __name__ == "__main__":
    main()
