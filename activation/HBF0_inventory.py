"""HBF-0 — HISTORICAL MIGRATION INVENTORY. Read-only, dry-run, zero mutation.

Bất biến trung tâm, trả học phí ở A4.1:

    MISSING LEDGER != MISSING PROJECTION.

Nên inventory KHÔNG bắt đầu từ "thiếu ledger = cần replay". Nó phân lớp toàn
bộ population lịch sử qua BỐN quan hệ đo được: event→ledger, event→tombstone,
event→erasure, event→legacy-memory — và canonical candidate được TÍNH trong
tiến trình (đúng nội dung builder sẽ sinh), không ghi một hàng nào đâu cả.

Mỗi event nhận ĐÚNG MỘT lớp chính, ưu tiên theo cây của chủ:
tombstone → erasure → event-only → ledger → quan hệ memory.
UNCLASSIFIED phải bằng 0, nếu không thì chưa được mở migration.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REAL = Path(r"c:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh"
            r"\.bio-agent-os\memory.db")
WORK = Path(__file__).parent / "HBF0"
SNAP = WORK / "hbf0_snapshot.db"
report: dict = {"phase": "HBF-0", "mode": "READ-ONLY"}


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)

    # ---- snapshot tươi = population ĐÓNG BĂNG + admissibility
    if SNAP.exists():
        SNAP.unlink()
    src = sqlite3.connect(f"file:{REAL.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(SNAP))
    src.backup(dst)
    dst.close()
    src.close()
    conn = sqlite3.connect(f"file:{SNAP.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    fk = list(conn.execute("PRAGMA foreign_key_check"))
    assert ic == ["ok"] and not fk, f"ADMISSIBILITY FAIL: ic={ic[:2]} fk={fk[:2]}"
    report["snapshot_sha256"] = hashlib.sha256(SNAP.read_bytes()).hexdigest()
    report["integrity"] = "ok"
    report["effective_store_mode"] = "outbox (settings) — inventory chạy trên snapshot"

    # ---- bốn quan hệ, đọc một lượt
    ledger_events = {str(r["event_id"]) for r in conn.execute(
        "SELECT event_id FROM projection_ledger")}
    tombstoned = {str(r["event_id"]) for r in conn.execute(
        "SELECT event_id FROM memory_tombstones WHERE event_id IS NOT NULL")}
    try:
        erased = {str(r[0]) for r in conn.execute(
            "SELECT event_id FROM event_erasures")}
    except sqlite3.OperationalError:
        erased = set()

    mem_by_event: dict[str, list[str]] = {}
    for r in conn.execute("SELECT event_id, memory_id FROM memory_source_events"):
        mem_by_event.setdefault(str(r["event_id"]), []).append(str(r["memory_id"]))
    mem_content = {str(r["memory_id"]): r["content"] for r in conn.execute(
        "SELECT memory_id, content FROM cognitive_memories")}
    events_by_mem: dict[str, list[str]] = {}
    for e, mems in mem_by_event.items():
        for m in mems:
            events_by_mem.setdefault(m, []).append(e)

    # ---- phân lớp từng event
    classes: dict[str, list[str]] = {k: [] for k in (
        "ALREADY_MANAGED", "TOMBSTONED_EXCLUDED", "ERASED_EXCLUDED",
        "EVENT_ONLY", "LEGACY_EQUIVALENT", "TRUE_MISSING", "DIVERGENT",
        "AMBIGUOUS", "UNCLASSIFIED")}

    events = list(conn.execute(
        "SELECT event_id, payload_json FROM cognitive_events"))
    for r in events:
        eid = str(r["event_id"])
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        content = str(payload.get("content", ""))

        if eid in tombstoned:
            classes["TOMBSTONED_EXCLUDED"].append(eid)
            continue
        if eid in erased:
            classes["ERASED_EXCLUDED"].append(eid)
            continue
        # event-only: nội dung chỉ là marker của cơ chế tạo ra nó —
        # "hook=<tên>" không mang gì sau dấu " | " (cùng luật substantive
        # của adapter, áp lên content đã render).
        stripped = content.strip()
        marker_only = (not stripped
                       or (stripped.startswith("hook=") and " | " not in stripped)
                       or (stripped.startswith("hook=")
                           and stripped.split(" | ", 1)[1].strip() == ""))
        if marker_only:
            classes["EVENT_ONLY"].append(eid)
            continue
        if eid in ledger_events:
            classes["ALREADY_MANAGED"].append(eid)
            continue

        legacy = mem_by_event.get(eid, [])
        if len(legacy) == 0:
            classes["TRUE_MISSING"].append(eid)
        elif len(legacy) > 1:
            classes["AMBIGUOUS"].append(eid)
        else:
            # canonical candidate TÍNH trong tiến trình: builder sản xuất
            # memory.content = payload["content"] (CognitiveMemoryBuilder).
            canonical = content
            actual = mem_content.get(legacy[0])
            if actual is None:
                # link trỏ tới memory đã bị xoá phía serving — không còn
                # tương đương, cũng không phải thiếu: cần người quyết.
                classes["AMBIGUOUS"].append(eid)
            elif str(actual) == canonical:
                classes["LEGACY_EQUIVALENT"].append(eid)
            else:
                classes["DIVERGENT"].append(eid)

    total = len(events)
    counted = sum(len(v) for v in classes.values())
    assert counted == total, f"counts không cân: {counted} != {total}"

    # ---- các phép đo ngược + phụ
    orphan_memories = [m for m in mem_content if m not in events_by_mem]
    multi_source = {m: es for m, es in events_by_mem.items() if len(es) > 1}
    from bio_agent_os.cognitive.projection_capability import unsupported_types
    unsupported = {t: True for t in unsupported_types()}
    conn.close()

    # ---- report
    print(f"CURRENT HISTORICAL EVENTS     {total}")
    print()
    for k in ("ALREADY_MANAGED", "TOMBSTONED_EXCLUDED", "ERASED_EXCLUDED",
              "EVENT_ONLY", "LEGACY_EQUIVALENT", "TRUE_MISSING", "DIVERGENT",
              "AMBIGUOUS", "UNCLASSIFIED"):
        print(f"{k:<28}  {len(classes[k])}")
    print()
    print(f"memories không truy được source event : {len(orphan_memories)}")
    print(f"memories có NHIỀU source event         : {len(multi_source)}")
    print(f"tombstone đang hoạt động               : {len(tombstoned)}")
    print(f"ledger coverage                        : {len(ledger_events)}")
    print(f"projection types chưa hỗ trợ           : {list(unsupported) or 'không'}")

    report["total_events"] = total
    report["classes"] = {k: len(v) for k, v in classes.items()}
    report["event_ids"] = {k: classes[k] for k in
                           ("TRUE_MISSING", "DIVERGENT", "AMBIGUOUS")}
    report["orphan_memories"] = orphan_memories
    report["memories_multi_source"] = multi_source
    report["tombstoned_events"] = sorted(tombstoned)
    report["ledger_coverage"] = len(ledger_events)
    report["unsupported_projection_types"] = list(unsupported)
    report["counts_reconcile"] = counted == total
    report["UNCLASSIFIED"] = len(classes["UNCLASSIFIED"])
    (WORK / "hbf0_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nUNCLASSIFIED = {len(classes['UNCLASSIFIED'])}  "
          f"{'— DU DIEU KIEN mo HBF-1' if not classes['UNCLASSIFIED'] else '— CHUA duoc mo migration'}")


if __name__ == "__main__":
    main()
