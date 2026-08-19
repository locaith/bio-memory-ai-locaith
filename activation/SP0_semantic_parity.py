"""SP-0 — PRODUCTION MEMORY SEMANTIC PARITY FORENSIC. Toàn bộ trên clone.

    CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT
    EXACTLY-ONCE EXECUTION != SEMANTIC PARITY

Cùng MỘT prompt qua hai đường ghi production (legacy remember vs outbox
worker), rồi diff từng cột của hàng memory, side-effects, và retrieval.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import AccessContext
from bio_agent_os.cognitive.reconciliation_worker import worker_for

WORK = Path(__file__).parent / "SP0"
PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "sp0",
          "prompt": "SP0 probe: khách hàng An Phú chốt hợp đồng 120 triệu "
                    "tháng 9, mã tham chiếu SP0-PARITY-01."}
QUERY = "khách hàng An Phú hợp đồng"
report: dict = {"phase": "SP-0", "sections": {}}


def build(tag: str, mode: str) -> Path:
    path = WORK / f"{tag}.db"
    for e in ("", "-wal", "-shm"):
        p = Path(str(path) + e)
        if p.exists():
            p.unlink()
    memory_os = MemoryOS(path, projection_mode=mode)
    adapter = ClaudeCodeHookAdapter(memory_os, "t1", "w1")
    adapter.ingest("UserPromptSubmit", PROMPT)
    if mode == "outbox":
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
    memory_os.close()
    return path


def memory_row(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM cognitive_memories "
                           "WHERE content LIKE '%SP0-PARITY-01%'").fetchone()
        assert row is not None, f"khong co memory trong {path.name}"
        d = dict(row)
        d["_world_model_rows"] = _count(conn, "world_entities") + _count(
            conn, "world_claims") + _count(conn, "world_model")
        return d
    finally:
        conn.close()


def _count(conn, table) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def retrieval(path: Path, mode: str) -> dict:
    memory_os = MemoryOS(path, projection_mode=mode)
    try:
        got = memory_os.recall(QUERY, context=AccessContext(tenant_id="t1"),
                               limit=5)
        out = []
        for g in got:
            m = getattr(g, "memory", g)
            out.append({
                "content": str(getattr(m, "content", ""))[:40],
                "score": getattr(g, "score", None),
                "components": getattr(g, "components", None) or
                              getattr(g, "score_breakdown", None),
            })
        return {"n": len(got), "top": out[:2]}
    finally:
        memory_os.close()


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    legacy_db = build("legacy", "legacy")
    outbox_db = build("outbox", "outbox")
    a, b = memory_row(legacy_db), memory_row(outbox_db)

    skip = {"memory_id", "created_at", "updated_at", "last_accessed_at"}
    diffs = {}
    for k in sorted(set(a) | set(b)):
        if k in skip:
            continue
        if a.get(k) != b.get(k):
            diffs[k] = {"legacy": str(a.get(k))[:90], "outbox": str(b.get(k))[:90]}
    print(f"=== FIELD DIFF (legacy vs outbox), {len(diffs)} cột lệch ===")
    for k, v in diffs.items():
        print(f"  {k}:")
        print(f"      legacy: {v['legacy']}")
        print(f"      outbox: {v['outbox']}")
    report["sections"]["field_diff"] = diffs

    print("\n=== RETRIEVAL DELTA (cùng query, hai store) ===")
    ra = retrieval(legacy_db, "legacy")
    rb = retrieval(outbox_db, "outbox")
    print(f"  legacy: {json.dumps(ra, ensure_ascii=False, default=str)[:300]}")
    print(f"  outbox: {json.dumps(rb, ensure_ascii=False, default=str)[:300]}")
    report["sections"]["retrieval"] = {"legacy": ra, "outbox": rb, "note":
        "cùng query cùng nội dung; lệch score/component = product-visible"}

    # ---- audit 14 ALREADY_MANAGED trên snapshot thật (read-only)
    REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
                r"\.bio-agent-os\memory.db")
    conn = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    managed = list(conn.execute(
        "SELECT l.event_id, l.target_id, e.payload_json, m.confidence, "
        "m.importance, m.salience, m.utility, m.metadata_json "
        "FROM projection_ledger l "
        "JOIN cognitive_events e ON e.event_id = l.event_id "
        "LEFT JOIN cognitive_memories m ON m.memory_id = l.target_id"))
    audit = []
    for r in managed:
        payload = json.loads(r["payload_json"] or "{}")
        content = str(payload.get("content", ""))
        kind = ("CANARY" if any(t in content for t in
                                ("CANARY", "A5v2", "A5.4", "REHEARSAL", "DCW",
                                 "R6 post", "L3"))
                else "REAL_USER_WRITE" if content.startswith("hook=")
                else "OTHER")
        deltas = []
        if r["confidence"] is not None:
            # legacy hook se ghi 0.72; cac score khac 0.55/0.35, 0.75/0.5, 0.65
            if abs((r["confidence"] or 0) - 0.72) > 1e-9:
                deltas.append(f"confidence={r['confidence']} (legacy=0.72)")
            meta = json.loads(r["metadata_json"] or "{}")
            if "state" not in meta:
                deltas.append("metadata.state THIẾU")
        audit.append({"event_id": str(r["event_id"])[:8], "kind": kind,
                      "deltas": deltas, "memory_alive": r["confidence"] is not None})
    conn.close()
    real_writes = [x for x in audit if x["kind"] == "REAL_USER_WRITE"
                   and x["memory_alive"]]
    print(f"\n=== AUDIT {len(audit)} ALREADY_MANAGED ===")
    for x in audit:
        print(f"  {x['event_id']} {x['kind']:<16} "
              f"{'alive' if x['memory_alive'] else 'forgotten/absent':<18} "
              f"{'; '.join(x['deltas']) or 'khớp legacy contract' if x['memory_alive'] else ''}")
    report["sections"]["managed_audit"] = audit
    report["sections"]["real_user_writes_affected"] = len(real_writes)

    verdict = "FAIL" if diffs else "PASS"
    report["conclusion"] = {
        "semantic_parity": verdict,
        "field_diff_count": len(diffs),
        "real_user_writes_with_degraded_semantics": len(real_writes),
    }
    (WORK / "sp0_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    print(f"\nSP-0 SEMANTIC PARITY: {verdict} — {len(diffs)} cột lệch, "
          f"{len(real_writes)} real user write bị ảnh hưởng")


if __name__ == "__main__":
    main()
