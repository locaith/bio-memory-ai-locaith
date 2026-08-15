"""Label memories written before labelling existed.

`ingest_learning_inbox` now records what each memory *is* (`memory_class`), so
that a question like "tôi từng làm sai gì" has something to select on. The 413
memories already in the learning store were written before that, and measured
on 2026-08-15 none of them carried a label — which is why the question returned
nothing at all rather than returning the wrong thing.

This derives the label from the text, using the same rules the ingest now
applies at write time. That is string matching, and string matching is what the
planner exists to avoid at *query* time; doing it once, offline, over data
written before the field existed is a migration rather than a retrieval
strategy. New memories get their label from the record that produced them and
never come through here.

Dry run by default. Prints the plan for a self-referential question before and
after, because the point is not that rows were updated.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.query_planner import CLASS_KEY  # noqa: E402

DEFAULT_DB = _REPO / "data" / "learning" / "memory.db"

#: Same order and same markers as `memory_items` in the ingest. Two copies of
#: one rule would drift, so this file states the dependency out loud: if the
#: ingest's markers change, this migration is already finished and stale, which
#: is fine — it only ever runs over pre-label data.
RULES = (
    ("Điểm yếu tự ghi nhận", "weakness"),
    ("Làm sai", "mistake"),
    ("Kết quả ", "score"),
    ("Tiến độ chương trình", "progress"),
    ("Đã hoàn thành khoá", "completion"),
    ("Chứng chỉ ", "completion"),
)

DEFAULT_CLASS = "lesson_fact"


def classify_text(text: str) -> str:
    """Match on the memory's own words, not on its course prefix.

    Every lesson memory carries `[Course name / Lesson]`, and several course
    names contain "Chứng chỉ chuyên môn IBM…". Matching the raw string put 168
    of 413 memories in `completion` — a wrong label on 40% of the store,
    produced by a rule that read as obviously correct. Caught by looking at the
    counts and one example rather than at the fact that it ran.
    """
    body = re.sub(r"^\[[^\]]*\]\s*", "", str(text or ""))
    for marker, label in RULES:
        if marker in body:
            return label
    return DEFAULT_CLASS


def main() -> int:
    ap = argparse.ArgumentParser(prog="backfill_memory_class")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"không thấy {db}")

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT memory_id, content, metadata_json FROM cognitive_memories"
    ).fetchall()

    planned: dict[str, list[tuple[str, str]]] = {}
    already = 0
    for memory_id, content, blob in rows:
        try:
            meta = json.loads(blob or "{}")
        except ValueError:
            meta = {}
        if meta.get(CLASS_KEY):
            already += 1
            continue
        label = classify_text(str(content))
        planned.setdefault(label, []).append((memory_id, str(content)))

    print(f"{len(rows)} ký ức | {already} đã có nhãn | "
          f"{sum(len(v) for v in planned.values())} sẽ gán\n")
    for label, items in sorted(planned.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):>4}  {label}")
        print(f"        vd: {items[0][1][:78]}")

    if not args.apply:
        print("\n  XEM TRƯỚC — thêm --apply để ghi")
        conn.close()
        return 0

    backup = db.with_name(db.stem + "_before_class_backfill.db")
    import shutil
    shutil.copy2(db, backup)
    print(f"\n  sao lưu: {backup.name}")

    written = 0
    for label, items in planned.items():
        for memory_id, _ in items:
            row = conn.execute(
                "SELECT metadata_json FROM cognitive_memories WHERE memory_id=?",
                (memory_id,)).fetchone()
            try:
                meta = json.loads(row[0] or "{}") if row else {}
            except ValueError:
                meta = {}
            meta[CLASS_KEY] = label
            conn.execute(
                "UPDATE cognitive_memories SET metadata_json=? WHERE memory_id=?",
                (json.dumps(meta, ensure_ascii=False, sort_keys=True), memory_id))
            written += 1
    conn.commit()

    check = conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE metadata_json LIKE ?",
        (f'%"{CLASS_KEY}"%',)).fetchone()[0]
    print(f"  gán {written} nhãn | {check}/{len(rows)} ký ức nay có nhãn")
    conn.close()
    if check != len(rows):
        raise SystemExit("còn ký ức chưa có nhãn — xem lại trước khi tin kết quả")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
