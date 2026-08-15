"""Forgetting, measured in both directions at once.

The previous round fixed under-deletion and created over-deletion, and only the
improving direction was measured — `forgotten` went 0.000 to 0.278 and the
patch was called a success. Measured afterwards, the same patch was removing
eight of a person's records on a request about one:

    CA 2  khớp 7, xoá lây 6
    CA 3  khớp 9, xoá lây 8

A one-directional measurement is an unfinished measurement, so this reports all
four numbers together and refuses to rank a change on any one of them:

    scope_recall              of what should go, how much went
    scope_precision           of what went, how much should have
    missed_deletion_rate      1 - recall
    collateral_deletion_rate  1 - precision

Ground truth per request comes from the world, never from the deleter: a claim
belongs to a deletion request when it is about that subject *and* carries the
attribute the request names. Both halves are the ledger's, not the system's.

It also prints the score distribution — every candidate's topic similarity,
split by whether it should have been deleted. A cut-off is only worth having if
those two populations separate; `best * 0.6` was chosen without ever looking,
and it does not.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import unicodedata
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.forget_scope import preview, resolve  # noqa: E402
from bio_agent_os.cognitive.semantic_index import (  # noqa: E402
    backfill_embeddings, cosine, load_vectors,
)
from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter  # noqa: E402
from bio_agent_os.evals.lifetime_world import (  # noqa: E402
    ATTRIBUTES, EventKind, generate,
)

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


def _truth_for(request_event, events, name: str, upto: int) -> set[str]:
    """Which sentences the world says belong to this request.

    A memory is in scope when it is about this subject and this attribute.
    Derived from the event stream, so the deleter's own opinion of its scope
    never enters the answer key.
    """
    return {
        _fold(e.text) for e in events
        if e.tick <= upto and e.subject_id == request_event.subject_id
        and e.attribute == request_event.attribute and e.value
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--out",
                    default="benchmark_reports/forget_scope_2026_08_15.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder

    events, ledger, people = generate(ticks=args.ticks, subjects=args.subjects,
                                      seed=args.seed)
    name_of = {p.subject_id: p.name for p in people}
    label_of = {a.key: a.label_vi for a in ATTRIBUTES}
    embedder = Embedder()

    forgets = [e for e in events if e.kind is EventKind.FORGET]
    print(f"{len(forgets)} yêu cầu xoá trong thế giới {args.ticks} sự kiện\n")

    rows: list[dict] = []
    positives: list[float] = []       # scores of memories that SHOULD go
    negatives: list[float] = []       # scores of memories that should NOT

    adapter = CognitiveAdapter(
        Path(tempfile.mkdtemp(prefix="scope-")) / "m.db", embedder=embedder)
    fed = 0
    for request in forgets:
        while fed < request.tick - 1:
            event = events[fed]
            fed += 1
            if event.kind is not EventKind.FORGET:
                adapter.ingest(event)
            if fed in CHECKPOINTS:
                while backfill_embeddings(adapter.memory_os.memories.conn,
                                          embedder, limit=200):
                    pass

        name = name_of.get(request.subject_id, "")
        truth = _truth_for(request, events, name, request.tick)
        scope = resolve(request.text)
        matched = {_fold(m.content) for m in preview(adapter.memory_os, scope)}

        conn = adapter.memory_os.memories.conn
        present = {_fold(c) for (c,) in
                   conn.execute("SELECT content FROM cognitive_memories")}
        should_go = truth & present
        hit = matched & should_go
        collateral = matched - should_go
        missed = should_go - matched

        # The distribution behind the cut-off, gathered while the store is in
        # the right state.
        if scope.topic:
            candidates = [(m, c) for m, c in
                          conn.execute("SELECT memory_id, content FROM cognitive_memories")
                          if all(w.lower() in _fold(c) for w in name.split())]
            topic_vector = embedder.embed(scope.topic)
            vectors = load_vectors(conn, [m for m, _ in candidates],
                                   dims=len(topic_vector))
            for memory_id, content in candidates:
                vector = vectors.get(memory_id) or embedder.embed(content)
                score = cosine(topic_vector, vector)
                (positives if _fold(content) in should_go else negatives).append(score)

        rows.append({
            "tick": request.tick,
            "request": request.text,
            "slot": f"{request.subject_id}/{request.attribute}",
            "label": label_of.get(request.attribute, ""),
            "should_go": len(should_go),
            "matched": len(matched),
            "hit": len(hit),
            "collateral": len(collateral),
            "missed": len(missed),
            "collateral_examples": sorted(collateral)[:3],
        })
        print(f"  T{request.tick:<5} {request.text[:44]:<46} "
              f"cần {len(should_go):>2} | khớp {len(matched):>2} | "
              f"đúng {len(hit):>2} | lây {len(collateral):>2} | "
              f"sót {len(missed):>2}")

    adapter.close()

    total_should = sum(r["should_go"] for r in rows)
    total_matched = sum(r["matched"] for r in rows)
    total_hit = sum(r["hit"] for r in rows)
    total_collateral = sum(r["collateral"] for r in rows)
    total_missed = sum(r["missed"] for r in rows)

    recall = total_hit / total_should if total_should else 0.0
    precision = total_hit / total_matched if total_matched else 0.0

    print("\n" + "=" * 70)
    print("FORGET SCOPE — CẢ HAI CHIỀU")
    print("=" * 70)
    print(f"  scope_recall             {total_hit}/{total_should} = {recall:.3f}")
    print(f"  scope_precision          {total_hit}/{total_matched} = {precision:.3f}")
    print(f"  missed_deletion_rate     {total_missed}/{total_should} = "
          f"{1 - recall:.3f}")
    print(f"  collateral_deletion_rate {total_collateral}/{total_matched} = "
          f"{1 - precision:.3f}")
    print("\n  Một bản vá chỉ nâng recall mà hạ precision KHÔNG phải thành công.")

    print("\n" + "=" * 70)
    print("PHÂN BỐ ĐIỂM — ngưỡng chỉ đáng có nếu hai nhóm tách nhau")
    print("=" * 70)
    for label, values in (("nên xoá  ", positives), ("KHÔNG nên", negatives)):
        if not values:
            print(f"  {label}: (không có mẫu)")
            continue
        values = sorted(values)
        print(f"  {label}: n={len(values):<4} "
              f"min={values[0]:.3f} p25={values[len(values)//4]:.3f} "
              f"trung vị={statistics.median(values):.3f} "
              f"p75={values[3*len(values)//4]:.3f} max={values[-1]:.3f}")
    if positives and negatives:
        overlap = sum(1 for n in negatives if n >= min(positives))
        print(f"\n  {overlap}/{len(negatives)} mẫu KHÔNG nên xoá có điểm >= "
              f"điểm thấp nhất của nhóm nên xoá")
        if overlap:
            print("  -> hai nhóm CHỒNG NHAU. Không ngưỡng đơn nào tách được;")
            print("     một luật `best * hằng số` chỉ đang chọn chỗ để sai.")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed},
        "scope_recall": round(recall, 4),
        "scope_precision": round(precision, 4),
        "missed_deletion_rate": round(1 - recall, 4),
        "collateral_deletion_rate": round(1 - precision, 4),
        "requests": rows,
        "score_distribution": {"positive": positives, "negative": negatives},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
