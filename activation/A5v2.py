"""A5-v2 — PRODUCTION-HOOK NEW-WRITE ACTIVATION.

Khác A5-v1 ở bốn hàng rào, mỗi cái trả một món nợ của v1:

  1. MỌI write đi qua ĐÚNG lệnh production hook (v1 đo observe() bên cạnh).
  2. Harness tự certify trên CLONE trước khi được chạm store thật (sự cố
     18/08 đến từ harness, không phải product).
  3. Interlock nhìn MỌI outbox/ledger row mới — kể cả completed. Một
     historical job enqueue rồi drain cực nhanh không lẩn qua khe đo.
  4. Rollback là install_generation (generation replacement), không bao giờ
     copy đè; digest tách cột VOLATILE (access-stats do recall hợp lệ) khỏi
     cột ngữ nghĩa — đúng population.

Chạy: python A5v2.py --target clone   (bắt buộc PASS trước)
      python A5v2.py --target real
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bio_agent_os.cognitive.store_generation import (certify_candidate,
                                                     install_generation)

REALDIR = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
               r"\.bio-agent-os")
REAL = REALDIR / "memory.db"
WORK = Path(__file__).parent / "A5v2"
PY = sys.executable
# Đo trên clone rehearsal: bước recall của hook đổi ĐÚNG hai cột này trên
# các hàng được recall — access-stats, không phải ngữ nghĩa.
VOLATILE = {"last_accessed", "last_accessed_at", "access_count",
            "recall_count", "retrieval_count", "updated_at", "accessed_at"}
report: dict = {"phase": "A5-v2", "steps": []}


def step(name, ok, detail=""):
    report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"A5-v2 dừng tại: {name}")


def hook(store: Path, prompt: str | None, session: str) -> None:
    """ĐÚNG lệnh production, env tường minh cho subprocess."""
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)
    env.update({"BIO_MEMORY_DB": str(store),
                "BIO_AGENT_PROJECTION_MODE": "outbox",
                "BIO_AGENT_TENANT_ID": "locaith",
                "BIO_AGENT_WORKSPACE_ID": "locaith-intelligence-os",
                "BIO_AGENT_WORKSPACE_STRATEGY": "explicit"})
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": session,
               "cwd": str(store.parent.parent)}
    if prompt is not None:
        payload["prompt"] = prompt
    r = subprocess.run([PY, "-m", "bio_agent_os.cognitive.hook_cli",
                        "UserPromptSubmit"],
                       input=json.dumps(payload).encode(), capture_output=True,
                       env=env, cwd=str(store.parent.parent), timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"hook exit {r.returncode}: {r.stderr[:200]}")


def semantic_digest(db: Path) -> dict:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    out = {}
    try:
        for table, key in (("cognitive_events", "event_id"),
                           ("cognitive_memories", "memory_id")):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            keep = [c for c in cols if c not in VOLATILE]
            out[table] = {}
            for r in conn.execute(f"SELECT {', '.join(keep)} FROM {table}"):
                d = dict(zip(keep, r))
                out[table][str(d[key])] = hashlib.sha256(
                    repr(sorted(d.items())).encode()).hexdigest()
        return out
    finally:
        conn.close()


def rows(db: Path, sql: str, args=()) -> list:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return list(conn.execute(sql, args))
    finally:
        conn.close()


def stage(store: Path, name: str, n: int, baseline: dict) -> dict:
    pre_jobs = {r[0] for r in rows(store, "SELECT job_id FROM projection_outbox")}
    pre_ledger = {r[0] for r in rows(
        store, "SELECT projection_key FROM projection_ledger")} \
        if rows(store, "SELECT name FROM sqlite_master WHERE name='projection_ledger'") else set()

    markers = []
    for i in range(n):
        # Mã mang TÊN STAGE ĐẦY ĐỦ: bản đầu dùng name[-1], mà S20[-1] == S50[-1]
        # nên 20 mã đầu của S50 trùng S20 và máy đếm LIKE đếm chéo — dupes giả.
        m = f"A5v2 ghi qua production hook [{name}-{i:04d}]."
        markers.append(m)
        hook(store, m, f"a5v2-{name}")

    confirmed = sum(
        rows(store, "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
             ("%[" + m.split("[")[1] + "%",))[0][0] for m in markers)
    dupes = sum(1 for m in markers if rows(
        store, "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        ("%[" + m.split("[")[1] + "%",))[0][0] > 1)

    # INTERLOCK v2: MỌI row outbox/ledger MỚI phải thuộc event của stage này
    marker_events = {r[0] for m in markers for r in rows(
        store, "SELECT event_id FROM cognitive_events WHERE payload_json LIKE ?",
        ("%[" + m.split("[")[1] + "%",))}
    new_jobs = [r for r in rows(
        store, "SELECT job_id, event_id, status FROM projection_outbox")
        if r[0] not in pre_jobs]
    alien = [r for r in new_jobs if str(r[1]) not in marker_events]
    unresolved = sum(1 for r in new_jobs if r[2] in ("pending", "in_progress"))
    new_ledger = [r[0] for r in rows(
        store, "SELECT projection_key, event_id FROM projection_ledger")
        if r[0] not in pre_ledger and str(r[1]) not in marker_events]

    after = semantic_digest(store)
    sem_changed = [k for t in ("cognitive_events", "cognitive_memories")
                   for k, v in baseline[t].items() if after[t].get(k) != v]

    result = {"requested": n, "confirmed": confirmed, "lost": n - confirmed,
              "duplicates": dupes, "unresolved": unresolved,
              "alien_jobs(historical?)": len(alien),
              "alien_ledger": len(new_ledger),
              "semantic_old_rows_changed": len(sem_changed)}
    print(f"  [{name}] {result}")
    ok = (confirmed == n and dupes == 0 and unresolved == 0
          and not alien and not new_ledger and not sem_changed)
    step(f"{name} VALID PASS", ok, "" if ok else str(result))
    return result


def main() -> None:
    target = sys.argv[sys.argv.index("--target") + 1]
    WORK.mkdir(parents=True, exist_ok=True)

    if target == "clone":
        store = WORK / "harness_rehearsal_clone.db"
        for e in ("", "-wal", "-shm"):
            p = Path(str(store) + e)
            if p.exists():
                p.unlink()
        src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(str(store))
        src.backup(dst)
        dst.close()
        src.close()
    else:
        store = REAL

    # ---- PRE snapshot certified (đồng thời là rollback candidate)
    snap = WORK / f"pre_a5v2_{target}.db"
    if snap.exists():
        snap.unlink()
    src = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    dst.close()
    src.close()
    baseline_hash = certify_candidate(snap)      # integrity + hash, fail-closed
    step("PRE snapshot CERTIFIED (integrity + hash)", True, baseline_hash[:16])
    report[f"baseline_{target}"] = baseline_hash

    baseline = semantic_digest(store)

    # ---- ladder qua production hook
    stage(store, "S5", 5, baseline)
    stage(store, "S20", 20, baseline)
    stage(store, "S50", 50, baseline)

    # ---- clean generation reset: 75 canary không sống mãi trong trí nhớ chủ
    manifest = install_generation(store, snap, WORK / f"quar_{target}")
    ok = manifest["installed_sha256"] == baseline_hash
    fk = rows(store, "PRAGMA foreign_key_check")
    ic = rows(store, "PRAGMA integrity_check")
    step("CLEAN GENERATION RESET — INSTALL_CONFIRMED, hash khớp baseline, "
         "integrity ok, FK rỗng",
         ok and ic == [("ok",)] and not fk, manifest["installed_sha256"][:16])

    # ---- deployed-mode acceptance: 3 substantive + 1 non-substantive.
    # Đếm DELTA, không đếm tổng: store thật còn giữ canary sống của lần
    # acceptance trước, và clone thừa kế chúng — đếm tổng cho live=6 giả.
    pre_live = sum(rows(store,
                   "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
                   (f"%070099{i}88%",))[0][0] for i in range(3))
    for i in range(3):
        hook(store, f"A5v2 live canary cuối #{i} mã 070099{i}88.", "a5v2-live")
    hook(store, None, "a5v2-live")               # non-substantive: event-only
    live = sum(rows(store,
                    "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
                    (f"%070099{i}88%",))[0][0] for i in range(3)) - pre_live
    marker_mems = rows(store, "SELECT COUNT(*) FROM cognitive_memories "
                              "WHERE content LIKE '%hook=UserPromptSubmit%' "
                              "AND content NOT LIKE '%prompt=%'")[0][0]
    empty_mem = rows(store, "SELECT COUNT(*) FROM cognitive_memories "
                            "WHERE content = 'hook=UserPromptSubmit'")[0][0]
    unresolved = rows(store, "SELECT COUNT(*) FROM projection_outbox "
                             "WHERE status IN ('pending','in_progress')")[0][0]
    step("3 substantive live canaries — exactly once", live == 3, f"live={live}")
    step("non-substantive canary — event-only", empty_mem == 0 and marker_mems == 0)
    step("0 unresolved sau live window", unresolved == 0)

    report["conclusion"] = f"A5-v2 {target.upper()}: PASS"
    (WORK / f"a5v2_{target}_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nA5-v2 [{target}]: PASS")


if __name__ == "__main__":
    main()
