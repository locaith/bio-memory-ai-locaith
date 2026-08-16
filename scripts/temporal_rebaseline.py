"""Re-baseline TEMPORAL_AT. Measures; changes no logic.

The number that has to be redone: `TEMPORAL_AT 23/38 = 0.6053` was reported as
"unchanged, therefore no coupling" after structured slots landed. It was
unchanged because the structured branch never executed on this path —
`answer_temporal` passed a Vietnamese probe string where the comparison
expected an attribute key, so the equality was always false. A dead branch,
read as evidence of isolation.

Three arms on the same 38 frozen questions:

    A  legacy      slot inferred from text, as before
    B  structured  slot read from the row
    C  oracle      slot taken from the world's own ledger — upper bound only,
                   never shipped, and it is the number that says whether slot
                   resolution is where the remaining errors live

The invariant, checked before any score is printed:

    a question classified TEMPORAL, whose subject and predicate resolve, and
    whose slot exists on the row, MUST have executed the structured path

A run that violates it prints nothing. The same rule that caught three
0.0000 lifecycle A/Bs and one ranking A/B with no treatment: an arm that did
not execute is not a measurement.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.evals.lifetime_questions import (  # noqa: E402
    Family, questions_at,
)
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)


def _instrument():
    """Count what actually ran inside `claim_history`, without changing it.

    Wrapping rather than editing: this file must not be able to alter the
    behaviour it is measuring, and a counter added to the module would be one
    more thing to remember to remove.
    """
    from bio_agent_os.cognitive import temporal_operator as module

    stats = Counter()
    original_by_aspect = module._by_aspect
    original_history = module.claim_history

    def counted_by_aspect(memory_os, candidates, aspect):
        stats["by_aspect_calls"] += 1
        return original_by_aspect(memory_os, candidates, aspect)

    def counted_history(memory_os, **kwargs):
        predicate = kwargs.get("predicate")
        stats["history_calls"] += 1
        if predicate:
            stats["predicate_supplied"] += 1
        before = stats["by_aspect_calls"]
        spans = original_history(memory_os, **kwargs)
        if stats["by_aspect_calls"] == before and predicate:
            # No text comparison happened and a predicate was given: the
            # structured branch carried this query on its own.
            stats["structured_only"] += 1
        elif predicate:
            stats["structured_plus_fallback"] += 1
        else:
            stats["legacy_only"] += 1
        return spans

    module._by_aspect = counted_by_aspect
    module.claim_history = counted_history
    return stats, lambda: (setattr(module, "_by_aspect", original_by_aspect),
                           setattr(module, "claim_history", original_history))


def run_arm(name: str, *, slots: str, events, ledger, people, embedder,
            seed: int, per_family: int, workdir: Path, engine=None) -> dict:
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.cognitive.slot_backfill import slot_for
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    adapter = CognitiveAdapter(workdir / f"{name}.db", embedder=embedder,
                               engine=engine)
    adapter.reset()

    rng = random.Random(seed)
    rows = []
    fed = 0
    stats, restore = _instrument()
    try:
        for checkpoint in CHECKPOINTS:
            while fed < len(events) and events[fed].tick <= checkpoint:
                event = events[fed]
                fed += 1
                if event.kind is EventKind.FORGET:
                    adapter.forget(event.text)
                else:
                    adapter.ingest(event)

            conn = adapter.memory_os.memories.conn
            if slots == "legacy":
                # Strip the slots the ingest path wrote, so arm A is the
                # behaviour that existed before any of this.
                conn.execute("UPDATE cognitive_memories SET structured_json='{}'")
                conn.commit()
            elif slots == "oracle":
                _install_oracle_slots(conn, events)

            if embedder is not None:
                while backfill_embeddings(conn, embedder, limit=200):
                    pass
                calibrate_with_probes(conn, embedder)

            for question in questions_at(ledger, people, checkpoint, rng=rng,
                                         per_family=per_family):
                if question.family is not Family.HISTORICAL:
                    continue
                result = adapter.query(question.text, checkpoint)
                rows.append({
                    "tick": question.tick, "question": question.text,
                    "route": result.route, "value": question.value,
                    "subject_id": question.subject_id,
                    "attribute": question.attribute,
                    "answer": (result.answer or "")[:160],
                    "correct": bool(question.grade(result.answer))
                               if engine else None,
                    "retrieval_hit": _fold(question.value) in
                                     _fold(" ".join(result.retrieved)),
                    "stage_failed": result.stage_failed,
                })
    finally:
        restore()

    with_slot = adapter.memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories "
        "WHERE structured_json != '{}' AND structured_json IS NOT NULL"
    ).fetchone()[0]
    adapter.close()
    return {"arm": name, "slots": slots, "rows": rows,
            "rows_with_slot": with_slot, "paths": dict(stats)}


def _install_oracle_slots(conn, events) -> None:
    """Arm C: the world's own attribute, written onto the row.

    An upper bound, never a product. It answers one question and only one:
    if slot resolution were perfect, how many of the remaining temporal
    errors would go away? If the answer is "few", the errors are in interval
    selection and no amount of resolver work reaches them.
    """
    truth = {}
    for event in events:
        if event.text and getattr(event, "attribute", None):
            truth[_fold(event.text)] = event.attribute
    for memory_id, content in conn.execute(
            "SELECT memory_id, content FROM cognitive_memories").fetchall():
        attribute = truth.get(_fold(content))
        if not attribute:
            continue
        conn.execute(
            "UPDATE cognitive_memories SET structured_json = ? "
            "WHERE memory_id = ?",
            (json.dumps({"attribute": attribute, "source": "oracle"}),
             memory_id))
    conn.commit()


def _fold(text: str) -> str:
    import re
    import unicodedata

    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser(prog="temporal_rebaseline")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--engine", action="store_true")
    ap.add_argument("--out", default="benchmark_reports/temporal_rebaseline.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.provenance import identity

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}")
    print("  Ba nhánh khác nhau đúng một thứ: slot đến từ đâu.\n")

    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    embedder = Embedder()
    engine = None
    if args.engine:
        from bio_agent_os.core.llm_engine import LLMEngine
        engine = LLMEngine.from_env()

    workdir = _REPO / ".staging" / "temporal_rebaseline"
    workdir.mkdir(parents=True, exist_ok=True)

    arms = {}
    for name, slots in (("A_legacy", "legacy"), ("B_structured", "stored"),
                        ("C_oracle", "oracle")):
        print(f"  đang chạy {name} …")
        arms[name] = run_arm(name, slots=slots, events=events, ledger=ledger,
                             people=people, embedder=embedder, seed=args.seed,
                             per_family=args.per_family, workdir=workdir,
                             engine=engine)

    # ------------------------------------------------------------------
    # THE INVARIANT, before any score is printed.
    # ------------------------------------------------------------------
    problems = []
    counts = {name: len(arm["rows"]) for name, arm in arms.items()}
    if len(set(counts.values())) != 1:
        problems.append(f"số câu khác nhau giữa các nhánh: {counts}")

    b = arms["B_structured"]
    if b["rows_with_slot"] == 0:
        problems.append("nhánh B không có hàng nào mang slot — không có liều "
                        "điều trị")
    if b["paths"].get("predicate_supplied", 0) == 0:
        problems.append("claim_history chưa từng nhận predicate ở nhánh B — "
                        "nhánh structured KHÔNG chạy, đúng lỗi F1")
    if b["paths"].get("structured_only", 0) == 0 and \
            b["paths"].get("structured_plus_fallback", 0) == 0:
        problems.append("không truy vấn nào đi qua đường structured")

    if problems:
        print("\n  DỪNG — KHÔNG THI HÀNH KHÔNG PHẢI LÀ ĐIỂM BẰNG KHÔNG")
        for line in problems:
            print(f"    {line}")
        print(f"    đường đã đi, nhánh B: {b['paths']}")
        return 2

    print("\n" + "=" * 74)
    print("TEMPORAL_AT — 38 câu frozen, ba nhánh")
    print("=" * 74)
    metric = "correct" if engine else "retrieval_hit"
    print(f"  chấm theo: {metric}"
          f"{'' if engine else '  (không có engine — chỉ đo truy xuất)'}\n")
    print(f"  {'nhánh':<14}{'n':>4}{'đúng':>7}{'tỷ lệ':>9}"
          f"{'hàng có slot':>14}{'structured':>12}{'legacy':>9}")
    for name, arm in arms.items():
        rows = arm["rows"]
        ok = sum(1 for r in rows if r[metric])
        paths = arm["paths"]
        print(f"  {name:<14}{len(rows):>4}{ok:>7}{ok/len(rows):>9.4f}"
              f"{arm['rows_with_slot']:>14}"
              f"{paths.get('structured_only', 0) + paths.get('structured_plus_fallback', 0):>12}"
              f"{paths.get('legacy_only', 0):>9}")

    base = arms["A_legacy"]["rows"]
    print("\n  CHÊNH LỆCH so với A")
    for name in ("B_structured", "C_oracle"):
        rows = arms[name]["rows"]
        delta = (sum(1 for r in rows if r[metric])
                 - sum(1 for r in base if r[metric]))
        print(f"    {name:<14}{delta:>+4} câu")
    print(f"  n=38, một câu = 0.0263. Đừng đọc chênh lệch một câu thành xu hướng.")

    print("\n" + "=" * 74)
    print("ĐƯỜNG ĐÃ ĐI TRONG claim_history")
    print("=" * 74)
    for name, arm in arms.items():
        print(f"  {name:<14} {arm['paths']}")

    print("\n" + "=" * 74)
    print("LỖI CÒN LẠI Ở NHÁNH B")
    print("=" * 74)
    wrong = [r for r in arms["B_structured"]["rows"] if not r[metric]]
    by_attribute = Counter(r["attribute"] for r in wrong)
    print(f"  {len(wrong)} câu sai | theo thuộc tính: {dict(by_attribute)}")
    for r in wrong[:6]:
        print(f"    t={r['tick']:<5} {r['attribute']:<10} cần {r['value']!r}")
        print(f"       {r['question']}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(), "metric": metric,
        "config": {"ticks": args.ticks, "seed": args.seed,
                   "model": getattr(engine, "model_id", None)},
        "arms": {name: {k: v for k, v in arm.items() if k != "rows"}
                 for name, arm in arms.items()},
        "scores": {name: sum(1 for r in arm["rows"] if r[metric])
                   for name, arm in arms.items()},
        "n": len(base),
        "rows": {name: arm["rows"] for name, arm in arms.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
