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
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

SOURCE = _REPO / "data" / "learning" / "memory.db"
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


def make_store(tag: str, backend: str):
    """A copy of the store, re-embedded end to end by one backend."""
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes, coverage,
    )

    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / f"{tag}.db"
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(SOURCE) + suffix)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260814)
    args = ap.parse_args()

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
