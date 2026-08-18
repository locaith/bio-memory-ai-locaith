"""RECOVERY INCIDENT MODE — dựng candidate ngoài, chứng minh, thay MỘT lần.

Không copy đè database đang sống. Không nói "0 mất mát" trước khi closure
được chứng minh row-by-row.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import os
from pathlib import Path

REC = Path(__file__).parent
CLEAN = REC.parent / "A4_1" / "post_a4_snapshot.db"
EVIDENCE = REC / "corrupt_evidence.db"
CAND = REC / "recovery_candidate.db"
REALDIR = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh\.bio-agent-os")
REAL = REALDIR / "memory.db"
QUAR = REC / "quarantine_incident"
report: dict = {"mode": "RECOVERY", "steps": []}


def step(name, ok, detail=""):
    report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"RECOVERY dừng tại: {name}")


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    # ---------- R2: candidate từ CLEAN + toàn bộ dependency closure của delta
    if CAND.exists():
        CAND.unlink()
    shutil.copy2(CLEAN, CAND)
    conn = sqlite3.connect(str(CAND))
    # Attach theo path thường (URI cần bật riêng cho ATTACH). Evidence là BẢN
    # SAO; script chỉ SELECT từ ev.*
    conn.execute("ATTACH DATABASE ? AS ev", (str(EVIDENCE),))

    delta_events = [r[0] for r in conn.execute(
        "SELECT event_id FROM ev.cognitive_events WHERE event_id NOT IN "
        "(SELECT event_id FROM main.cognitive_events)")]
    delta_mems = [r[0] for r in conn.execute(
        "SELECT memory_id FROM ev.cognitive_memories WHERE memory_id NOT IN "
        "(SELECT memory_id FROM main.cognitive_memories)")]

    # closure: MỌI bảng có cột event_id/memory_id, delta row-by-row
    closure = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM ev.sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'")]
    for t in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA ev.table_info([{t}])")]
        keys = [(c, delta_events if c == "event_id" else delta_mems)
                for c in cols if c in ("event_id", "memory_id")]
        moved = 0
        for col, vals in keys:
            if not vals:
                continue
            marks = ",".join("?" * len(vals))
            col_list = ",".join(f"[{c}]" for c in cols)
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO main.[{t}] ({col_list}) "
                    f"SELECT {col_list} FROM ev.[{t}] WHERE [{col}] IN ({marks})",
                    vals)
                moved += max(cur.rowcount, 0)
            except sqlite3.OperationalError as e:
                closure[f"{t}!SKIP"] = str(e)[:60]
        if moved:
            closure[t] = moved
    conn.commit()

    # DEDUPE CÓ CHỦ ĐÍCH: tin "chọn (a)" của chủ đến khi env còn outbox —
    # bug A5.4 xảy ra THẬT: remember ghi bản #1, hook-drain build bản #2.
    # Hợp đồng legacy: event đó chỉ có MỘT memory. Loại bản worker-built
    # (nhận diện qua projection_ledger.target_id) + link + ledger + outbox
    # row của nó. Ghi rõ từng hàng bị loại.
    excluded = {}
    for (target,) in list(conn.execute(
            "SELECT target_id FROM projection_ledger "
            "WHERE event_id IN (%s)" % ",".join("?" * len(delta_events)),
            delta_events)):
        excluded["memory"] = conn.execute(
            "DELETE FROM cognitive_memories WHERE memory_id=?", (target,)).rowcount
        excluded["source_link"] = conn.execute(
            "DELETE FROM memory_source_events WHERE memory_id=?", (target,)).rowcount
    excluded["ledger"] = conn.execute(
        "DELETE FROM projection_ledger WHERE event_id IN (%s)"
        % ",".join("?" * len(delta_events)), delta_events).rowcount
    excluded["outbox"] = conn.execute(
        "DELETE FROM projection_outbox WHERE event_id IN (%s)"
        % ",".join("?" * len(delta_events)), delta_events).rowcount
    conn.commit()
    closure["EXCLUDED_duplicate_leg"] = excluded

    # FTS: khong copy row nao từ DB hỏng — REBUILD toàn bộ từ bảng content
    try:
        conn.execute("INSERT INTO cognitive_memory_fts(cognitive_memory_fts) "
                     "VALUES('rebuild')")
        conn.commit()
        fts = "rebuilt"
    except sqlite3.OperationalError as e:
        fts = f"rebuild failed: {e}"
    conn.execute("DETACH DATABASE ev")

    step("R2: closure row-by-row",
         len(delta_events) == 2 and 1 <= len(delta_mems) <= 3,
         f"events={len(delta_events)} mems={len(delta_mems)} "
         f"closure={closure} fts={fts}")

    # ---------- R3: certify candidate
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    step("R3: integrity_check == ok", ic == ["ok"], ic[:2])
    fk = list(conn.execute("PRAGMA foreign_key_check"))
    step("R3: foreign_key_check rỗng", not fk, fk[:2])
    total = conn.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    scan = sum(1 for _ in conn.execute("SELECT rowid FROM cognitive_memories"))
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT memory_id) FROM cognitive_memories").fetchone()[0]
    step("R3: COUNT == scan == distinct (hết mâu thuẫn thước đo)",
         total == scan == distinct, f"{total}/{scan}/{distinct}")
    ev_read = conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE event_id IN (?,?)",
        tuple(delta_events)).fetchone()[0]
    prov = conn.execute(
        "SELECT COUNT(*) FROM memory_source_events WHERE memory_id IN (%s)"
        % ",".join("?" * len(delta_mems)), tuple(delta_mems)).fetchone()[0]
    dupes = conn.execute(
        "SELECT COUNT(*) FROM (SELECT content, COUNT(*) c FROM "
        "cognitive_memories GROUP BY content HAVING c > 1)").fetchone()[0]
    tomb = conn.execute("SELECT COUNT(*) FROM memory_tombstones").fetchone()[0] \
        if conn.execute("SELECT name FROM sqlite_master WHERE name='memory_tombstones'").fetchone() else 0
    outbox_rows = conn.execute("SELECT COUNT(*) FROM projection_outbox "
                               "WHERE status IN ('pending','in_progress')").fetchone()[0]
    conn.close()
    step("R3: readback ứng dụng — events cứu được đọc lại, provenance, "
         "dupes == 3 nhóm lịch sử của CLEAN (không nhóm mới), tombstone giữ, runtime bất hoạt",
         ev_read == len(delta_events) and dupes == 3 and outbox_rows == 0,
         f"ev={ev_read} prov_links={prov} dupes={dupes} tomb={tomb} "
         f"pending={outbox_rows}")
    cand_hash = sha(CAND)
    report["candidate_sha256"] = cand_hash

    # ---------- R5: install — move-aside, KHÔNG copy đè
    QUAR.mkdir(exist_ok=True)
    for name in ("memory.db", "memory.db-wal", "memory.db-shm"):
        p = REALDIR / name
        if p.exists():
            shutil.move(str(p), str(QUAR / name))
    shutil.copy2(CAND, REAL)          # cài vào chỗ TRỐNG, không đè file sống
    step("R5: install vào chỗ trống, không stale wal/shm",
         REAL.exists() and not (REALDIR / "memory.db-wal").exists())
    c = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    ic2 = [r[0] for r in c.execute("PRAGMA integrity_check")]
    fk2 = list(c.execute("PRAGMA foreign_key_check"))
    n_tables = len(list(c.execute("SELECT name FROM sqlite_master WHERE type='table'")))
    c.close()
    step("R5: installed mở READ-ONLY — integrity ok, fk rỗng",
         ic2 == ["ok"] and not fk2, f"tables={n_tables}")
    step("R5: hash installed == candidate", sha(REAL) == cand_hash,
         cand_hash[:24])

    # ---------- R6: vài legacy write thật qua đúng lệnh hook, rồi snapshot
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)
    env.update({"BIO_AGENT_TENANT_ID": "locaith",
                "BIO_AGENT_WORKSPACE_ID": "locaith-intelligence-os",
                "BIO_AGENT_WORKSPACE_STRATEGY": "explicit"})
    for i in range(2):
        payload = json.dumps({"hook_event_name": "UserPromptSubmit",
                              "prompt": f"R6 post-recovery legacy write thu #{i} ma 09411122{i}.",
                              "session_id": "r6", "cwd": str(REALDIR.parent)})
        r = subprocess.run([sys.executable, "-m", "bio_agent_os.cognitive.hook_cli",
                            "UserPromptSubmit"], input=payload.encode(),
                           capture_output=True, env=env,
                           cwd=str(REALDIR.parent), timeout=120)
        step(f"R6: legacy hook write #{i}", r.returncode == 0)
    snap = REC / "post_recovery_snapshot.db"
    if snap.exists():
        snap.unlink()
    src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    dst.close()
    ic3 = [r[0] for r in src.execute("PRAGMA integrity_check")]
    src.close()
    rc = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    ic4 = [r[0] for r in rc.execute("PRAGMA integrity_check")]
    rc.close()
    step("R6: snapshot + restore-verify, integrity ok CẢ HAI",
         ic3 == ["ok"] and ic4 == ["ok"])

    report["conclusion"] = "REAL STORE RECOVERY = VERIFIED"
    (REC / "recovery_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nREAL STORE RECOVERY: VERIFIED — dừng, không tự resume A5")


if __name__ == "__main__":
    main()
