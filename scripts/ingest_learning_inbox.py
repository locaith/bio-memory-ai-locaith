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

#: Two shapes arrive through the same inbox and they are not the same record.
#:
#: Measured on 2026-08-14: 34 of 82 captures sat in `_rejected`, every one of
#: them an assessment, every one rejected for "thiếu lesson_id" and "thiếu
#: summary" — fields an assessment does not have and was never going to have.
#: A lesson has a summary and key facts; an assessment has an item, attempts,
#: a score and a list of what went wrong. Requiring the lesson shape of both
#: threw away 41% of everything captured, including 12 self-recorded weak
#: topics and 12 questions answered wrongly, which this file's own docstring
#: calls the most valuable thing in a capture.
#: A third shape turned up the moment the second was handled: a course
#: completion, carrying a certificate id and a final grade and none of the
#: fields either of the others has. Adding kinds one rejection at a time is the
#: pattern to notice — the inbox accepts whatever the capture tool emits, so the
#: validator has to describe shapes rather than demand one.
LESSON = "lesson"
ASSESSMENT = "assessment"
COMPLETION = "completion"

REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    LESSON: ("schema", "course", "lesson_id", "summary"),
    ASSESSMENT: ("schema", "course", "lesson_id"),
    COMPLETION: ("schema", "course"),
}

#: The same thing under different names across captures. Three shapes of
#: `wrong_or_partial` were observed in one pile of 34 files, so reading a single
#: key would silently drop whichever shape it did not match — the failure this
#: whole file keeps producing. First present wins.
_QUESTION_KEYS = ("question", "question_summary")
_ANSWER_KEYS = ("correct_answer", "ai_suggested_answer",
                "ai_suggested_or_confirmed_answer")
_CHOSEN_KEYS = ("user_selected_answer", "user_selected")
_RESULT_KEYS = ("points_awarded", "result")


DEFAULT_CONFIDENCE = 0.7


def kind_of(record: dict) -> str:
    """Lesson or assessment, decided by what the record carries.

    Not by `type` alone: 26 of the 34 rejected assessments had no `type` field
    at all, so a check that trusted it would still reject three quarters of
    them.
    """
    if str(record.get("item_type") or "").strip().lower() in {
        "course-completion", "course_completion", "certificate"
    } or record.get("certificate_id") or record.get("final_grade_percent") is not None:
        return COMPLETION
    if str(record.get("type") or "").strip().lower() in {
        "quiz", "exam", "assessment", "graded_quiz", "test"
    }:
        return ASSESSMENT
    if any(record.get(k) for k in ("item_id", "item_title", "item_type")):
        return ASSESSMENT
    return LESSON


