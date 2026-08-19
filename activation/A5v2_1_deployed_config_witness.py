"""A5-v2.1 — DEPLOYED CONFIG WITNESS.

A5-v2 chứng minh hook path đúng KHI process bị ép OUTBOX. Nó chưa chứng minh
persistent settings là bên thật sự chọn OUTBOX. Khe này cùng họ mọi
false-green trước: execution path không đo đúng điều report tuyên bố.

Luật của harness này: KHÔNG một chuỗi "outbox" nào được hard-code. Mode đi
từ file settings thật → env của subprocess → runtime, và mỗi chặng có một
nhân chứng riêng.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLATFORM = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh")
SETTINGS = PLATFORM / ".claude" / "settings.json"
REAL = PLATFORM / ".bio-agent-os" / "memory.db"
WORK = Path(__file__).parent / "A5v2_1"
report: dict = {"phase": "A5-v2.1", "steps": []}


def step(name, ok, detail=""):
    report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(f"A5-v2.1 dừng tại: {name}")


def rows(sql, args=()):
    conn = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    try:
        return list(conn.execute(sql, args))
    finally:
        conn.close()


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    # ---- 1. NGUỒN settings thật: file, lệnh hook, giá trị, phạm vi
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    declared = cfg.get("env", {}).get("BIO_AGENT_PROJECTION_MODE")
    hook_cmd = None
    for h in cfg.get("hooks", {}).get("UserPromptSubmit", []):
        for inner in h.get("hooks", []):
            if "hook_cli" in inner.get("command", ""):
                hook_cmd = inner["command"]
    report["settings_source"] = str(SETTINGS)
    report["hook_command"] = hook_cmd
    report["declared_mode_from_settings"] = declared
    step("nguồn settings + lệnh hook + declared mode",
         declared is not None and hook_cmd is not None,
         f"declared={declared!r} cmd={'tìm thấy' if hook_cmd else 'KHÔNG'}")

    # ---- 2. env cho fresh process: XOÁ thừa kế, áp env TỪ SETTINGS
    env = dict(os.environ)
    env.pop("BIO_AGENT_PROJECTION_MODE", None)     # bỏ mọi thừa kế của phiên
    env.update(cfg.get("env", {}))                  # CHỈ từ file settings
    injected = env.get("BIO_AGENT_PROJECTION_MODE")
    step("env của launcher đến TỪ settings, không hard-code",
         injected == declared, f"env={injected!r}")

    def run_hook(payload: dict) -> None:
        # Lệnh chạy đúng như settings ghi. `hook_cmd` là chuỗi có quote kiểu
        # Windows — tách executable và args tối thiểu.
        exe = hook_cmd.split('"')[1]
        args = hook_cmd.split('"')[2].strip().split()
        r = subprocess.run([exe, *args], input=json.dumps(payload).encode(),
                           capture_output=True, env=env, cwd=str(PLATFORM),
                           timeout=120)
        if r.returncode != 0:
            raise SystemExit(f"hook exit {r.returncode}: {r.stderr[:200]}")

    pre_jobs = {r[0] for r in rows("SELECT job_id FROM projection_outbox")}
    pre_ledger = {r[0] for r in rows("SELECT projection_key FROM projection_ledger")}

    # ---- 3. MỘT canary substantive, KHÔNG override mode
    marker = "A5v21 deployed-config witness [DCW-0001]."
    run_hook({"hook_event_name": "UserPromptSubmit", "prompt": marker,
              "session_id": "a5v21", "cwd": str(PLATFORM)})

    mems = rows("SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
                ("%[DCW-0001]%",))[0][0]
    events = rows("SELECT event_id FROM cognitive_events WHERE payload_json LIKE ?",
                  ("%[DCW-0001]%",))
    new_jobs = [r for r in rows(
        "SELECT job_id, event_id, status FROM projection_outbox")
        if r[0] not in pre_jobs]
    new_ledger = [r for r in rows(
        "SELECT projection_key, event_id FROM projection_ledger")
        if r[0] not in pre_ledger]
    marker_events = {str(e[0]) for e in events}
    alien_jobs = [j for j in new_jobs if str(j[1]) not in marker_events]
    alien_ledger = [l for l in new_ledger if str(l[1]) not in marker_events]
    unresolved = sum(1 for j in new_jobs if j[2] in ("pending", "in_progress"))

    # effective_mode đo TRONG runtime: outbox job + ledger row cho event này
    # là bằng chứng dương rằng fresh process THẬT SỰ chạy OUTBOX.
    effective_outbox = (len(new_jobs) == 1 and new_jobs[0][2] == "completed"
                        and len(new_ledger) == 1)
    step("effective mode trong fresh process == declared (nhân chứng dương: "
         "1 job completed + 1 ledger cho đúng event)",
         effective_outbox, f"jobs={new_jobs} ledger={len(new_ledger)}")
    step("substantive exactly-once, 0 alien, 0 unresolved",
         mems == 1 and len(events) == 1 and not alien_jobs
         and not alien_ledger and unresolved == 0,
         f"mems={mems} events={len(events)} alien={len(alien_jobs)}/"
         f"{len(alien_ledger)} unresolved={unresolved}")

    # ---- 4. MỘT hook non-substantive từ settings-derived launcher
    pre_jobs2 = {r[0] for r in rows("SELECT job_id FROM projection_outbox")}
    pre_events = rows("SELECT COUNT(*) FROM cognitive_events")[0][0]
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "a5v21-empty",
              "cwd": str(PLATFORM)})
    post_events = rows("SELECT COUNT(*) FROM cognitive_events")[0][0]
    new_jobs2 = [r for r in rows("SELECT job_id FROM projection_outbox")
                 if r[0] not in pre_jobs2]
    empty_mems = rows("SELECT COUNT(*) FROM cognitive_memories "
                      "WHERE content = 'hook=UserPromptSubmit'")[0][0]
    step("non-substantive: 1 event, 0 projection debt, 0 memory",
         post_events == pre_events + 1 and not new_jobs2 and empty_mems == 0,
         f"events+{post_events - pre_events} jobs+{len(new_jobs2)}")

    # ---- 5. dọn canary qua ĐƯỜNG FORGET CHÍNH THỨC, giữ bia mộ
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive import forgetting
    runtime = MemoryOS(REAL, projection_mode=declared)
    try:
        target = runtime.memories.conn.execute(
            "SELECT memory_id FROM cognitive_memories WHERE content LIKE ?",
            ("%[DCW-0001]%",)).fetchone()[0]
        fr = forgetting.forget_derived(runtime, memory_id=str(target),
                                       needle="DCW-0001")
        step("canary cleanup qua forget chính thức — ForgetReport giữ lại",
             fr.memories_deleted == 1 and fr.tombstoned >= 1,
             f"deleted={fr.memories_deleted} tombstoned={fr.tombstoned}")
    finally:
        runtime.close()

    # ---- 6. correction cho artifact A5-v2: phân biệt nguồn mode
    for tag in ("clone", "real"):
        p = Path(__file__).parent / "A5v2" / f"a5v2_{tag}_report.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            data["mode_provenance_correction_19_08"] = {
                "ladder_mode_source": "explicit experimental env (harness hook())",
                "deployed_mode_source": "persistent settings — CHƯA được đo ở A5-v2",
                "deployed_mode_effective": "đo tại A5-v2.1 bằng fresh process, "
                                            "env dựng từ settings, không override",
            }
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    report["declared_mode"] = declared
    report["effective_mode_witness"] = "outbox job completed + ledger row"
    report["conclusion"] = "DEPLOYED CONFIG WITNESS: PASS"
    (WORK / "a5v2_1_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nA5-v2.1: PASS")


if __name__ == "__main__":
    main()
