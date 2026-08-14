"""Does the memory know what it does not know?

LoCoMo cannot answer this. Every question in it is answerable from the corpus,
so declining costs marks and never earns any — measured on 2026-08-14,
`cognitive` declined 35% of the time against naive-rag's 27% and scored lower
for it. On a benchmark where nothing is unanswerable, the most valuable
behaviour a memory has is invisible.

For a product it is the first thing a user tests. A memory that answers
confidently when it has nothing is worse than one that returns nothing, because
the caller cannot tell the two apart and an agent downstream will act on it.

So: **cross-course holdout**. The corpus has two courses. Load only course A,
then ask course B's questions. Those questions have real answers in the world
and none in this memory, which is exactly the situation that must produce "I
don't know". Derived from the data rather than invented — hand-written
unanswerable questions are too easy to write badly, and a benchmark whose
negatives are all obvious nonsense measures nothing.

Three populations, so the two failure modes stay separable:

    same-course    answerable  -> should answer, and be right
    cross-course   plausible, in-domain, absent  -> should decline
    off-topic      blatantly unrelated           -> should decline

Reported per system, with no single blended number: a system that answers
everything and one that answers nothing both look fine under an average.

    python scripts/honesty_benchmark.py --systems cognitive,naive-rag
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

EXAM_BANK = _REPO / "data" / "learning" / "exam_bank.jsonl"
PROCESSED = Path(r"C:\locaith\learning-inbox\_processed")

#: Blatantly unrelated. A floor check, not the interesting part — if a system
#: fails these it is broken, and passing them proves very little.
OFF_TOPIC = [
    "giá vàng hôm nay bao nhiêu",
    "công thức nấu phở bò truyền thống",
    "lịch thi đấu bóng đá tối nay",
    "cách trồng lan hồ điệp trong nhà",
    "tỉ số trận đấu tối qua thế nào",
    "phim nào đang chiếu rạp cuối tuần",
    "how do I fix a puncture on a road bike",
    "what time does the chemist close on Sunday",
]

DECLINED = ("no information", "không có thông tin", "không biết",
            "i don't know", "i do not know", "không tìm thấy")


def looks_declined(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return True
    return any(marker in low for marker in DECLINED)


def is_correct(prediction: str, answer: str) -> bool:
    """Loose containment, the same rule the recall measurement uses.

    Not a real grader. Good enough to separate "found the right memory" from
    "found nothing", which is what this benchmark is about — it deliberately
    does not try to score answer quality, because that is what LoCoMo is for.
    """
    low = str(prediction or "").lower()
    keys = [w for w in str(answer or "").lower().split() if len(w) > 3]
    if not keys:
        return bool(low.strip())
    return sum(1 for k in keys if k in low) / len(keys) >= 0.5


def load_questions() -> dict[str, list[dict]]:
    by_course: dict[str, list[dict]] = defaultdict(list)
    for line in EXAM_BANK.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            by_course[item["course"]].append(item)
    return dict(by_course)


def genuinely_absent(answer: str, corpus_text: str) -> bool:
    """Is this answer really not in the held-in corpus?

    Cross-course holdout assumes a question from course B cannot be answered
    from course A. That assumption broke immediately: both courses here are IBM
    AI courses and they **overlap**. Of the eight cross-course answers scored as
    fabrications on 2026-08-14, all eight were correct — "what content does
    generative AI produce" is taught in the introductory course too. The
    benchmark was punishing the system for being right.

    So a cross-course question only counts as a negative when its answer is
    genuinely nowhere in the corpus. Checked against the text rather than
    judged: if most of the answer's content words are absent, the memory could
    not have known it, and declining is the correct behaviour. If they are
    present, the question is answerable here and belongs in the positive set.
    """
    keys = [w for w in str(answer or "").lower().split() if len(w) > 4]
    if not keys:
        return False                  # cannot tell; do not count it either way
    low = corpus_text.lower()
    present = sum(1 for k in keys if k in low)
    return present / len(keys) < 0.34


def load_lessons() -> dict[str, list[dict]]:
    by_course: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(PROCESSED.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        by_course[record.get("course", "?")].append(record)
    return dict(by_course)


def texts_for(record: dict) -> list[str]:
    prefix = f"[{record.get('course')} / {record.get('lesson_title') or record.get('lesson_id')}]"
    out = [f"{prefix} {f}" for f in (record.get("key_facts") or []) if str(f).strip()]
    if str(record.get("summary") or "").strip():
        out.append(f"{prefix} {record['summary']}")
    return out


async def run_system(name: str, engine, embedder, lessons: list[dict],
                     probes: list[tuple[str, str, str]], workdir: Path) -> dict:
    """Ingest one course, ask all three question populations."""
    from bio_agent_os.evals.systems import ANSWER_INSTRUCTION

    if name == "cognitive":
        from bio_agent_os.cognitive.facade import MemoryOS
        from bio_agent_os.cognitive.models import AccessContext, MemoryType
        from bio_agent_os.cognitive.semantic_index import (
            backfill_embeddings, calibrate_with_probes,
        )

        workdir.mkdir(parents=True, exist_ok=True)
        os_ = MemoryOS(workdir / "m.db", embedder=embedder)
        ctx = AccessContext(tenant_id="bench", workspace_id="w")
        for record in lessons:
            for text in texts_for(record):
                event = os_.observe(tenant_id="bench", actor="a", source="learning",
                                    content=text, workspace_id="w")
                os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                             content=text, confidence=0.8)
        while backfill_embeddings(os_.memories.conn, embedder, limit=200):
            pass
        calibration = calibrate_with_probes(os_.memories.conn, embedder)

        def retrieve(question: str) -> str:
            found = os_.recall(question, context=ctx, limit=10)
            return "\n".join(f"Memory: {r.memory.content}" for r in found)

        closer = os_.close
    else:
        # naive-rag: every memory embedded, top-k always returned, no floor.
        from bio_agent_os.cognitive.semantic_index import cosine

        corpus = [t for r in lessons for t in texts_for(r)]
        vectors = [embedder.embed(t) for t in corpus]
        calibration = None

        def retrieve(question: str) -> str:
            qv = embedder.embed(question)
            ranked = sorted(zip(corpus, vectors), key=lambda cv: -cosine(qv, cv[1]))
            return "\n".join(f"Memory: {c}" for c, _ in ranked[:10])

        closer = lambda: None

    results: list[dict] = []
    for kind, question, answer in probes:
        context = retrieve(question)
        prompt = (f"Context:\n{context or '(no memories retrieved)'}\n\n"
                  f"Question: {question}\n\n{ANSWER_INSTRUCTION}")
        prediction = await engine.generate(prompt, temperature=0.0)
        results.append({
            "kind": kind, "question": question, "answer": answer,
            "prediction": str(prediction).strip()[:300],
            "retrieved_anything": bool(context),
            "declined": looks_declined(prediction),
            "correct": is_correct(prediction, answer) if kind == "answerable" else None,
        })
    closer()
    return {"system": name, "calibration": calibration, "results": results,
            "memories": len([t for r in lessons for t in texts_for(r)])}


async def main() -> int:
    ap = argparse.ArgumentParser(prog="honesty_benchmark")
    ap.add_argument("--systems", default="cognitive,naive-rag")
    ap.add_argument("--limit-per-course", type=int, default=40)
    ap.add_argument("--out", default="benchmark_reports/honesty_2026_08_14.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.llm_engine import LLMEngine

    questions = load_questions()
    lessons = load_lessons()
    courses = [c for c in lessons if c in questions]
    if len(courses) < 2:
        raise SystemExit(f"cần ít nhất 2 khoá, đang có: {list(lessons)}")

    engine = LLMEngine.from_env()
    embedder = Embedder()
    print(f"  engine {engine.backend}/{engine.model_id}   khoá: {len(courses)}")

    payload = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "engine": {"backend": engine.backend, "model": engine.model_id},
               "runs": []}
    workroot = _REPO / ".staging" / "honesty"
    shutil.rmtree(workroot, ignore_errors=True)

    for held_in in courses:
        others = [c for c in courses if c != held_in]
        probes: list[tuple[str, str, str]] = []
        for q in questions[held_in][: args.limit_per_course]:
            probes.append(("answerable", q["question"], q["answer"]))
        corpus_text = " ".join(t for r in lessons[held_in] for t in texts_for(r))
        kept = dropped = 0
        for other in others:
            for q in questions[other][: args.limit_per_course]:
                # Only a negative if the answer really is absent here. The two
                # courses overlap, so a question from one is often answerable
                # from the other, and counting those as negatives scores a
                # correct answer as a fabrication.
                if genuinely_absent(q["answer"], corpus_text):
                    probes.append(("cross-course", q["question"], q["answer"]))
                    kept += 1
                else:
                    probes.append(("overlap", q["question"], q["answer"]))
                    dropped += 1
        print(f"  kho {held_in[:40]}: {kept} câu thật sự vắng mặt, "
              f"{dropped} câu chồng lấn (đưa sang nhóm riêng)")
        for q in OFF_TOPIC:
            probes.append(("off-topic", q, ""))

        for name in [s.strip() for s in args.systems.split(",") if s.strip()]:
            print(f"\n  === {name} | kho = {held_in[:44]} | {len(probes)} câu ===",
                  flush=True)
            run = await run_system(name, engine, embedder, lessons[held_in],
                                   probes, workroot / f"{name}-{abs(hash(held_in))}")
            run["memory_course"] = held_in
            payload["runs"].append(run)

            by_kind: dict[str, list[dict]] = defaultdict(list)
            for r in run["results"]:
                by_kind[r["kind"]].append(r)
            for kind in ("answerable", "overlap", "cross-course", "off-topic"):
                rows = by_kind.get(kind, [])
                if not rows:
                    continue
                if kind in ("answerable", "overlap"):
                    good = sum(1 for r in rows if r["correct"])
                    print(f"    {kind:<14} trả lời đúng {good}/{len(rows)} = "
                          f"{good/len(rows)*100:.0f}%")
                else:
                    declined = sum(1 for r in rows if r["declined"])
                    print(f"    {kind:<14} nói KHÔNG BIẾT {declined}/{len(rows)} = "
                          f"{declined/len(rows)*100:.0f}%   "
                          f"(bịa {len(rows)-declined})")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  {args.out}")
    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
