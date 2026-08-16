"""Does strength decide who answers the question?

    A  legacy           top-k by total score
    B  relevance_first  top-k by query relevance, ordered by total score

One change, in one place: which memories enter the window. The score formula,
the admission floor, the query planner, the EVER operator, the lifecycle and
the grader are all untouched — this is the only variable.

The invariant under test, and the number that decides it:

    REINFORCEMENT != RELEVANCE
    sole_evidence_displacement_rate = 0

If exactly one memory in the store can answer a question, no amount of
repetition elsewhere may push it out of the top k. Measured, which is why this
exists: `reinforcement` adds up to 0.25 to a score, and 232 reconfirmations in
one lifetime run changed the returned context on eight of eight sampled
questions — including one where the only memory that answered fell out while
a repeated, unrelated one stayed.

`evidence_coverage@k` is reported at 1, 3 and 6 because the harm depends on
where the evidence lands. Evidence at rank 6 in a 6-slot window is one repeat
away from being gone.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.evals.lifetime_questions import (  # noqa: E402
    Family, Question, questions_at,
)
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip().lower()


def evidence_for(question: Question, memories: list[str]) -> list[int]:
    """Ranks at which a memory answering this question appears.

    A memory counts as evidence when it names the subject and carries the
    value the key expects. Crude and deliberately so: this measures whether
    the *right sentence* reached the window, not whether a model could use it.
    """
    if not question.value or not question.subject_name:
        return []
    value = _fold(question.value)
    parts = [_fold(p) for p in question.subject_name.split()]
    out = []
    for rank, memory in enumerate(memories, start=1):
        folded = _fold(memory)
        if value in folded and all(p in folded for p in parts):
            out.append(rank)
    return out


def run_arm(name: str, *, ranking: str, events, ledger, people, embedder,
            seed: int, per_family: int, workdir: Path) -> dict:
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    # The lifecycle is ON in both arms, and that is the point rather than a
    # detail. `reinforcement_count` is what this experiment neutralises, and
    # nothing increments it unless something is reconfirming claims.
    #
    # The first version of this script ran both arms with it off. Every memory
    # then carried reinforcement 0, strength was near-constant across
    # candidates, and ranking by total was ranking by relevance plus a
    # constant — identical windows on all 136 questions, `rank_churn 0.0000`,
    # and a clean-looking "no effect". There was no treatment to have an
    # effect. Same shape as the three 0.0000 lifecycle runs: the experiment
    # had not run, and it read like a result.
    adapter = CognitiveAdapter(workdir / f"{name}.db", embedder=embedder,
                               lifecycle_mode="safe")
    adapter.reset()
    adapter.memory_os.retrieval.ranking = ranking

    rng = random.Random(seed)
    rows = []
    fed = 0
    for checkpoint in CHECKPOINTS:
        while fed < len(events) and events[fed].tick <= checkpoint:
            event = events[fed]
            fed += 1
            if event.kind is EventKind.FORGET:
                adapter.forget(event.text)
            else:
                adapter.ingest(event)
        if embedder is not None:
            while backfill_embeddings(adapter.memory_os.memories.conn,
                                      embedder, limit=200):
                pass
            calibrate_with_probes(adapter.memory_os.memories.conn, embedder)

        for question in questions_at(ledger, people, checkpoint, rng=rng,
                                     per_family=per_family):
            result = adapter.query(question.text, checkpoint)
            rows.append({
                "family": question.family.value, "tick": question.tick,
                "question": question.text,
                "subject_id": question.subject_id,
                "attribute": question.attribute,
                "retrieved": result.retrieved,
                "evidence_ranks": evidence_for(question, result.retrieved),
                "prompt_chars": getattr(result, "prompt_chars", 0),
                "latency_ms": result.latency_ms,
                "route": result.route,
            })
    # The confound this experiment exists to neutralise, counted. If it never
    # varies, the arms are comparing nothing and the run must say so rather
    # than report a tidy zero.
    spread = adapter.memory_os.memories.conn.execute(
        "SELECT COUNT(DISTINCT COALESCE(reinforcement_count, 0)), "
        "MAX(COALESCE(reinforcement_count, 0)) FROM cognitive_memories"
    ).fetchone()
    adapter.close()
    return {"arm": name, "ranking": ranking, "rows": rows,
            "reinforcement_levels": int(spread[0]),
            "reinforcement_max": int(spread[1] or 0)}


def coverage(rows: list[dict], k: int) -> tuple[int, int]:
    """(questions with evidence in the first k, questions that have evidence
    anywhere in the store's answer).

    The denominator is questions where evidence exists at all in what was
    returned by *either* arm — computed by the caller, since a question no arm
    can answer measures nothing about ranking.
    """
    hit = sum(1 for r in rows if any(rank <= k for rank in r["evidence_ranks"]))
    return hit, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(prog="ranking_ab")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--out", default="benchmark_reports/ranking_ab.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.provenance import identity

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}")
    print("  Một thay đổi, một chỗ: ai lọt vào cửa sổ top-k. Công thức điểm,")
    print("  ngưỡng nhận, planner, toán tử EVER, lifecycle và bộ chấm đều "
          "không đụng.\n")

    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    embedder = Embedder()
    workdir = _REPO / ".staging" / "ranking_ab"
    workdir.mkdir(parents=True, exist_ok=True)

    arms = {}
    for name, ranking in (("A_legacy", "legacy"),
                          ("B_relevance", "relevance_first")):
        print(f"  đang chạy {name} …")
        arms[name] = run_arm(name, ranking=ranking, events=events,
                             ledger=ledger, people=people, embedder=embedder,
                             seed=args.seed, per_family=args.per_family,
                             workdir=workdir)

    a_rows, b_rows = arms["A_legacy"]["rows"], arms["B_relevance"]["rows"]
    if len(a_rows) != len(b_rows):
        print(f"\n  DỪNG: hai nhánh hỏi khác số câu ({len(a_rows)} vs "
              f"{len(b_rows)}) — không so được.")
        return 2

    # NO TREATMENT != NO EFFECT. The same invariant the lifecycle A/B earned,
    # applied to its own confound: if reinforcement never varies between
    # memories, ranking by total is ranking by relevance plus a constant, and
    # a difference of zero says nothing about the change under test.
    levels = arms["A_legacy"]["reinforcement_levels"]
    if levels < 2:
        print(f"\n  DỪNG: reinforcement_count chỉ có {levels} mức khác nhau "
              f"trong toàn kho.")
        print("  Không có gì để trung hoà, nên chênh lệch 0 không phải là "
              "'không có tác dụng' — là không có liều điều trị.")
        return 2
    print(f"\n  reinforcement: {levels} mức khác nhau, cao nhất "
          f"{arms['A_legacy']['reinforcement_max']} — có thứ để trung hoà.")

    # Only questions where evidence is reachable at all. A question no arm can
    # answer says nothing about ranking, and leaving it in dilutes both sides
    # by the same amount while making every rate look worse than it is.
    answerable = [i for i in range(len(a_rows))
                  if a_rows[i]["evidence_ranks"] or b_rows[i]["evidence_ranks"]]
    print("\n" + "=" * 70)
    print("EVIDENCE COVERAGE — bằng chứng có lọt vào cửa sổ không")
    print("=" * 70)
    print(f"  {len(answerable)}/{len(a_rows)} câu có bằng chứng lọt vào ít "
          f"nhất một nhánh. Chỉ tính trên số đó.\n")
    print(f"  {'':<14}{'@1':>10}{'@3':>10}{'@6':>10}")
    for name, rows in (("A_legacy", a_rows), ("B_relevance", b_rows)):
        subset = [rows[i] for i in answerable]
        line = f"  {name:<14}"
        for k in (1, 3, 6):
            hit, total = coverage(subset, k)
            line += f"{hit/total if total else 0:>10.4f}"
        print(line)

    # A displacement: evidence was in A's window and is not in B's, or the
    # reverse. Counted both directions so a "fix" that trades one loss for
    # another cannot report only its good half.
    lost = [i for i in answerable
            if a_rows[i]["evidence_ranks"] and not b_rows[i]["evidence_ranks"]]
    gained = [i for i in answerable
              if b_rows[i]["evidence_ranks"] and not a_rows[i]["evidence_ranks"]]
    sole_a = [i for i in answerable if len(a_rows[i]["evidence_ranks"]) == 1]
    sole_lost = [i for i in sole_a if not b_rows[i]["evidence_ranks"]]

    print("\n" + "=" * 70)
    print("DỊCH CHUYỂN BẰNG CHỨNG")
    print("=" * 70)
    print(f"  A có, B mất  : {len(lost)}")
    print(f"  B có, A mất  : {len(gained)}")
    print(f"  ca chỉ có MỘT bằng chứng trong A: {len(sole_a)}")
    print(f"  trong đó B đánh rơi              : {len(sole_lost)}")
    rate = len(sole_lost) / len(sole_a) if sole_a else 0.0
    print(f"  sole_evidence_displacement_rate  : {rate:.4f}"
          f"{'  ✓ MỤC TIÊU 0' if rate == 0 else '  ⚠ MỤC TIÊU 0'}")
    for i in lost[:5]:
        print(f"\n    MẤT: {a_rows[i]['question']}")
        for m in a_rows[i]["retrieved"][:1]:
            print(f"      A hạng {a_rows[i]['evidence_ranks']}: {m[:80]}")

    churn = sum(1 for i in range(len(a_rows))
                if a_rows[i]["retrieved"] != b_rows[i]["retrieved"])
    print("\n" + "=" * 70)
    print("XÁO TRỘN VÀ CHI PHÍ")
    print("=" * 70)
    print(f"  rank_churn (ngữ cảnh khác nhau)  : {churn}/{len(a_rows)} = "
          f"{churn/len(a_rows):.4f}")
    for name, rows in (("A_legacy", a_rows), ("B_relevance", b_rows)):
        chars = sum(r["prompt_chars"] for r in rows)
        p50 = sorted(r["latency_ms"] for r in rows)[len(rows) // 2]
        print(f"  {name:<14} ký tự prompt {chars:>8,}   p50 {p50:>7.1f} ms")

    by_family = Counter()
    for i in lost:
        by_family[a_rows[i]["family"]] += 1
    if by_family:
        print(f"\n  mất bằng chứng theo nhóm: {dict(by_family)}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(),
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed, "per_family": args.per_family},
        "answerable": len(answerable), "asked": len(a_rows),
        "coverage": {name: {str(k): coverage([rows[i] for i in answerable], k)
                            for k in (1, 3, 6)}
                     for name, rows in (("A_legacy", a_rows),
                                        ("B_relevance", b_rows))},
        "evidence_lost": len(lost), "evidence_gained": len(gained),
        "sole_evidence_cases": len(sole_a),
        "sole_evidence_displacement_rate": round(rate, 4),
        "rank_churn": churn,
        "lost_questions": [a_rows[i]["question"] for i in lost],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
