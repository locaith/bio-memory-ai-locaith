"""Bao nhieu byte WAL cho MOT luot ghi, va con so do co doi theo kich thuoc database khong?

Run 8 chet vi WAL nam tren nguong cung 634 giay du co che thu hoi da chay
(48/68 lan thanh cong). Nghia la benh khong nam o cho don dep — no nam o cho
moi luot ghi sinh ra bao nhieu WAL.

Phep do: dung database lon dan, va tai moi moc kich thuoc thi
  1. TRUNCATE cho WAL ve 0
  2. ghi N luot observe+remember
  3. do byte WAL sinh ra
  4. chia cho N

Neu con so nay TANG theo kich thuoc database thi nguyen nhan la tinh cuc bo cua
chi muc: moi lan chen, B-tree phai lam ban nhung trang nam rai rac, va cang lon
thi cang rai. Neu no KHONG doi thi day chi la van de khoi luong.

Do them bang dbstat de biet trang nam o bang/chi muc nao — quy trach nhiem chu
khong doan.

KHONG dung vao database cua Run 1-8. Dung database moi trong scratchpad.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, r"C:\locaith\bio-memory-ai-locaith")
os.environ.setdefault("BIO_AGENT_TENANT_ID", "bench")
os.environ.setdefault("BIO_AGENT_WORKSPACE_ID", "wal-per-write")

from bio_agent_os.cognitive.facade import MemoryOS          # noqa: E402
from bio_agent_os.cognitive.models import MemoryType         # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "wal_per_write.json")
DB = Path(sys.argv[2] if len(sys.argv) > 2 else "walbench.db")
MODE = sys.argv[3] if len(sys.argv) > 3 else "shadow"
#: Moc kich thuoc de do. Moi moc: ghi cho toi khi dat, roi do mau.
MILESTONES = [2_000, 10_000, 25_000, 50_000, 100_000, 175_000, 250_000]
SAMPLE = 1_000          # so luot ghi trong mot lan do
BODY = "x" * 380        # xap xi kich thuoc mot ban ghi that

for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()


def sizes(db: Path) -> tuple[int, int]:
    def s(suffix: str) -> int:
        p = Path(str(db) + suffix)
        try:
            return p.stat().st_size
        except OSError:
            return 0
    return s(""), s("-wal")


def write_n(os_: MemoryOS, n: int, start: int) -> None:
    for i in range(start, start + n):
        ev = os_.observe(tenant_id="bench", actor="a", source="unit",
                         content=f"ban ghi {i} {BODY}", workspace_id="wal-per-write")
        os_.remember(event=ev, memory_type=MemoryType.EPISODIC,
                     content=f"ban ghi {i} {BODY}")


def truncate(conn: sqlite3.Connection) -> None:
    for _ in range(6):
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row and int(row[0]) == 0:
            return
        time.sleep(0.2)


def dbstat(conn: sqlite3.Connection) -> dict[str, int]:
    """Trang thuoc ve bang/chi muc nao. Chi chay duoc neu SQLite bat dbstat."""
    try:
        rows = conn.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(r[0]): int(r[1]) for r in rows}


def main() -> int:
    os_ = MemoryOS(DB, projection_mode=MODE)
    conn = os_.events.conn
    written = 0
    results: list[dict] = []
    print(f"che do={MODE}  db={DB}", flush=True)
    print(f"{'su kien':>9} {'db_MB':>8} {'WAL/luot':>10} {'byte/luot':>11} {'giay':>7}", flush=True)

    for target in MILESTONES:
        if written < target:
            write_n(os_, target - written, written)
            written = target

        truncate(conn)
        db_before, wal_before = sizes(DB)
        t0 = time.perf_counter()
        write_n(os_, SAMPLE, written)
        elapsed = time.perf_counter() - t0
        written += SAMPLE
        db_after, wal_after = sizes(DB)
        per_write = (wal_after - wal_before) / SAMPLE

        row = {
            "events": written,
            "db_bytes": db_after,
            "wal_bytes_for_sample": wal_after - wal_before,
            "wal_bytes_per_write": round(per_write, 1),
            "sample_writes": SAMPLE,
            "seconds": round(elapsed, 2),
            "writes_per_second": round(SAMPLE / elapsed, 1) if elapsed else None,
        }
        results.append(row)
        print(f"{written:>9,} {db_after/1048576:>8.0f} {wal_after-wal_before:>10,} "
              f"{per_write:>11,.0f} {elapsed:>7.1f}", flush=True)
        OUT.write_text(json.dumps({"mode": MODE, "samples": results},
                                  ensure_ascii=False, indent=2), encoding="utf-8")

    attribution = dbstat(conn)
    payload = {
        "mode": MODE,
        "page_size": conn.execute("PRAGMA page_size").fetchone()[0],
        "samples": results,
        "page_attribution": attribution,
        "db_bytes_final": sizes(DB)[0],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if attribution:
        total = sum(attribution.values()) or 1
        print("\ntrang thuoc ve dau (top 12):", flush=True)
        for name, b in list(attribution.items())[:12]:
            print(f"  {b/1048576:8.1f} MB  {100*b/total:5.1f}%  {name}", flush=True)
    os_.close()
    print(f"\nda ghi {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
