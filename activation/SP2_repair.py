"""SP-2 — repair 8 real user write bị làm nghèo semantics. KHÔNG rewrite lịch sử.

    event cũ / memory_id / ledger target_id / content   GIỮ NGUYÊN
    semantic fields sai → sửa theo contract đã chứng minh → audit repair

Bio không nói "memory này vừa được sinh lại". Nó nói: "memory này sinh ngày X;
ngày Y hệ thống phát hiện materialization path làm nghèo semantics và sửa
chính record đó theo contract đã được chứng minh (SP-0/SP-1)."
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bio_agent_os.cognitive.store_generation import certify_candidate

REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
WORK = Path(__file__).parent / "SP2"
SNAP = WORK / "pre_sp2_snapshot.db"
CANARY_MARKS = ("CANARY", "A5v2", "A5.4", "REHEARSAL", "DCW", "R6 post", "L3")
# Hợp đồng legacy của hook UserPromptSubmit — đúng call-site đã chứng minh:
CONTRACT = {"confidence": 0.72, "importance": 0.55, "salience": 0.50,
            "utility": 0.65}
report: dict = {"phase": "SP-2", "repairs": []}


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    # snapshot tươi + certify TRƯỚC mutation — điểm rollback
    if SNAP.exists():
        SNAP.unlink()
    src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(SNAP))
    src.backup(dst)
    dst.close()
    src.close()
    report["rollback_point_sha256"] = certify_candidate(SNAP)
    print(f"  rollback point certified: {report['rollback_point_sha256'][:16]}")

    conn = sqlite3.connect(str(REAL))
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        "SELECT l.event_id, l.target_id, e.payload_json, m.memory_id, "
        "m.confidence, m.importance, m.salience, m.utility, m.metadata_json "
        "FROM projection_ledger l "
        "JOIN cognitive_events e ON e.event_id = l.event_id "
        "JOIN cognitive_memories m ON m.memory_id = l.target_id"))
    repaired = 0
    for r in rows:
        payload = json.loads(r["payload_json"] or "{}")
        content = str(payload.get("content", ""))
        if any(t in content for t in CANARY_MARKS):
            continue                                   # canary: để nguyên
        if not content.startswith("hook="):
            continue
        before = {k: r[k] for k in ("confidence", "importance",
                                    "salience", "utility")}
        if all(abs(before[k] - v) < 1e-9 for k, v in CONTRACT.items()):
            continue                                   # đã đúng contract
        meta = json.loads(r["metadata_json"] or "{}")
        if "state" not in meta:
            meta["state"] = {"mode": "implement", "stress_state": "normal"}
        meta.setdefault("repairs", []).append({
            "repaired_at": "2026-08-19",
            "reason": "SP-0: outbox builder làm nghèo semantics "
                      "(model defaults thay vì call-site contract)",
            "evidence": "activation/SP0/sp0_report.json + SP-1 parity gate",
            "fields_before": before,
            "fields_after": CONTRACT,
            "contract": "hook UserPromptSubmit legacy call-site, "
                        "projection_intent contract_version=1",
        })
        conn.execute(
            "UPDATE cognitive_memories SET confidence=?, importance=?, "
            "salience=?, utility=?, metadata_json=? WHERE memory_id=?",
            (CONTRACT["confidence"], CONTRACT["importance"],
             CONTRACT["salience"], CONTRACT["utility"],
             json.dumps(meta, ensure_ascii=False), r["memory_id"]))
        repaired += 1
        report["repairs"].append({"memory_id": str(r["memory_id"])[:8],
                                  "event_id": str(r["event_id"])[:8],
                                  "before": before})
        print(f"  repaired {str(r['memory_id'])[:8]}  "
              f"conf {before['confidence']}→{CONTRACT['confidence']}  "
              f"util {before['utility']}→{CONTRACT['utility']}  +state +audit")
    conn.commit()

    ic = [x[0] for x in conn.execute("PRAGMA integrity_check")]
    fk = list(conn.execute("PRAGMA foreign_key_check"))
    conn.close()
    report["repaired_count"] = repaired
    report["integrity_after"] = ic[:1]
    assert ic == ["ok"] and not fk
    (WORK / "sp2_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSP-2: {repaired} memory repaired in place — lịch sử giữ nguyên, "
          f"audit nằm trong chính record. integrity ok.")


if __name__ == "__main__":
    main()
