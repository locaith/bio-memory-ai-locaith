"""INCIDENT RCA LANE 1 — chuỗi nhân quả corruption, clone dùng-một-lần.

Giả thuyết: handle sống + WAL có frames đã commit + copy2 đè main db bằng
snapshot cũ + handle cũ tiếp tục hoạt động → WAL cũ áp lên main db khác
lineage → index/table disagreement.

Ba nhánh, không trộn:
  TREATMENT   handle sống + WAL có frames + copy2 đè + handle cũ chạy tiếp
  CONTROL C   đóng hết connection rồi mới copy → phải sạch
  CONTROL D   handle sống nhưng WAL RỖNG (đã checkpoint TRUNCATE) + copy2 đè
              → trả lời "chỉ đè thôi có đủ gây hỏng không?"

Nhân chứng: kích thước/frame WAL đo TRƯỚC copy, handle còn mở, thứ tự bước.
Không kết luận từ timestamp.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

WORK = Path(__file__).parent / "rca_lane1"
TRIALS = 5
SIGNATURE = ("missing from index", "never used")


def build_s0(tag: str) -> tuple[Path, Path]:
    db = WORK / f"{tag}.db"
    s0 = WORK / f"{tag}_S0.db"
    for p in (db, s0, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if p.exists():
            p.unlink()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE INDEX idx_v ON t(v)")
    conn.executemany("INSERT INTO t (v) VALUES (?)",
                     [(f"baseline-{i}",) for i in range(200)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    shutil.copy2(db, s0)                      # S0: lineage gốc, WAL sạch
    return db, s0


def verdict(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        try:
            ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError as e:
            # Hỏng nặng tới mức integrity_check tự ném — vẫn là một verdict.
            return {"integrity_ok": False, "signature_match": True,
                    "findings": -1, "count": None, "scan": None,
                    "via_index": None, "disagreement": True,
                    "fatal": str(e)}
        def safe(q):
            try:
                return sum(1 for _ in conn.execute(q))
            except sqlite3.DatabaseError:
                return None
        cnt_row = conn.execute("SELECT COUNT(*) FROM t").fetchone()             if ic else None
        cnt = cnt_row[0] if cnt_row else None
        scan = safe("SELECT rowid FROM t")
        via_idx = safe("SELECT v FROM t INDEXED BY idx_v WHERE v >= ''")
        return {"integrity_ok": ic == ["ok"],
                "signature_match": any(any(s in line for s in SIGNATURE)
                                       for line in ic),
                "findings": len(ic) if ic != ["ok"] else 0,
                "count": cnt, "scan": scan, "via_index": via_idx,
                "disagreement": not (cnt == scan == via_idx)}
    finally:
        conn.close()


def one_trial(tag: str, arm: str) -> dict:
    db, s0 = build_s0(tag)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA wal_autocheckpoint=0")   # giữ frames trong -wal
    conn.executemany("INSERT INTO t (v) VALUES (?)",
                     [(f"post-s0-{i}",) for i in range(300)])
    conn.commit()                                  # WAL giờ có frames thật

    wal = Path(str(db) + "-wal")
    if arm == "CONTROL_D":
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # WAL rỗng
    wal_bytes_before_copy = wal.stat().st_size if wal.exists() else 0

    witnesses = {"handle_alive_at_copy": None,
                 "wal_bytes_before_copy": wal_bytes_before_copy}

    if arm == "CONTROL_C":
        conn.close()                               # đóng TRƯỚC copy
        witnesses["handle_alive_at_copy"] = False
        shutil.copy2(s0, db)
    else:
        witnesses["handle_alive_at_copy"] = True
        shutil.copy2(s0, db)                       # đè khi handle SỐNG
        # handle cũ tiếp tục: đúng hành vi rollback cũ của A5
        try:
            conn.executemany("INSERT INTO t (v) VALUES (?)",
                             [(f"after-overwrite-{i}",) for i in range(50)])
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            witnesses["old_handle_activity"] = "ok"
        except sqlite3.Error as e:
            witnesses["old_handle_activity"] = f"error: {e}"
        conn.close()

    out = verdict(db)
    out["witnesses"] = witnesses
    return out


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    report = {"lane": "RCA-1", "arms": {}}
    for arm in ("TREATMENT", "CONTROL_C", "CONTROL_D"):
        rows = [one_trial(f"{arm.lower()}_{i}", arm) for i in range(TRIALS)]
        corrupt = sum(1 for r in rows if not r["integrity_ok"])
        sig = sum(1 for r in rows if r["signature_match"])
        dis = sum(1 for r in rows if r["disagreement"])
        report["arms"][arm] = {"trials": TRIALS, "corrupt": corrupt,
                                "signature_match": sig, "disagreement": dis,
                                "sample": rows[0]}
        print(f"  {arm:<10} corrupt={corrupt}/{TRIALS}  "
              f"signature={sig}/{TRIALS}  index/table lệch={dis}/{TRIALS}  "
              f"wal_trước_copy={rows[0]['witnesses']['wal_bytes_before_copy']}b")

    t = report["arms"]["TREATMENT"]
    c = report["arms"]["CONTROL_C"]
    d = report["arms"]["CONTROL_D"]
    if t["corrupt"] == TRIALS and c["corrupt"] == 0:
        rc = "VERIFIED" if d["corrupt"] < TRIALS else \
             "VERIFIED (đè + handle sống đủ, WAL frames không bắt buộc)"
    elif t["corrupt"] == 0:
        rc = "FALSIFIED / NOT REPRODUCED"
    else:
        rc = "OPEN"
    report["root_cause"] = rc
    print(f"\n  INCIDENT ROOT CAUSE: {rc}")
    (WORK / "rca_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