def _first(mapping: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(mapping.get(key) or "").strip()
        if value:
            return value
    return ""


def repair(record: dict) -> list[str]:
    """Fix what can be fixed without inventing knowledge. Returns what was done.

    The line this draws matters, and it is not "never fill anything in":

    * `confidence` arriving as the word "high" is a **metadata format** problem.
      Fifteen files of real facts were rejected over it. Translating "high" to
      0.9 would be picking a number and calling it the author's certainty, so
      the word is kept verbatim and the numeric field falls back to the same
      neutral default used when it is absent — recorded as defaulted, so nobody
      later reads it as a measurement.
    * `lesson_id` is an **identifier**, not a claim. Deriving a stable one from
      the title loses nothing and keeps the lesson.
    * `summary` and `key_facts` are the knowledge itself. Missing means the
      capture is empty, and an empty capture stays rejected — there is nothing
      to repair, and inventing a summary would put a sentence nobody wrote into
      a memory nobody could tell from a real one.
    """
    notes: list[str] = []

    confidence = record.get("confidence")
    if confidence is not None and not (
        isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        and 0 <= confidence <= 1
    ):
        record["confidence_raw"] = confidence
        record["confidence"] = DEFAULT_CONFIDENCE
        record["confidence_defaulted"] = True
        notes.append(f"confidence {confidence!r} không phải số 0..1 — giữ nguyên "
                     f"chữ gốc, dùng mặc định {DEFAULT_CONFIDENCE}")

    if not str(record.get("lesson_id") or "").strip():
        # An assessment names its item rather than a lesson, and `item_id` is
        # already a stable identifier — no need to derive one from a title.
        explicit = str(record.get("item_id") or "").strip()
        title = str(record.get("lesson_title") or record.get("item_title") or "").strip()
        if explicit:
            record["lesson_id"] = explicit
            record["lesson_id_derived"] = True
            notes.append(f"lesson_id thiếu — dùng item_id: {explicit}")
        elif title:
            import hashlib
            record["lesson_id"] = "auto-" + hashlib.sha256(
                (str(record.get("course")) + title).encode("utf-8")
            ).hexdigest()[:8]
            record["lesson_id_derived"] = True
            notes.append(f"lesson_id thiếu — suy từ tên bài: {record['lesson_id']}")
    return notes


def problems(record: dict) -> list[str]:
    """What is missing, judged against the shape this record actually is.

    An assessment needs no `summary`: what it carries instead is a score, the
    questions that went wrong, and the topics the learner flagged as weak. It
    is rejected when it carries none of those, because then nothing was
    captured — the same rule as an empty lesson, applied to the right fields.
    """
    issues = []
    kind = kind_of(record)
    if record.get("schema") != SCHEMA:
        issues.append(f"schema phải là {SCHEMA}")
    for field in REQUIRED_BY_KIND[kind]:
        if not str(record.get(field, "")).strip():
            issues.append(f"thiếu hoặc rỗng: {field}")
    for field in ("key_facts", "qa", "attempts"):
        if field in record and not isinstance(record[field], list):
            issues.append(f"{field} phải là danh sách")
    if kind in (ASSESSMENT, COMPLETION) and not memory_texts(record):
        issues.append(f"{kind}: không có nội dung nào đáng ghi nhớ")
    return issues


def wrong_questions(record: dict) -> set[str]:
    """Every question this capture says was answered wrongly, both shapes.

    A lesson marks them `attempts[].was_correct == False`; an assessment nests
    them under `attempts[].wrong_or_partial[]`. The exam-bank leak guard reads
    this, so a shape it cannot see is a question that lands verbatim in memory
    *and* in the question bank — recall would then find the card instead of the
    knowledge, and the score would mean nothing.
    """
    out: set[str] = set()
    for attempt in record.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        if attempt.get("was_correct") is False:
            question = str(attempt.get("question", "")).strip()
            if question:
                out.add(question.lower())
        for item in attempt.get("wrong_or_partial") or []:
            if isinstance(item, dict):
                question = _first(item, _QUESTION_KEYS)
                if question:
                    out.add(question.lower())
    return out


def _assessment_texts(record: dict, prefix: str) -> list[str]:
    """What is worth remembering from a graded attempt.

    Ordered by how much it says about the learner rather than the score: a
    topic they flagged as weak is a standing instruction to themselves, a
    question they got wrong marks where understanding actually failed, and the
    percentage is context for both.
    """
    out: list[str] = []

    for attempt in record.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue

        for topic in attempt.get("weak_topics") or []:
            text = str(topic).strip()
            if text:
                out.append(f"{prefix} Điểm yếu tự ghi nhận: {text}")

        for item in attempt.get("wrong_or_partial") or []:
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    out.append(f"{prefix} Làm sai: {text}")
                continue
            question = _first(item, _QUESTION_KEYS)
            answer = _first(item, _ANSWER_KEYS)
            chosen = _first(item, _CHOSEN_KEYS)
            why = str(item.get("explanation") or item.get("note") or "").strip()
            if not (question or why):
                continue
            line = f"{prefix} Làm sai câu: \"{question}\"." if question \
                else f"{prefix} Làm sai một câu."
            if chosen and chosen != answer:
                line += f" Đã chọn: {chosen}."
            if answer:
                line += f" Đáp án đúng: {answer}."
            if why:
                line += f" Vì: {why}"
            out.append(line)

        score = attempt.get("score_percent")
        if score is not None:
            passed = attempt.get("passed")
            verdict = "đạt" if passed else ("chưa đạt" if passed is False else "")
            number = attempt.get("attempt_no")
            out.append(
                f"{prefix} Kết quả {score}%"
                + (f" ở lần thứ {number}" if number else "")
                + (f", {verdict}" if verdict else "") + "."
            )
    return out


def _completion_texts(record: dict) -> list[str]:
    """A finished course is a milestone, and milestones are what a progress
    question is asking about. Stored as facts with their verification id, so
    "how far am I through the program" has an answer that can be checked."""
    course = str(record.get("course") or "").strip()
    if not course:
        return []

    out: list[str] = []
    grade = record.get("final_grade_percent")
    when = str(record.get("completion_date") or "").strip()
    line = f"Đã hoàn thành khoá {course}"
    if grade is not None:
        line += f" với điểm tổng kết {grade}%"
    if when:
        line += f", ngày {when}"
    out.append(line + ".")

    index = record.get("course_number_in_program")
    total = record.get("total_courses_in_program")
    if index and total:
        out.append(f"Tiến độ chương trình: đã xong khoá {index}/{total} "
                   f"({course}).")

    certificate = str(record.get("certificate_id") or "").strip()
    if certificate:
        url = str(record.get("verify_url") or "").strip()
        out.append(f"Chứng chỉ {course}: mã {certificate}"
                   + (f", kiểm tra tại {url}" if url else "") + ".")
    return out


def memory_texts(record: dict) -> list[str]:
    """What actually goes into memory.

    Each fact is stored on its own rather than as one blob: retrieval scores
    whole memories, so a fact buried in a paragraph of six competes on the
    paragraph's average relevance instead of its own.

    Course and lesson are prefixed onto every one. A fact that reads "the three
    deployment models are public, private and hybrid" is unanchored six months
    later — anchored, it can answer "what did I learn in the cloud course".
    """
    prefix = (f"[{record.get('course')} / "
              f"{record.get('lesson_title') or record.get('item_title') or record.get('lesson_id')}]")
    kind = kind_of(record)
    if kind == ASSESSMENT:
        return _assessment_texts(record, prefix)
    if kind == COMPLETION:
        return _completion_texts(record)

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
        # `.env` holds OPENAI_API_KEY, and `Embedder()` picks its backend by
        # whether that key is in the *process* environment. Without this load
        # it silently chose the local model instead, wrote 60 vectors into a
        # 384-dimension space beside 353 in a 3072-dimension one, and split the
        # store in two: `cosine` scores mismatched dimensions 0.0, retrieval
        # reads 0.0 as "unrelated", and those 60 memories became unreachable by
        # any query. Nothing raised, nothing logged.
        from dotenv import load_dotenv

        load_dotenv(_REPO / ".env")

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
            notes = repair(record)
            for note in notes:
                print(f"    sửa: {note}")
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
        attempted = wrong_questions(record)
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
            from bio_agent_os.cognitive.semantic_index import (
                backfill_embeddings, coverage, models_present,
            )
            before = models_present(memory_os.memories.conn)
            total = 0
            while True:
                written = backfill_embeddings(memory_os.memories.conn, embedder, limit=100)
                total += written
                if written == 0:
                    break
            report = coverage(memory_os.memories.conn)
            print(f"\n  vector: +{total}  ({report})")

            # A store acquiring a second embedding space is the moment to say
            # so out loud. Vectors from different spaces cannot be compared:
            # `cosine` returns 0.0 for mismatched dimensions and retrieval
            # reads that as "unrelated", so each half becomes invisible to
            # queries embedded by the other. This was found by reading a
            # coverage line, which is not a way to find things.
            after = models_present(memory_os.memories.conn)
            spaces = {(m["model"], m["dims"]) for m in report.get("models", [])}
            if len(spaces) > 1:
                print("\n  ⚠  KHO CÓ NHIỀU KHÔNG GIAN VECTOR:")
                for name, dims in sorted(spaces):
                    print(f"       {name} ({dims} chiều)")
                print("     Vector khác không gian không so sánh được với nhau.")
                print("     Truy vấn nhúng bằng model nào chỉ 'thấy' vector của")
                print("     model đó; phần còn lại rơi về so khớp từ.")
                if after - before:
                    print(f"     Lần chạy này vừa thêm: {sorted(after - before)}")
                print("     Nạp lại toàn bộ bằng một model để gộp về một không gian.")
        memory_os.close()

    print(f"\n  {len(files)} file | {stored} ký ức | {exams} câu hỏi vào đề "
          f"| {rejected} từ chối")
    if not args.dry_run:
        print(f"  trí nhớ: {db}")
        print(f"  bộ đề  : {EXAM_BANK}   <- KHÔNG nằm trong trí nhớ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
