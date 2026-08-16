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
    adapter.close()
    return rows


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

    events, ledger, people = generate(ticks=1000, subjects=20, seed=args.seed)
    workdir = _REPO / ".staging" / "integration_gate"
    workdir.mkdir(parents=True, exist_ok=True)
    after = run_now(events, ledger, people, Embedder(), LLMEngine.from_env(),
                    args.seed, args.per_family, workdir)

    print("=" * 74)
    print("ĐIỂM THEO NHÓM — trước / sau")
    print("=" * 74)
    print(f"  {'nhóm':<14}{'trước':>12}{'sau':>12}{'Δ':>6}")
    families = ("ever", "current", "historical", "forgotten")
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
        "privacy": {"undeclared": sorted(undeclared),
                    "unscanned_content_stores":
                        [p.store_name for p in unscanned]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
