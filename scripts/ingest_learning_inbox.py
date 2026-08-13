"""Feeds the learning inbox into memory, and keeps the exam papers out of it.

Each captured lesson has two halves that must not mix:

    key_facts, summary   ->  memory. What was learned.
    qa                   ->  a held-out question set. NEVER stored.

If the answers went into memory, asking them back later would measure reading,
not remembering, and every score would be perfect and worthless. That is the
same fault that made the 2026-08-12 Gemini benchmark unusable — a comparison
that flatters by construction — and it is easier to make here, because storing
everything is the obvious thing to do.

    python scripts/ingest_learning_inbox.py --dry-run
    python scripts/ingest_learning_inbox.py

Processed files move to `_processed/`, invalid ones to `_rejected/` with a note
saying what was wrong. Nothing is deleted and nothing is guessed at: a missing
field is reported, never filled in, because an invented value becomes a memory
nobody can tell from a real one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

INBOX = Path(r"C:\locaith\learning-inbox")
EXAM_BANK = _REPO / "data" / "learning" / "exam_bank.jsonl"
SCHEMA = "locaith.learning.v1"
REQUIRED = ("schema", "course", "lesson_id", "summary")


def problems(record: dict) -> list[str]:
    issues = []
    if record.get("schema") != SCHEMA:
        issues.append(f"schema phải là {SCHEMA}")
    for field in REQUIRED:
        if not str(record.get(field, "")).strip():
            issues.append(f"thiếu hoặc rỗng: {field}")
    for field in ("key_facts", "qa", "attempts"):
        if field in record and not isinstance(record[field], list):
            issues.append(f"{field} phải là danh sách")
    return issues


def memory_texts(record: dict) -> list[str]:
    """What actually goes into memory.

    Each fact is stored on its own rather than as one blob: retrieval scores
    whole memories, so a fact buried in a paragraph of six competes on the
    paragraph's average relevance instead of its own.

    Course and lesson are prefixed onto every one. A fact that reads "the three
    deployment models are public, private and hybrid" is unanchored six months
    later — anchored, it can answer "what did I learn in the cloud course".
    """
    prefix = f"[{record.get('course')} / {record.get('lesson_title') or record.get('lesson_id')}]"
    out: list[str] = []
    for fact in record.get("key_facts") or []:
        text = str(fact).strip()
        if text:
            out.append(f"{prefix} {text}")
    summary = str(record.get("summary") or "").strip()
    if summary:
        out.append(f"{prefix} {summary}")

    # A wrong answer is the most valuable thing in the file: it marks where
    # understanding actually failed, which is exactly what needs recalling
    # later. Stored as a correction, with the mistake kept visible.
    for attempt in record.get("attempts") or []:
        if isinstance(attempt, dict) and attempt.get("was_correct") is False:
            question = str(attempt.get("question", "")).strip()
            correct = str(attempt.get("correct_answer", "")).strip()
            why = str(attempt.get("why_wrong", "")).strip()
            if question and correct:
                out.append(
                    f"{prefix} Từng trả lời sai: \"{question}\" — đáp án đúng là "
                    f"{correct}." + (f" Nhầm ở chỗ: {why}" if why else "")
                )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="ingest_learning_inbox")
    ap.add_argument("--inbox", default=str(INBOX))
    ap.add_argument("--tenant", default="tuananh")
    ap.add_argument("--workspace", default="learning")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--embed", action="store_true",
                    help="backfill vectors after ingesting (needs an embedder)")
    args = ap.parse_args()

    inbox = Path(args.inbox)
    files = sorted(p for p in inbox.glob("*.json") if p.is_file())
    if not files:
        print(f"  không có file nào trong {inbox}")
        return 0

    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.models import MemoryType

    embedder = None
    if args.embed:
        from bio_agent_os.core.embedder import Embedder
        embedder = Embedder()

    db = _REPO / "data" / "learning" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    memory_os = None if args.dry_run else MemoryOS(db, embedder=embedder)

    stored = exams = rejected = 0
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            record, issues = {}, [f"JSON hỏng: {exc}"]
        else:
            issues = problems(record)

        if issues:
            rejected += 1
            print(f"  TỪ CHỐI {path.name}: {issues[0]}")
            if not args.dry_run:
                target = inbox / "_rejected"
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target / path.name))
                (target / path.name).with_suffix(".why.txt").write_text(
                    "\n".join(f"  - {i}" for i in issues), encoding="utf-8"
                )
            continue

        texts = memory_texts(record)
        qa = [q for q in (record.get("qa") or [])
              if isinstance(q, dict) and q.get("question") and q.get("answer")]

        # A wrong-answer memory stores the question *and* its answer together,
        # which is the one genuine leak in this design: if the same question is
        # also in the exam bank, recall finds the card instead of the knowledge
        # and the score means nothing. Facts overlapping answers is fine and
        # necessary — a memory that does not contain the fact has nothing to
        # recall. A verbatim pair is not.
        attempted = {
            str(a.get("question", "")).strip().lower()
            for a in (record.get("attempts") or [])
            if isinstance(a, dict) and a.get("was_correct") is False
        }
        overlap = [q for q in qa if str(q["question"]).strip().lower() in attempted]
        if overlap:
            qa = [q for q in qa if str(q["question"]).strip().lower() not in attempted]
            print(f"    bỏ {len(overlap)} câu khỏi bộ đề: đã nằm nguyên văn trong "
                  f"ký ức 'từng trả lời sai'")

        print(f"  {path.name}: {len(texts)} ký ức, {len(qa)} câu hỏi giữ riêng")
        if args.dry_run:
            stored += len(texts)
            exams += len(qa)
            continue

        for text in texts:
            event = memory_os.observe(
                tenant_id=args.tenant, actor="tuananh", source="learning",
                content=text, workspace_id=args.workspace,
                metadata={"course": record.get("course"),
                          "lesson_id": record.get("lesson_id"),
                          "captured_at": record.get("captured_at")},
            )
            memory_os.remember(
                event=event, memory_type=MemoryType.SEMANTIC, content=text,
                confidence=float(record.get("confidence") or 0.7),
            )
        stored += len(texts)

        # The exam bank lives outside the memory database on purpose. Two
        # separate stores is the cheapest way to make leakage impossible rather
        # than merely unlikely.
        if qa:
            EXAM_BANK.parent.mkdir(parents=True, exist_ok=True)
            with EXAM_BANK.open("a", encoding="utf-8") as fh:
                for pair in qa:
                    fh.write(json.dumps({
                        "course": record.get("course"),
                        "lesson_id": record.get("lesson_id"),
                        "question": pair["question"],
                        "answer": pair["answer"],
                        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }, ensure_ascii=False) + "\n")
            exams += len(qa)

        target = inbox / "_processed"
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target / path.name))

    if memory_os is not None:
        if embedder is not None:
            from bio_agent_os.cognitive.semantic_index import backfill_embeddings, coverage
            total = 0
            while True:
                written = backfill_embeddings(memory_os.memories.conn, embedder, limit=100)
                total += written
                if written == 0:
                    break
            print(f"\n  vector: +{total}  ({coverage(memory_os.memories.conn)})")
        memory_os.close()

    print(f"\n  {len(files)} file | {stored} ký ức | {exams} câu hỏi vào đề "
          f"| {rejected} từ chối")
    if not args.dry_run:
        print(f"  trí nhớ: {db}")
        print(f"  bộ đề  : {EXAM_BANK}   <- KHÔNG nằm trong trí nhớ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
