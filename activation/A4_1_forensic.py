"""A4.1 — DURABLE FORGETTING FORENSIC. Không chạm store thật.

Dấu chân không khớp: forget của A4 xoá memory nhưng `memory_tombstones`
không xuất hiện, trong khi cơ chế tuyên bố là tombstone-per-event.

Ba bước, toàn bộ trên clone:
  1. Tái lập ĐÚNG lời gọi A4 từ snapshot pre-A4, lần này GIỮ ForgetReport.
  2. Trên clone post-A4: ép đường replay/rebuild THẬT (không phải run_once
     trên job đã completed) → canary có sống lại không?
  3. Trên clone tươi còn memory: đường forget chính thức phải đặt được
     tombstone thật, rồi replay → canary phải vẫn vắng.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive import forgetting

WORK = Path(__file__).parent / "A4_1"
PRE = Path(__file__).parent / "A4" / "pre_a4_snapshot.db"
REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
MARKER = "CANARY-A4: kiểm hoạt hoá thật, một job duy nhất, 0913777555."
LEASE = 300.0
report: dict = {"phase": "A4.1", "sections": {}}


def clone(tag: str, src: Path) -> Path:
    dst = WORK / f"{tag}.db"
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    return dst


def state(db, needle="0913777555") -> dict:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        out = {
            "memory_rows": conn.execute(
                "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
                (f"%{needle}%",)).fetchone()[0],
            "event_rows": conn.execute(
                "SELECT COUNT(*) FROM cognitive_events WHERE payload_json LIKE ?",
                (f"%{needle}%",)).fetchone()[0],
            "tombstones_table": "memory_tombstones" in tables,
        }
        if out["tombstones_table"]:
            out["tombstone_rows"] = conn.execute(
                "SELECT COUNT(*) FROM memory_tombstones").fetchone()[0]
        if "projection_outbox" in tables:
            out["outbox"] = [tuple(r) for r in conn.execute(
                "SELECT status, COUNT(*) FROM projection_outbox GROUP BY status")]
        return out
    finally:
        conn.close()


def replay_and_drain(runtime) -> dict:
    """Đường phục hồi THẬT: quét mồ côi → re-enqueue → drain."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine
    engine = ProjectionReplayEngine(runtime.memories.conn)
    scan = engine.scan()
    result = engine.replay(dry_run=False)
    worker = worker_for(runtime, worker_id="a41-replay", lease_seconds=LEASE)
    worker.run_once(batch_size=50)
    return {"scan_candidates": len(scan.candidates),
            "replayed": getattr(result, "enqueued", str(result))[:200]
            if not isinstance(getattr(result, "enqueued", None), int)
            else result.enqueued}


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    # ---------- BƯỚC 1: tái lập đúng lời gọi A4, GIỮ ForgetReport
    c1 = clone("step1_replay_of_a4", PRE)
    runtime = MemoryOS(c1, projection_mode="outbox")
    runtime.observe(tenant_id="personal", actor="activation", source="canary",
                    content=MARKER, workspace_id="personal")
    worker = worker_for(runtime, worker_id="canary-a4", lease_seconds=LEASE)
    worker.run_once(batch_size=1)
    link_rows = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM memory_source_events m "
        "JOIN cognitive_memories c ON c.memory_id = m.memory_id "
        "WHERE c.content LIKE '%0913777555%'").fetchone()[0]

    fr = forgetting.forget_derived(runtime, subject="kiểm hoạt hoá",
                                   needle="0913777555")
    step1 = {
        "api": "forgetting.forget_derived",
        "arguments": {"subject": "kiểm hoạt hoá", "needle": "0913777555"},
        "source_event_link_rows_BEFORE_forget": link_rows,
        "memories_deleted": fr.memories_deleted,
        "tombstoned": fr.tombstoned,
        "checks_run": fr.checks_run,
        "note": fr.note,
        "residue": len(fr.residue),
        "as_dict": {k: v for k, v in fr.as_dict().items()
                    if k in ("verified_clean", "survives_rebuild",
                              "reversible", "reversible_via")}
        if hasattr(fr, "as_dict") else None,
    }
    runtime.close()
    step1["post_state"] = state(c1)
    report["sections"]["step1_exact_a4_call"] = step1
    print("BUOC 1 — ForgetReport cua DUNG loi goi A4:")
    for k, v in step1.items():
        print(f"    {k}: {v}")

    # ---------- BƯỚC 2: replay thật trên bản sao post-A4
    post_snapshot = WORK / "post_a4_snapshot.db"
    src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(post_snapshot))
    src.backup(dst)
    dst.close()
    src.close()
    c2 = clone("step2_replay_path", post_snapshot)
    pre2 = state(c2)
    print(f"\nBUOC 2 — clone post-A4 truoc replay: {pre2}")
    runtime = MemoryOS(c2, projection_mode="outbox")
    replay_info = replay_and_drain(runtime)
    runtime.close()
    post2 = state(c2)
    resurrected = post2["memory_rows"] > 0
    report["sections"]["step2_replay_on_post_a4"] = {
        "before": pre2, "replay": replay_info, "after": post2,
        "canary_resurrected": resurrected}
    print(f"    replay: {replay_info}")
    print(f"    sau replay: {post2}")
    print(f"    >>> CANARY {'SONG LAI' if resurrected else 'van vang'}")

    # ---------- BƯỚC 3: đường forget chính thức phải tombstone được
    c3 = clone("step3_official_forget", PRE)
    runtime = MemoryOS(c3, projection_mode="outbox")
    runtime.observe(tenant_id="personal", actor="activation", source="canary",
                    content=MARKER, workspace_id="personal")
    worker_for(runtime, lease_seconds=LEASE).run_once(batch_size=1)
    fr3 = forgetting.forget(runtime, needle="0913777555") \
        if hasattr(forgetting, "forget") else None
    if fr3 is None or getattr(fr3, "tombstoned", 0) == 0:
        # thử đường projection-scope tường minh
        fr3 = forgetting.forget_projection(runtime, needle="0913777555") \
            if hasattr(forgetting, "forget_projection") else fr3
    step3 = {"api_tried": "forget / forget_projection",
             "tombstoned": getattr(fr3, "tombstoned", None),
             "memories_deleted": getattr(fr3, "memories_deleted", None),
             "note": getattr(fr3, "note", None)}
    mid3 = state(c3)
    replay3 = replay_and_drain(runtime)
    runtime.close()
    post3 = state(c3)
    step3.update({"state_after_forget": mid3, "replay": replay3,
                  "state_after_replay": post3,
                  "canary_absent_after_replay": post3["memory_rows"] == 0})
    report["sections"]["step3_official_forget"] = step3
    print(f"\nBUOC 3 — duong forget chinh thuc: {step3}")

    (WORK / "a4_1_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print("\nreport:", WORK / "a4_1_report.json")


if __name__ == "__main__":
    main()
