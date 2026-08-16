"""What is Bio-Memory missing — which *operation*, not which Python file.

The system stopped being a retrieval engine somewhere around the EVER
operator. Once a question is answered by executing a defined operation over a
claim history, "which module has bugs" is the wrong question: the bottleneck
shows up as an intent with no operator, or an operator that declines, long
before it shows up as a file.

So every failure is placed on four axes:

    failure_class      what went wrong, in the user's terms
    query_operator     which operation ran, or that none exists
    first_wrong_stage  the earliest point the answer was already lost
    severity           what it costs when it happens

and one table is printed beside them:

    operator_coverage  how many questions each operation claimed, and how
                       many of those it got right

`operator_missing` is the row that matters most. It means a question shape the
system recognises and answers by ranking the whole store and hoping — the
state EVER was in until yesterday, when it scored 0.4750 and the operator
took it to 0.9750 without calling a model.

**Nothing is fixed here.** This measures; the decision about what to build
comes after, from what it says.
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
    Expect, Family, Question, questions_at,
)
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)

#: The route a plan reports, mapped to the operation it performs.
#:
#: `recall` is deliberately named as an absence rather than an operator. It
#: ranks the whole store by similarity — a fallback that answers many things
#: adequately and nothing exactly.
OPERATORS = {
    "ever_operator": "EXISTS",
    "temporal_operator": "TEMPORAL_AT",
    "select_by_class": "CLASS_SCAN",
    "recall": "RECALL (không có toán tử)",
    "recall_after_ever_failed": "EXISTS → RECALL (toán tử từ chối)",
    "recall_after_temporal_failed": "TEMPORAL_AT → RECALL (toán tử từ chối)",
}

#: What each family is really asking the memory to compute.
INTENT = {
    Family.CURRENT: "CURRENT_STATE",
    Family.HISTORICAL: "TEMPORAL_AT",
    Family.EVER: "EXISTS",
    Family.FORGOTTEN: "CURRENT_STATE sau khi xoá",
}


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip().lower()


def _evidence_present(question: Question, retrieved: list[str]) -> bool:
    """Was the answer in what the store handed over?

    The line between "the store could not find it" and "the store found it and
    the answer was still wrong" — which are two different repairs.
    """
    if not question.value:
        return False
    return _fold(question.value) in _fold(" ".join(retrieved))


def classify_failure(question: Question, route: str, retrieved: list[str],
                     answer: str, stage_failed: str,
                     leaked: bool) -> tuple[str, str, str]:
    """(failure_class, first_wrong_stage, severity)."""
    operator = OPERATORS.get(route, route)

    if leaked:
        # Reciting something the user asked to have deleted. Nothing else on
        # this list costs as much, and the cost is not recoverable by a
        # follow-up question.
        return "privacy_leak", "retrieval_returned_deleted", "critical"

    if route.endswith("_failed"):
        return (f"{question.family.value}_wrong",
                f"operator_declined:{stage_failed or 'unknown'}", "high")

    if operator.startswith("RECALL"):
        # The system recognised the question and had no operation for it, so
        # it ranked the store. This is the row that names what to build.
        return (f"{question.family.value}_wrong", "operator_missing", "high")

    if not _evidence_present(question, retrieved):
        return (f"{question.family.value}_wrong",
                f"{route}_selection", "high")

    # The operator ran, the evidence was in the window, and the answer is
    # still wrong. Only here does the model layer come into it.
    return (f"{question.family.value}_wrong", "answer_synthesis", "medium")


def main() -> int:
    ap = argparse.ArgumentParser(prog="failure_matrix")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--engine", action="store_true", default=True)
    ap.add_argument("--lifecycle", default="safe")
    ap.add_argument("--out", default="benchmark_reports/failure_matrix.json")
    args = ap.parse_args()

    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.llm_engine import LLMEngine
    from bio_agent_os.core.provenance import identity
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}")
    print(f"  lifecycle={args.lifecycle}   KHÔNG sửa gì trong lúc đo.\n")

    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    embedder = Embedder()
    engine = LLMEngine.from_env() if args.engine else None
    workdir = _REPO / ".staging" / "failure_matrix"
    workdir.mkdir(parents=True, exist_ok=True)

    adapter = CognitiveAdapter(workdir / "run.db", embedder=embedder,
                               engine=engine, lifecycle_mode=args.lifecycle)
    adapter.reset()

    rng = random.Random(args.seed)
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
                                     per_family=args.per_family):
            result = adapter.query(question.text, checkpoint)
            correct = question.grade(result.answer)
            leaked = question.leaks_in_answer(result.answer)
            row = {
                "family": question.family.value,
                "intent": INTENT.get(question.family, "?"),
                "tick": question.tick,
                "question": question.text,
                "route": result.route,
                "operator": OPERATORS.get(result.route, result.route),
                "expect": question.expect.value,
                "value": question.value,
                "answer": (result.answer or "")[:160],
                "correct": bool(correct),
                "evidence_present": _evidence_present(question,
                                                      result.retrieved),
            }
            if not correct:
                cls, stage, severity = classify_failure(
                    question, result.route, result.retrieved, result.answer,
                    result.stage_failed, leaked)
                row.update(failure_class=cls, first_wrong_stage=stage,
                           severity=severity)
            rows.append(row)
    adapter.close()

    total = len(rows)
    right = sum(1 for r in rows if r["correct"])
    failures = [r for r in rows if not r["correct"]]

    print("=" * 74)
    print(f"ĐỘ PHỦ THEO TOÁN TỬ — {right}/{total} = {right/total:.4f}")
    print("=" * 74)
    by_op: dict[str, list] = {}
    for row in rows:
        by_op.setdefault(row["operator"], []).append(row)
    print(f"  {'toán tử':<38}{'hỏi':>6}{'đúng':>7}{'tỷ lệ':>9}")
    for operator, group in sorted(by_op.items(),
                                  key=lambda kv: -len(kv[1])):
        ok = sum(1 for r in group if r["correct"])
        print(f"  {operator:<38}{len(group):>6}{ok:>7}"
              f"{ok/len(group):>9.4f}")

    print("\n" + "=" * 74)
    print("Ý ĐỊNH ↔ TOÁN TỬ — chỗ nào câu hỏi không có phép toán của nó")
    print("=" * 74)
    pairs = Counter((r["intent"], r["operator"]) for r in rows)
    print(f"  {'ý định':<26}{'toán tử đã chạy':<40}{'n':>5}")
    for (intent, operator), n in sorted(pairs.items(),
                                        key=lambda kv: (kv[0][0], -kv[1])):
        mark = "  ⚠" if operator.startswith("RECALL") else ""
        print(f"  {intent:<26}{operator:<40}{n:>5}{mark}")

    print("\n" + "=" * 74)
    print(f"MA TRẬN LỖI BỐN CHIỀU — {len(failures)} lỗi")
    print("=" * 74)
    print(f"  {'failure_class':<22}{'query_operator':<32}"
          f"{'first_wrong_stage':<30}{'sev':<9}{'n':>4}")
    matrix = Counter(
        (r["failure_class"], r["operator"], r["first_wrong_stage"],
         r["severity"]) for r in failures)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for (cls, operator, stage, severity), n in sorted(
            matrix.items(), key=lambda kv: (order.get(kv[0][3], 9), -kv[1])):
        print(f"  {cls:<22}{operator:<32}{stage:<30}{severity:<9}{n:>4}")

    print("\n" + "=" * 74)
    print("GOM NHÓM — đây là thứ quyết định bước sau")
    print("=" * 74)
    by_stage = Counter(r["first_wrong_stage"] for r in failures)
    by_severity = Counter(r["severity"] for r in failures)
    by_operator = Counter(r["operator"] for r in failures)
    print(f"  theo giai đoạn hỏng đầu tiên : {dict(by_stage)}")
    print(f"  theo mức nghiêm trọng        : {dict(by_severity)}")
    print(f"  theo toán tử                 : {dict(by_operator)}")

    biggest = by_stage.most_common(1)[0] if by_stage else ("", 0)
    share = biggest[1] / len(failures) if failures else 0.0
    print(f"\n  nhóm lớn nhất: {biggest[0]} — {biggest[1]}/{len(failures)} "
          f"= {share:.2%}")
    if share < 0.35:
        print("  Không nhóm nào chiếm ưu thế. Theo luật đã chốt: dừng làm "
              "feature, chuyển sang corpus lịch sử / compaction.")
    elif biggest[0] == "operator_missing":
        print("  Lỗi tập trung vào một ý định CHƯA CÓ phép toán. Theo luật đã "
              "chốt: thiết kế toán tử mới cho ý định đó.")
    elif biggest[0] == "answer_synthesis":
        print("  Toán tử đúng, bằng chứng có mặt, câu trả lời vẫn sai. Theo "
              "luật đã chốt: giờ mới nhìn tới tầng tổng hợp/model.")
    else:
        print(f"  Lỗi tập trung vào một toán tử ĐÃ CÓ ({biggest[0]}). Theo "
              f"luật đã chốt: sửa chính toán tử đó.")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(),
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed, "per_family": args.per_family,
                   "lifecycle": args.lifecycle,
                   "model": getattr(engine, "model_id", None)},
        "accuracy": round(right / total, 4), "asked": total,
        "operator_coverage": {
            operator: {"asked": len(group),
                       "correct": sum(1 for r in group if r["correct"])}
            for operator, group in by_op.items()},
        "matrix": [{"failure_class": c, "query_operator": o,
                    "first_wrong_stage": s, "severity": v, "n": n}
                   for (c, o, s, v), n in matrix.items()],
        "by_stage": dict(by_stage), "by_severity": dict(by_severity),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
