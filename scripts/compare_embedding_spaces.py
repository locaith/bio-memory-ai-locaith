"""Which vector space should the learning store live in — measured, not argued.

The store holds two spaces because an ingest run silently fell back to the
local embedder. Merging is required; which way to merge is a real trade-off:

    OpenAI text-embedding-3-large   3072d, costs a call per query, needs network
    paraphrase-multilingual-MiniLM   384d, free, offline, strong on Vietnamese

`embedder.py` is deliberately local-first and CLAUDE.md §15 wants less cloud
dependence, so the cheap answer is not automatically the right one.

PROTOCOL — known-item retrieval.

For each sampled memory, the query is that memory's own text with the
`[course / lesson]` prefix stripped and only the first half of the words kept:
a half-remembered question, which is what someone actually types. The target is
the memory it came from. No answer key is written by hand, so nothing here can
be tuned toward a preferred outcome.

Reported: recall@1, recall@5, recall@10, MRR@10, query latency.

THE CONTROL — and the reason to trust or discard the whole table.

A third column runs with **no embedder at all**, so retrieval falls back to
token overlap. Queries are built from the target's own words, so lexical
matching should do well; if it does *as well as* both embedding spaces, then
this measurement is not measuring embeddings and the comparison must be thrown
out rather than published. A benchmark that cannot distinguish its subject from
its baseline has no verdict to give, whatever numbers it prints.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

SOURCE = _REPO / "data" / "learning" / "memory.db"

#: The store as it was before the assessment records were ingested: 353 lesson
#: memories, none of which contain the English text the cross-lingual queries
#: are drawn from. Keeping query and content in separate files is what stops
#: the query from finding a verbatim copy of itself.
LESSONS_ONLY = _REPO / "data" / "learning" / "memory_before_assessment_ingest.db"

WORK = _REPO / "data" / "learning" / "_space_compare"

TOP_K = 10


def build_queries(conn: sqlite3.Connection, sample: int,
                  seed: int) -> list[tuple[str, str]]:
    """(query, target_memory_id), deterministic for a seed."""
    import random
    import re

    rows = conn.execute(
        "SELECT memory_id, content FROM cognitive_memories ORDER BY memory_id"
    ).fetchall()
    rng = random.Random(seed)
    picked = rng.sample(rows, min(sample, len(rows)))

    out: list[tuple[str, str]] = []
    for memory_id, content in picked:
        body = re.sub(r"^\[[^\]]*\]\s*", "", str(content)).strip()
        words = body.split()
        if len(words) < 6:
            continue                      # too short to halve into a question
        query = " ".join(words[: max(4, len(words) // 2)])
        out.append((query, memory_id))
    return out


_VN = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệ"
                 r"ìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)

_COURSE_NOISE = re.compile(
    r"\s*(\(ibm\)|-\s*ibm|\(kh[oó]a\s*h[oọ]c\s*\d*|with python|,)\s*", re.I)


def norm_course(name: str) -> str:
    """Course names arrive in several dresses for one course.

    "Python for Data Science, AI & Development with Python" and "Python for
    Data Science, AI & Development - IBM (Khóa học 4" are the same thing, and
    scoring them as different courses would count correct answers as wrong —
    the mistake this project has already made twice on its own benchmarks.
    """
    text = _COURSE_NOISE.sub(" ", str(name or "").lower())
    return re.sub(r"[^a-z0-9& ]+", " ", text).split("(")[0].strip()


def crosslingual_queries() -> list[tuple[str, str, str]]:
    """(kind, course, query) taken from what the learner already wrote.

    The corpus is 95% Vietnamese; these are the English topics and questions
    recorded in the assessment captures. Nothing here is authored for the
    benchmark, and the answer key is the course label, which is metadata.

    The point is lexical distance: an English question about a Vietnamese note
    shares almost no tokens with it, so the token-overlap baseline cannot win
    by construction — which is exactly what sank the known-item protocol.
    """
    import json

    inbox = Path(r"C:\locaith\learning-inbox")
    out: list[tuple[str, str, str]] = []
    for folder in (inbox / "_processed", inbox):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            course = str(rec.get("course") or "").strip()
            if not course:
                continue
            for att in rec.get("attempts") or []:
                if not isinstance(att, dict):
                    continue
                for topic in att.get("weak_topics") or []:
                    text = str(topic).strip()
                    if text and not _VN.search(text):
                        out.append(("weak_topic", course, text))
                for item in att.get("wrong_or_partial") or []:
                    if not isinstance(item, dict):
                        continue
                    for key in ("question", "question_summary"):
                        text = str(item.get(key) or "").strip()
                        if text and not _VN.search(text):
                            out.append(("wrong_q", course, text))
                            break
    return out


def score_crosslingual(memory_os, queries: list[tuple[str, str, str]]) -> dict:
    """Did the top hits come from the course the question belongs to?"""
    from bio_agent_os.cognitive.models import AccessContext

    ctx = AccessContext(tenant_id="tuananh", workspace_id="learning")
    top1 = top3 = top10 = 0
    latencies: list[float] = []
    scored = 0

    for _, course, query in queries:
        want = norm_course(course)
        if not want:
            continue
        scored += 1
        started = time.perf_counter()
        hits = memory_os.recall(query=query, context=ctx, limit=TOP_K) or []
        latencies.append((time.perf_counter() - started) * 1000)

        got = []
        for hit in hits:
            m = re.match(r"^\[([^/\]]+)", str(hit.memory.content))
            got.append(norm_course(m.group(1)) if m else "")
        if got[:1] == [want]:
            top1 += 1
        if want in got[:3]:
            top3 += 1
        if want in got:
            top10 += 1

    if not scored:
        return {"n": 0}
    return {
        "n": scored,
        "course@1": top1 / scored,
        "course@3": top3 / scored,
        "course@10": top10 / scored,
        "hits@1": top1,
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[max(int(len(latencies) * 0.95) - 1, 0)],
    }


def make_store(tag: str, backend: str, source: Path = SOURCE):
    """A copy of the store, re-embedded end to end by one backend."""
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes, coverage,
    )

    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / f"{tag}.db"
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(source) + suffix)
        if src.exists():
            shutil.copy2(src, str(target) + suffix)

    if backend == "none":
        return MemoryOS(target, embedder=None), None

    embedder = _embedder_for(backend)
    conn = sqlite3.connect(str(target))
    conn.execute("DELETE FROM memory_embeddings")
    conn.execute("DELETE FROM embedding_calibration")
    conn.commit()

    probe = embedder.embed("kiểm tra số chiều")
    print(f"  [{tag}] backend={getattr(embedder, 'backend', '?')} "
          f"model={getattr(embedder, 'model_id', '?')} dims={len(probe)}")
    if getattr(embedder, "backend", "") == "hash":
        raise SystemExit(
            f"[{tag}] embedder rơi về hash mode — recall gần như ngẫu nhiên. "
            f"Dừng lại: một cột hash so với một cột thật là bảng số vô nghĩa."
        )

    written = 0
    while True:
        n = backfill_embeddings(conn, embedder, limit=100)
        written += n
        if n == 0:
            break
    calibrate_with_probes(conn, embedder)
    print(f"  [{tag}] {written} vector, {coverage(conn)['models']}")
    conn.close()

    return MemoryOS(target, embedder=embedder), embedder


def _embedder_for(backend: str):
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    for key in ("EMBEDDING_BACKEND", "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL",
                "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
        os.environ.pop(key, None)

    if backend == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY không có trong .env")
        # `Embedder` is local-first on purpose: sentence-transformers wins over a
        # bare OPENAI_API_KEY. The documented way to ask for the cloud endpoint
        # is the dedicated embeddings config, so use that rather than defeating
        # the design.
        os.environ["EMBEDDING_API_KEY"] = api_key
        os.environ["EMBEDDING_MODEL"] = "text-embedding-3-large"
        os.environ["EMBEDDING_DIMENSIONS"] = "3072"

    from bio_agent_os.core.embedder import Embedder

    return Embedder()


def score(memory_os, queries: list[tuple[str, str]]) -> dict:
    from bio_agent_os.cognitive.models import AccessContext

    ctx = AccessContext(tenant_id="tuananh", workspace_id="learning")
    ranks: list[int | None] = []
    latencies: list[float] = []

    for query, target in queries:
        started = time.perf_counter()
        hits = memory_os.recall(query=query, context=ctx, limit=TOP_K) or []
        latencies.append((time.perf_counter() - started) * 1000)
        rank = None
        for index, hit in enumerate(hits, start=1):
            if hit.memory.memory_id == target:
                rank = index
                break
        ranks.append(rank)

    found = [r for r in ranks if r is not None]
    return {
        "n": len(queries),
        "recall@1": sum(1 for r in found if r == 1) / len(queries),
        "recall@5": sum(1 for r in found if r <= 5) / len(queries),
        "recall@10": len(found) / len(queries),
        "mrr@10": sum(1 / r for r in found) / len(queries),
        "p50_ms": statistics.median(latencies),
        "p95_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1],
    }


def answerable_courses(store: Path) -> set[str]:
    """Courses that actually have content to be found."""
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    out: set[str] = set()
    for (text,) in conn.execute("SELECT content FROM cognitive_memories"):
        m = re.match(r"^\[([^/\]]+)", str(text))
        if m:
            out.add(norm_course(m.group(1)))
    conn.close()
    return {c for c in out if c}


def run_crosslingual() -> int:
    if not LESSONS_ONLY.exists():
        raise SystemExit(f"không thấy {LESSONS_ONLY}")

    raw = crosslingual_queries()
    have = answerable_courses(LESSONS_ONLY)

    # A question whose course has no content in the store cannot be answered by
    # anything, and scoring it drags every column toward zero equally while
    # looking like a measurement. Measured here: 13 of 24. This project has
    # already published two benchmarks that punished correct behaviour; the
    # cheapest defence is to check that the answer exists before asking.
    queries = [q for q in raw if norm_course(q[1]) in have]
    dropped = len(raw) - len(queries)

    print(f"\n{len(raw)} truy vấn tiếng Anh trên kho 95% tiếng Việt")
    print(f"nội dung: {LESSONS_ONLY.name} (chỉ ký ức bài học, không có bài kiểm tra)")
    if dropped:
        missing = sorted({norm_course(q[1]) for q in raw
                          if norm_course(q[1]) not in have})
        print(f"\n  BỎ {dropped}/{len(raw)} câu: khoá của chúng không có ký ức nào")
        for name in missing:
            print(f"    - {name}")
        print("  Chấm những câu này sẽ trừ điểm cả ba cột cho việc không ai làm được.")
    print(f"\ncòn lại: {len(queries)} câu, {len({norm_course(q[1]) for q in queries})} khoá\n")

    results = {}
    for tag, backend in (("xl-openai", "openai"),
                         ("xl-local", "sentence-transformers"),
                         ("xl-no-vector", "none")):
        memory_os, _ = make_store(tag, backend, source=LESSONS_ONLY)
        results[tag] = score_crosslingual(memory_os, queries)
        memory_os.close()

    print(f"\n{'':<14}{'course@1':>10}{'@3':>8}{'@10':>8}{'đúng/n':>10}"
          f"{'p50 ms':>10}")
    for tag, r in results.items():
        fraction = "{}/{}".format(r["hits@1"], r["n"])
        print(f"{tag:<14}{r['course@1']:>10.3f}{r['course@3']:>8.3f}"
              f"{r['course@10']:>8.3f}{fraction:>10}{r['p50_ms']:>10.1f}")

    base = results["xl-no-vector"]["course@1"]
    openai = results["xl-openai"]["course@1"]
    local = results["xl-local"]["course@1"]
    print(f"\n  đối chứng không vector: {base:.3f}")
    print(f"  openai {openai:+.3f} so với đối chứng | "
          f"cục bộ {local - base:+.3f} so với đối chứng")

    n = results["xl-no-vector"]["n"]
    classes = len({norm_course(q[1]) for q in queries})

    # Three ways this protocol can fail to have a verdict, checked before any
    # winner is announced. An earlier version declared "local wins 4/11" while
    # all three of these were true, which is how a benchmark ends up
    # confirming whatever it was pointed at.
    blockers: list[str] = []
    if n < 30:
        blockers.append(
            f"n = {n}. Chênh vài câu ở cỡ mẫu này không phân biệt được với may rủi.")
    if classes < 3:
        blockers.append(
            f"đáp án chỉ còn {classes} lớp. Bài toán gần như thoái hoá: "
            f"'đúng khoá nào' mà chỉ có một khoá thì chỉ còn là 'có trả về gì không'.")
    if max(openai, local) - base < 0.08:
        blockers.append(
            "vector không hơn đối chứng đủ rõ — phép đo đang đo so khớp từ.")

    # And one that is structural rather than statistical: course membership is a
    # proxy for relevance, not relevance itself. A memory from a different
    # course can be the correct answer, and this rubric marks it wrong.
    blockers.append(
        "chuẩn đúng là TÊN KHOÁ, không phải mức liên quan. Một ký ức đúng chủ "
        "đề nhưng thuộc khoá khác vẫn bị chấm sai.")

    print("\n  KHÔNG KẾT LUẬN ĐƯỢC VỀ CHẤT LƯỢNG. Lý do:")
    for reason in blockers:
        print(f"    - {reason}")
    print("\n  Thứ đo được chắc chắn là độ trễ:")
    for tag, r in results.items():
        print(f"    {tag:<14} p50 {r['p50_ms']:>7.1f} ms")
    print("\n  Chọn không gian nào nên dựa vào độ trễ, chi phí và quyền riêng "
          "tư,\n  vì đó là những thứ phép đo này nói được — không phải chất lượng.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--protocol", choices=("known-item", "crosslingual"),
                    default="known-item")
    args = ap.parse_args()

    if args.protocol == "crosslingual":
        return run_crosslingual()

    if not SOURCE.exists():
        raise SystemExit(f"không thấy {SOURCE}")

    conn = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    queries = build_queries(conn, args.sample, args.seed)
    conn.close()
    print(f"\n{len(queries)} truy vấn known-item, seed={args.seed}\n")

    results = {}
    for tag, backend in (("openai", "openai"),
                         ("local", "sentence-transformers"),
                         ("no-vector", "none")):
        memory_os, _ = make_store(tag, backend)
        results[tag] = score(memory_os, queries)
        memory_os.close()

    print(f"\n{'':<12}{'r@1':>8}{'r@5':>8}{'r@10':>8}{'MRR':>8}"
          f"{'p50 ms':>10}{'p95 ms':>10}")
    for tag, r in results.items():
        print(f"{tag:<12}{r['recall@1']:>8.3f}{r['recall@5']:>8.3f}"
              f"{r['recall@10']:>8.3f}{r['mrr@10']:>8.3f}"
              f"{r['p50_ms']:>10.1f}{r['p95_ms']:>10.1f}")

    # The control decides whether the other two columns mean anything.
    base = results["no-vector"]["mrr@10"]
    best = max(results["openai"]["mrr@10"], results["local"]["mrr@10"])
    gap = best - base
    print(f"\n  đối chứng không vector: MRR {base:.3f}")
    print(f"  tốt nhất có vector    : MRR {best:.3f}   (chênh {gap:+.3f})")
    if gap < 0.02:
        print("\n  ⚠  KHÔNG KẾT LUẬN ĐƯỢC. Bỏ vector đi mà kết quả gần như không")
        print("     đổi, nghĩa là phép đo này đang đo so khớp từ chứ không đo")
        print("     embedding. Không dùng bảng trên để chọn không gian.")
    else:
        winner = "openai" if results["openai"]["mrr@10"] >= results["local"]["mrr@10"] \
            else "local"
        margin = abs(results["openai"]["mrr@10"] - results["local"]["mrr@10"])
        print(f"\n  vector có tác dụng thật (+{gap:.3f} so với đối chứng).")
        print(f"  hơn kém giữa hai không gian: {margin:.3f} — nghiêng về {winner}")
        if margin < 0.02:
            print("  nhưng khoảng cách này quá nhỏ để coi là hơn thật;")
            print("  chọn theo chi phí và quyền riêng tư thì hợp lý hơn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
