"""One question: after #2, #3 and #6, where did behaviour change?

Not "did the score go up". Three changes landed on the path every question
travels — the epistemic fallback, the name extraction, and a registry that
touches nothing — and `_names` in particular now reads a different subject
out of every lowercased sentence. So a number that moved is a number that
needs a *label* before it needs a celebration.

Every delta is classified, and there is no unlabelled bucket:

    INTENDED_PRODUCT_DELTA   the change was made to do this
    EXPECTED_COUPLING        a known consequence of a change made elsewhere
    UNEXPECTED_COUPLING      investigate before claiming anything
    MEASUREMENT_DELTA        the grader moved, not the system
    RUNTIME_DELTA            a different build or config produced it
    NO_DELTA

The "before" arm is the frozen report from the run that produced the
baseline, not a rebuild of the old code. Rebuilding it would mean trusting
that the rebuild is faithful; the report is what was actually measured.

Privacy is reported in two layers on purpose, because they are two claims and
only one of them is finished:

    DISCOVERY    all persistent stores − registry = ∅
    ENFORCEMENT  stores holding content − stores a verifier scans
                 = the exceptions, named

"Privacy perimeter complete" would be false. "Store discovery complete,
enforcement gaps enumerated" is what the data says.
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

from bio_agent_os.evals.lifetime_questions import questions_at  # noqa: E402
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)
BASELINE = "benchmark_reports/failure_matrix_slots.json"

#: What the frozen run recorded, by family. Stated here so a drift in the
#: report file itself is visible rather than silently adopted.
EXPECTED = {"ever": (40, 40), "current": (34, 40),
            "historical": (23, 38), "forgotten": (15, 18)}


#: The verified baselines, and where each was measured. A gate that compares
#: against a number nobody can point at is a gate that will one day compare
#: against a dead branch — which is what happened: `failure_matrix_slots.json`
#: records historical 23/38, measured before `answer_temporal` passed a
#: predicate to `claim_history`. The structured branch could not fire, and a
#: dead path scored beautifully stable.
#: CORRECTION. The first version recorded `current: 49/58`, which is the
#: `state_at` *operator* total — CURRENT_STATE and FORGOTTEN both route
#: through it, 36/40 + 13/18 = 49/58. Written as a family baseline it made
#: every comparison disagree with itself, and the gate reported three
#: "baseline mismatches" that were an arithmetic error of mine, not drift.
#:
#: A number is only a baseline if it says what it counted.
VERIFIED_BASELINE = {
    "ever": (40, 40, "failure_matrix_slots.json — EXISTS operator"),
    "current": (36, 40, "failure_matrix_slots.json — CURRENT family"),
    "historical": (31, 38, "temporal_rebaseline.json B_structured"),
    "forgotten": (13, 18, "failure_matrix_slots.json — FORGOTTEN family"),
}

#: The operator totals, kept separately so neither can be mistaken for the
#: other again.
VERIFIED_OPERATOR = {
    "EXISTS": (40, 40),
    "state_at": (49, 58),      # current 36/40 + forgotten 13/18
    "TEMPORAL_AT": (31, 38),
}

#: Candidate, not baseline. Promoted only by a passing gate.
CANDIDATE = {"historical": (33, 38, "conditional rescue, e9ab0f4")}


def _instrument():
    """Count which path `claim_history` actually took.

    Wrapped rather than edited: a gate must not be able to change what it
    measures. This is the same counter `temporal_rebaseline` uses, and it is
    what turns "historical did not move" into either "nothing changed" or
    "the structured branch never ran".
    """
    from bio_agent_os.cognitive import temporal_operator as module

    stats = Counter()
    original_by_aspect = module._by_aspect
    original_history = module.claim_history

    def counted_by_aspect(memory_os, candidates, aspect):
        stats["by_aspect_calls"] += 1
        return original_by_aspect(memory_os, candidates, aspect)

    def counted_history(memory_os, **kwargs):
        stats["history_calls"] += 1
        if kwargs.get("predicate"):
            stats["predicate_supplied"] += 1
        before = stats["by_aspect_calls"]
        spans = original_history(memory_os, **kwargs)
        if stats["by_aspect_calls"] == before and kwargs.get("predicate"):
            stats["structured_only"] += 1
        return spans

    module._by_aspect = counted_by_aspect
    module.claim_history = counted_history
    return stats, lambda: (setattr(module, "_by_aspect", original_by_aspect),
                           setattr(module, "claim_history", original_history))


def run_now(events, ledger, people, embedder, engine, seed, per_family,
            workdir: Path) -> dict:
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.cognitive.aspect_resolver import resolve_frame
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    adapter = CognitiveAdapter(workdir / "run.db", embedder=embedder,
                               engine=engine, lifecycle_mode="safe")
    adapter.reset()
    rng = random.Random(seed)
    rows = {}
    fed = 0
    stats, restore = _instrument()
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
            frame = resolve_frame(question.text)
            rows[f"{checkpoint}|{question.text}"] = {
                "family": question.family.value,
                "question": question.text,
                "subject": frame.subject or "",
                "predicate": frame.predicate.attribute or "",
                "route": result.route,
                "candidate_count": len(result.retrieved),
                "selected": [r[:60] for r in result.retrieved[:3]],
                "answer": (result.answer or "")[:120],
                "correct": bool(question.grade(result.answer)),
            }
    restore()
    adapter.close()
    return rows, dict(stats)


def classify(before: dict, after: dict) -> str:
    """Every delta gets a label — from a field the baseline actually has.

    The first version compared `before.get("subject")` against the current
    one. The baseline report has no `subject` field, so that read `None` for
    every row, the "subject changed" branch caught all 136, and the whole run
    reported `EXPECTED_COUPLING 136`. An unlabelled bucket would have been
    obvious; a *falsely* labelled one looked like a clean answer.

    So a field the baseline does not record cannot be used to explain a
    delta. What both sides do have is `route` and `correct`.
    """
    comparable = {"route", "correct"} & set(before) & set(after)
    if "correct" not in comparable:
        return "MEASUREMENT_DELTA"

    same_route = ("route" not in comparable
                  or before["route"] == after["route"])
    if before["correct"] == after["correct"] and same_route:
        return "NO_DELTA"
    if not same_route:
        # A question that changed operator changed for a structural reason,
        # and the route names it. Still a coupling to explain, not a result.
        return "EXPECTED_COUPLING"
    # Same route, different answer. Nothing in the route explains it, so it
    # is the class that has to be investigated before anything is claimed.
    return "UNEXPECTED_COUPLING"


def main() -> int:
    ap = argparse.ArgumentParser(prog="integration_gate")
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--baseline", default=BASELINE)
    ap.add_argument("--out", default="benchmark_reports/integration_gate.json")
    args = ap.parse_args()

    from bio_agent_os.cognitive import privacy_registry
    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.llm_engine import LLMEngine
    from bio_agent_os.core.provenance import identity

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}\n")

    baseline_path = _REPO / args.baseline
    if not baseline_path.exists():
        print(f"  DỪNG: không có baseline {args.baseline}")
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    before = {f"{r['tick']}|{r['question']}": r for r in baseline["rows"]}

    # The historical rows in `failure_matrix_slots.json` were measured before
    # `answer_temporal` passed a predicate, so they record the dead path at
    # 23/38. The verified checkpoint for that family lives in a different
    # report, and a baseline is per-family — one file is not automatically
    # the baseline for everything it happens to contain.
    temporal_path = _REPO / "benchmark_reports" / "temporal_rebaseline.json"
    if temporal_path.exists():
        temporal = json.loads(temporal_path.read_text(encoding="utf-8"))
        arm = temporal.get("rows", {}).get("B_structured", [])
        for row in arm:
            key = f"{row['tick']}|{row['question']}"
            before[key] = {"family": "historical", "question": row["question"],
                           "route": "temporal_operator",
                           "correct": bool(row.get("correct"))}

    events, ledger, people = generate(ticks=1000, subjects=20, seed=args.seed)
    workdir = _REPO / ".staging" / "integration_gate"
    workdir.mkdir(parents=True, exist_ok=True)
    after, paths = run_now(events, ledger, people, Embedder(),
                           LLMEngine.from_env(), args.seed, args.per_family,
                           workdir)

    # ------------------------------------------------------------------
    # WRONG BASELINE != PRODUCT REGRESSION
    #
    # The gate validates its own footing before printing a comparison. The
    # first run compared historical against 23/38 — a number measured before
    # `answer_temporal` passed a predicate, when the structured branch could
    # not fire at all. A dead path is beautifully stable, and "+0, no
    # coupling" was the conclusion it invited.
    # ------------------------------------------------------------------
    families = ("ever", "current", "historical", "forgotten")
    invalid = []
    for family in families:
        recorded = sum(1 for r in before.values()
                       if r["family"] == family and r["correct"])
        verified, total, source = VERIFIED_BASELINE[family]
        if recorded != verified:
            invalid.append(
                f"{family}: baseline trong {args.baseline} là {recorded}/{total}, "
                f"nhưng số đã xác minh là {verified}/{total} ({source})")

    # EXECUTION CONTRACT for TEMPORAL_AT.
    #
    # `by_aspect_calls == 0` is deliberately NOT required. A rescue that
    # earns its place is a legitimate cosine call, and an absolute zero would
    # become a wrong invariant the first time one fires. What is required is
    # that every such call had a reason:
    #
    #     fallback requires a STRUCTURED_GAP
    #
    # `unnecessary_fallback` is that rule as a number, and it is the one that
    # catches `376c4ce`.
    from bio_agent_os.cognitive.temporal_operator import execution_report

    execution = execution_report()
    historical_n = sum(1 for r in after.values()
                       if r["family"] == "historical")
    if execution["structured_branch_taken"] < historical_n:
        invalid.append(
            f"structured_branch_taken {execution['structured_branch_taken']}/"
            f"{historical_n} — nhánh structured chưa thực sự thi hành")
    if execution["unnecessary_fallback"] > 0:
        invalid.append(
            f"unnecessary_fallback {execution['unnecessary_fallback']} — "
            f"cosine chạy dù bằng chứng structured đã đủ")
    if execution["by_aspect_calls"] > execution["fallback_attempted"]:
        invalid.append(
            f"_by_aspect gọi {execution['by_aspect_calls']} lần nhưng chỉ "
            f"{execution['fallback_attempted']} lần có STRUCTURED_GAP — "
            f"fallback không có lý do")

    if invalid:
        print("=" * 74)
        print("GATE INVALID — KHÔNG IN BẢNG HỒI QUY")
        print("=" * 74)
        for line in invalid:
            print(f"  ⚠ {line}")
        print(f"\n  đường đã đi: {paths}")
        print("\n  WRONG BASELINE != PRODUCT REGRESSION.")
        print("  Một chân của phép so sánh đứng trên artifact cũ hoặc trên "
              "một nhánh chưa chạy. So sánh với nó không nói được gì về sản "
              "phẩm.")
        out = _REPO / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "gate_valid": False, "reasons": invalid, "paths": paths,
            "runtime": who.as_dict(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  -> {out}")
        return 2

    print("=" * 74)
    print("ĐIỂM THEO NHÓM — trước / sau")
    print("=" * 74)
    print(f"  {'nhóm':<14}{'trước':>12}{'sau':>12}{'Δ':>6}")
    deltas = {}
    for family in families:
        was = sum(1 for r in before.values()
                  if r["family"] == family and r["correct"])
        now = sum(1 for r in after.values()
                  if r["family"] == family and r["correct"])
        total = sum(1 for r in after.values() if r["family"] == family)
        deltas[family] = now - was
        flag = "" if now == was else ("  ⚠" if family == "ever" else "")
        print(f"  {family:<14}{was:>8}/{EXPECTED[family][1]:<3}"
              f"{now:>8}/{total:<3}{now - was:>+6}{flag}")

    print("\n" + "=" * 74)
    print("PHÂN LOẠI TỪNG THAY ĐỔI — không có ô nào không nhãn")
    print("=" * 74)
    labels = Counter()
    changed = []
    for key, now in after.items():
        was = before.get(key)
        if was is None:
            labels["MEASUREMENT_DELTA"] += 1
            continue
        label = classify(was, now)
        labels[label] += 1
        if label != "NO_DELTA":
            changed.append((key, was, now, label))
    for label, count in labels.most_common():
        print(f"  {label:<24}{count:>5}")

    print("\n  ROUTE DIFF — những câu đã đổi")
    for key, was, now, label in changed[:12]:
        print(f"\n    [{label}] {now['question']}")
        if was.get("subject") != now.get("subject"):
            print(f"      subject   {was.get('subject')!r} -> "
                  f"{now.get('subject')!r}")
        if was["route"] != now["route"]:
            print(f"      route     {was['route']} -> {now['route']}")
        if was["correct"] != now["correct"]:
            print(f"      đúng      {was['correct']} -> {now['correct']}")

    print("\n" + "=" * 74)
    print("BẤT BIẾN")
    print("=" * 74)
    from bio_agent_os.cognitive.facade import MemoryOS

    probe = MemoryOS(workdir / "probe.db")
    try:
        undeclared = privacy_registry.undeclared(probe.memories.conn)
    finally:
        probe.close()
    unscanned = privacy_registry.unscanned_content_stores()
    print(f"  privacy DISCOVERY   : {len(undeclared)} kho chưa khai báo"
          f"{'  ✓' if not undeclared else '  ⚠ ' + str(sorted(undeclared))}")
    print(f"  privacy ENFORCEMENT : {len(unscanned)} kho giữ nội dung ngoài "
          f"verifier — {[p.store_name for p in unscanned]}")
    print("  → 'store discovery perimeter complete; enforcement gaps "
          "enumerated', KHÔNG phải 'privacy perimeter complete'.")

    leaked = sum(1 for r in after.values()
                 if r["family"] == "forgotten" and not r["correct"])
    print(f"  câu forgotten sai   : {leaked}")

    # Labelled for what it counts. `claim_history` is called by EXISTS and by
    # `state_at` as well as by the temporal operator, so 133 is every call in
    # a 136-question gate — not 133 temporal queries against 38 that exist.
    # A number that invites its own author to re-excavate the scene in three
    # weeks is a badly named number.
    print("\n  ĐƯỜNG CHẠY — mọi lời gọi claim_history trong gate")
    for key, value in sorted(execution.items()):
        label = key
        if key in ("structured_branch_taken", "structured_queries",
                   "fallback_attempted", "by_aspect_calls"):
            label = f"{key}_total"
        print(f"    {label:<32}{value}")
    print(f"    {'historical_queries':<32}{historical_n}"
          f"   (bộ frozen TEMPORAL_AT)")
    # N/A rather than an inferred zero. With nothing rescued there is nothing
    # to have changed, and writing 0 would be a measurement nobody took.
    answer_effect = ("N/A — không có bằng chứng nào được cứu"
                     if execution["fallback_contributed"] == 0
                     else "phải đo bằng counterfactual, chưa cài")
    print(f"    {'fallback_changed_answer':<32}{answer_effect}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(),
        "baseline": args.baseline,
        "scores": {f: {"before": sum(1 for r in before.values()
                                     if r["family"] == f and r["correct"]),
                       "after": sum(1 for r in after.values()
                                    if r["family"] == f and r["correct"])}
                   for f in families},
        "labels": dict(labels),
        "changed": [{"key": k, "label": l, "before": b, "after": a}
                    for k, b, a, l in changed],
        "execution": {**execution,
                      "historical_queries": historical_n,
                      "fallback_changed_answer": answer_effect},
        "verified_baseline": {k: {"score": v[0], "of": v[1], "source": v[2]}
                              for k, v in VERIFIED_BASELINE.items()},
        "candidate_not_promoted": {
            k: {"score": v[0], "of": v[1], "source": v[2]}
            for k, v in CANDIDATE.items()},
        "privacy": {"undeclared": sorted(undeclared),
                    "unscanned_content_stores":
                        [p.store_name for p in unscanned]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
