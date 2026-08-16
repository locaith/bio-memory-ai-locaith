"""Does `classify_relation` get the relation right, and how does it fail?

Accuracy alone would hide the two failures that matter, so three numbers are
printed and two of them target zero:

    lifecycle_relation_accuracy   how often the verdict is the labelled one
    silent_conflict_rate          real disagreements quietly given a winner
    forced_transition_rate        truth rewritten on evidence that did not
                                  reach a verdict

The second and third are not slices of the first. A classifier can score well
on accuracy while resolving every conflict it meets, and that classifier is
worse than one which scores lower and says so — for a memory meant to last
years, a wrong belief held confidently costs more than an open question.

Seven mutants run beside the real one. Each is a rule someone would plausibly
write, and each must be *caught*: if a mutant scores as well as the classifier,
this benchmark is not measuring what it claims to.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.relations import (  # noqa: E402
    Claim, Event, Judgement, Relation, classify_relation, semantics_for,
)
from bio_agent_os.evals.relation_cases import Case, all_cases  # noqa: E402
from bio_agent_os.evals.lifetime_world import generate  # noqa: E402

ORDER = (Relation.REPEAT, Relation.CORRECT, Relation.SUPERSEDE,
         Relation.COEXIST, Relation.CONFLICT, Relation.UNKNOWN)


# --------------------------------------------------------------------------
# mutants — rules someone would plausibly write, each of which must be caught
# --------------------------------------------------------------------------

def _judge(relation: Relation) -> Judgement:
    return Judgement(relation=relation, reason="mutant", confidence=1.0)


def everything_newer_supersedes(old: Claim, new: Event, ctx=None) -> Judgement:
    """The rule this whole module replaces."""
    if str(new.observed_at or "") > str(old.observed_at or ""):
        return _judge(Relation.SUPERSEDE)
    return _judge(Relation.UNKNOWN)


def everything_different_conflicts(old: Claim, new: Event, ctx=None) -> Judgement:
    """Maximally cautious, and useless: nothing ever becomes current."""
    same = str(old.value).lower() == str(new.value).lower()
    return _judge(Relation.REPEAT if same else Relation.CONFLICT)


def everything_coexists(old: Claim, new: Event, ctx=None) -> Judgement:
    """Never delete anything. The store fills with contradictions."""
    same = str(old.value).lower() == str(new.value).lower()
    return _judge(Relation.REPEAT if same else Relation.COEXIST)


def latest_mention_wins(old: Claim, new: Event, ctx=None) -> Judgement:
    """The standing adversary: a timestamped vector store's whole theory of
    time. A repeat restates an old value; recency reads that as a change."""
    return _judge(Relation.SUPERSEDE)


def correction_as_supersession(old: Claim, new: Event, ctx=None) -> Judgement:
    """Treats "I was wrong" as "it changed".

    The one that looks harmless and is not: it keeps the false value as
    historical world-truth, so "what was his birth date in March?" answers with
    the value the user explicitly retracted.
    """
    verdict = classify_relation(old, new, ctx)
    if verdict.relation is Relation.CORRECT:
        return _judge(Relation.SUPERSEDE)
    return verdict


def repeat_extends_validity(old: Claim, new: Event, ctx=None) -> Judgement:
    """Treats being mentioned again as becoming true again.

    Reported as SUPERSEDE because that is what "extend the window" does to the
    ledger: a new validity interval opening at the moment of the repeat.
    """
    verdict = classify_relation(old, new, ctx)
    if verdict.relation is Relation.REPEAT:
        return _judge(Relation.SUPERSEDE)
    return verdict


def unknown_forced_to_supersede(old: Claim, new: Event, ctx=None) -> Judgement:
    """Refuses to say "I cannot tell". Every gap becomes a decision."""
    verdict = classify_relation(old, new, ctx)
    if verdict.relation in (Relation.UNKNOWN, Relation.CONFLICT):
        return _judge(Relation.SUPERSEDE)
    return verdict


MUTANTS = {
    "everything_newer_supersedes": everything_newer_supersedes,
    "everything_different_conflicts": everything_different_conflicts,
    "everything_coexists": everything_coexists,
    "latest_mention_wins": latest_mention_wins,
    "correction_as_supersession": correction_as_supersession,
    "repeat_extends_validity": repeat_extends_validity,
    "unknown_forced_to_supersede": unknown_forced_to_supersede,
}

#: Below this, a relation has too few cases for a recall gap to mean anything.
MIN_RELATION_SAMPLE = 5


def recall_for(cases: list[Case], classifier, relation: Relation) -> tuple[int, int]:
    """How many cases of one relation the classifier gets right."""
    subset = [c for c in cases if c.expected is relation]
    hit = sum(1 for c in subset
              if classifier(c.old, c.new, None).relation is relation)
    return hit, len(subset)


def recall_gaps(cases: list[Case], mutant) -> list[tuple[Relation, int, int, int]]:
    """Which relations a mutant loses, worst first.

    Overall accuracy is the wrong gate and the corpus shows why: REPEAT is 59%
    of it, so a mutant that gets every CORRECT wrong still scores 0.90 and
    reads as "barely different". That measures the corpus, not the mutant —
    the same mistake this project made in the lifetime benchmark this morning
    and fixed the same way.

    Computed rather than hand-assigned. A table of "this mutant targets that
    relation" goes stale the moment the classifier changes, and it went stale
    within one sitting: after the unmarked-change rule was tightened,
    `everything_different_conflicts` sat 0.090 below on accuracy and escaped a
    hand-set threshold entirely, while still getting every SUPERSEDE, CORRECT
    and COEXIST case wrong.
    """
    gaps = []
    for relation in ORDER:
        mutant_hit, n = recall_for(cases, mutant, relation)
        if n < MIN_RELATION_SAMPLE:
            continue
        ours, _ = recall_for(cases, classify_relation, relation)
        if mutant_hit < ours:
            gaps.append((relation, mutant_hit, ours, n))
    return sorted(gaps, key=lambda g: g[1] - g[2])


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score(cases: list[Case], classifier) -> dict:
    matrix: Counter = Counter()
    wrong: list[dict] = []
    for case in cases:
        got = classifier(case.old, case.new, None).relation
        matrix[(case.expected, got)] += 1
        if got is not case.expected:
            wrong.append({"name": case.name, "expected": case.expected.value,
                          "got": got.value, "why": case.why,
                          "source": case.source,
                          "old": case.old.value, "new": case.new.value,
                          "text": case.new.text})

    total = len(cases)
    correct = sum(n for (want, got), n in matrix.items() if want is got)

    # Of the cases that genuinely cannot be decided, how many were decided
    # anyway. `resolves` is every relation except CONFLICT and UNKNOWN.
    conflicts = [c for c in cases if c.expected is Relation.CONFLICT]
    silent = sum(1 for c in conflicts
                 if classifier(c.old, c.new, None).relation.resolves)

    # Of the cases where the evidence reaches no verdict, how many had the
    # system's beliefs rewritten anyway. Counted over CONFLICT as well as
    # UNKNOWN: a conflict turned into a supersession also rewrites truth.
    undecidable = [c for c in cases
                   if c.expected in (Relation.UNKNOWN, Relation.CONFLICT)]
    forced = sum(1 for c in undecidable
                 if classifier(c.old, c.new, None).relation.changes_truth)

    # Of the cases it gets wrong, how many fail to an outcome that changes no
    # belief. A classifier meeting wording it has never seen will be wrong;
    # what decides whether that is tolerable is where the wrongness lands.
    # Answering UNKNOWN leaves a question open. Answering SUPERSEDE to an
    # unrecognised correction writes a retracted value into history as true.
    unsafe = [w for w in wrong
              if Relation(w["got"]).changes_truth
              and Relation(w["expected"]) in (Relation.CONFLICT,
                                              Relation.UNKNOWN,
                                              Relation.CORRECT)]

    return {
        "n": total,
        "lifecycle_relation_accuracy": round(correct / total, 4) if total else 0.0,
        "unsafe_failures": len(unsafe),
        "unsafe_failure_rate": round(len(unsafe) / total, 4) if total else 0.0,
        "silent_conflict_rate": round(silent / len(conflicts), 4) if conflicts else None,
        "forced_transition_rate": round(forced / len(undecidable), 4) if undecidable else None,
        "silent_conflicts": silent,
        "conflict_cases": len(conflicts),
        "forced_transitions": forced,
        "undecidable_cases": len(undecidable),
        "matrix": {f"{want.value}->{got.value}": n
                   for (want, got), n in sorted(
                       matrix.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value))},
        "wrong": wrong,
    }


def print_matrix(result: dict) -> None:
    header = "  thật \\ đoán  " + "".join(f"{r.value[:9]:>11}" for r in ORDER)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for want in ORDER:
        cells = []
        for got in ORDER:
            n = result["matrix"].get(f"{want.value}->{got.value}", 0)
            cells.append(f"{n:>11}" if n else f"{'.':>11}")
        total = sum(result["matrix"].get(f"{want.value}->{g.value}", 0)
                    for g in ORDER)
        print(f"  {want.value:<13}" + "".join(cells) + f"   /{total}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="relation_benchmark")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--out", default="benchmark_reports/relations.json")
    ap.add_argument("--expect-runtime", default=None)
    args = ap.parse_args()

    from bio_agent_os.core.provenance import RuntimeMismatch, identity, require

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}")
    if args.expect_runtime:
        try:
            require(args.expect_runtime, actual=who)
        except RuntimeMismatch as mismatch:
            print(f"  DỪNG: {mismatch}")
            return 2
        print("  khớp vân tay kỳ vọng ✓")

    events, ledger, people = generate(ticks=args.ticks, subjects=args.subjects,
                                      seed=args.seed)
    cases = all_cases(events, ledger, people)
    by_source = Counter(c.source for c in cases)
    by_label = Counter(c.expected.value for c in cases)

    print("\n" + "=" * 70)
    print("BỘ CA CÓ NHÃN")
    print("=" * 70)
    print(f"  {len(cases)} ca | theo nguồn: {dict(by_source)}")
    print(f"  theo nhãn: {dict(by_label)}")
    missing = [r.value for r in ORDER if not by_label.get(r.value)]
    if missing:
        print(f"  ⚠ KHÔNG có ca nào cho: {missing} — ma trận không đầy đủ, "
              f"những quan hệ đó chưa được đo")

    result = score(cases, classify_relation)

    print("\n" + "=" * 70)
    print("MA TRẬN NHẦM LẪN")
    print("=" * 70)
    print_matrix(result)

    print("\n" + "=" * 70)
    print("SỐ ĐO")
    print("=" * 70)
    print(f"  lifecycle_relation_accuracy : {result['lifecycle_relation_accuracy']:.4f}"
          f"  ({result['n'] - len(result['wrong'])}/{result['n']})")
    silent = result["silent_conflict_rate"]
    forced = result["forced_transition_rate"]
    print(f"  silent_conflict_rate        : {silent}"
          f"  ({result['silent_conflicts']}/{result['conflict_cases']})"
          f"{'' if silent == 0 else '   ⚠ MỤC TIÊU 0'}")
    print(f"  forced_transition_rate      : {forced}"
          f"  ({result['forced_transitions']}/{result['undecidable_cases']})"
          f"{'' if forced == 0 else '   ⚠ MỤC TIÊU 0'}")

    print("\n" + "=" * 70)
    print("THEO NGUỒN — chỗ này mới nói được điều gì đã thật sự được đo")
    print("=" * 70)
    print("  'world' dùng đúng câu chữ thế giới sinh ra, mà thế giới chỉ có")
    print("  một tiền tố cho mỗi kind — nên cột đó gần với việc dò tiền tố.")
    print("  'world_heldout' dùng cách nói CỐ TÌNH không có trong bảng marker.")
    print("  Con số đáng tin nằm ở cột sau.\n")
    for source in ("curated", "world", "world_heldout"):
        subset = [c for c in cases if c.source == source]
        if not subset:
            continue
        sub = score(subset, classify_relation)
        print(f"  {source:<16} n={sub['n']:<5} acc {sub['lifecycle_relation_accuracy']:.4f}"
              f"  silent {sub['silent_conflict_rate']}"
              f"  forced {sub['forced_transition_rate']}"
              f"  hỏng-không-an-toàn {sub['unsafe_failures']}")

    print("\n" + "=" * 70)
    print("CÁI GIÁ CỦA VIỆC AN TOÀN — hai chính sách, cùng một bộ ca")
    print("=" * 70)
    print("  mặc định : lời khẳng định muộn hơn KHÔNG đủ để kết luận thay đổi")
    print("  opt-in   : caller khai rằng trong miền của họ thì nó đủ\n")
    lenient = score(cases, lambda o, n, c: classify_relation(
        o, n, {"unmarked_change": "supersede"}))
    hit_strict, n_sup = recall_for(cases, classify_relation, Relation.SUPERSEDE)
    hit_lenient, _ = recall_for(
        cases, lambda o, n, c: classify_relation(
            o, n, {"unmarked_change": "supersede"}), Relation.SUPERSEDE)
    for label, res, sup in (("mặc định", result, hit_strict),
                            ("opt-in  ", lenient, hit_lenient)):
        print(f"  {label}  acc {res['lifecycle_relation_accuracy']:.4f}"
              f"  silent {res['silent_conflict_rate']}"
              f"  forced {res['forced_transition_rate']}"
              f"  supersede bắt được {sup}/{n_sup}"
              f"  hỏng-không-an-toàn {res['unsafe_failures']}")
    print("\n  Chính sách nới lỏng bắt được nhiều supersede hơn và trả giá")
    print("  bằng đúng hai thứ anh đặt mục tiêu bằng 0. Số nằm cạnh nhau để")
    print("  cái giá đó là một lựa chọn, không phải một mặc định vô hình.")

    if result["wrong"]:
        print("\n  SAI:")
        for item in result["wrong"][:20]:
            print(f"    {item['name']}")
            print(f"      cần {item['expected']}, được {item['got']}"
                  f"   {item['old']!r} -> {item['new']!r}")
            print(f"      {item['why']}")

    print("\n" + "=" * 70)
    print("MUTANT — mỗi cái phải bị bắt")
    print("=" * 70)
    print("  Bắt được nghĩa là: ít nhất một số đo tách nó khỏi bộ phân loại")
    print("  thật. Cột 'bắt bởi' nói số đo nào, chứ không gộp vào một ngưỡng.\n")
    mutant_scores = {}
    undetected = []
    for name, mutant in MUTANTS.items():
        mutant_result = score(cases, mutant)
        gap = result["lifecycle_relation_accuracy"] - \
            mutant_result["lifecycle_relation_accuracy"]

        caught_by = []
        if gap >= 0.15:
            caught_by.append(f"accuracy {gap:+.3f}")
        if (mutant_result["silent_conflict_rate"] or 0) > 0:
            caught_by.append(f"silent {mutant_result['silent_conflict_rate']}")
        if (mutant_result["forced_transition_rate"] or 0) > 0:
            caught_by.append(f"forced {mutant_result['forced_transition_rate']}")
        gaps = recall_gaps(cases, mutant)
        for relation, hit, ours, n in gaps[:2]:
            caught_by.append(f"{relation.value} {hit}/{n} so với {ours}/{n}")

        mutant_scores[name] = {
            **{k: mutant_result[k] for k in
               ("lifecycle_relation_accuracy", "silent_conflict_rate",
                "forced_transition_rate")},
            "recall_gaps": [{"relation": r.value, "mutant": h, "ours": o,
                             "n": n} for r, h, o, n in gaps],
            "caught_by": caught_by}
        if not caught_by:
            undetected.append(name)

        print(f"  {name}")
        print(f"    acc {mutant_result['lifecycle_relation_accuracy']:.4f}"
              f"  silent {mutant_result['silent_conflict_rate']}"
              f"  forced {mutant_result['forced_transition_rate']}")
        print(f"    bắt bởi: {', '.join(caught_by) if caught_by else '⚠ KHÔNG GÌ CẢ'}")

    if undetected:
        print(f"\n  ⚠ {len(undetected)} mutant không bị bắt: {undetected}")
        print("    Benchmark này chưa đo được kiểu hỏng đó.")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(),
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed},
        "cases": {"total": len(cases), "by_source": dict(by_source),
                  "by_label": dict(by_label)},
        "result": result,
        "mutants": mutant_scores,
        "semantics": {p.name: semantics_for(p).as_dict()
                      for p in __import__(
                          "bio_agent_os.cognitive.aspect_resolver",
                          fromlist=["Predicate"]).Predicate
                      if p.attribute},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
