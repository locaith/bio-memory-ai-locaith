"""A/B: does a lifecycle help the runtime, or only the primitive?

    A  LIFECYCLE_RELATIONS=off    what ships today
    B  LIFECYCLE_RELATIONS=safe   classify each incoming claim and act only
                                  where the relation is established
    C  oracle relations           measurement only, never shipped

Everything else is held equal by construction rather than by care: one world,
one seed, one runtime fingerprint, one embedder instance, one grader, one set
of questions. The arms differ in a single argument.

**The benchmark is not touched.** A change to the scoring between two arms
makes the comparison meaningless, and the temptation to make one arrives
exactly when an arm underperforms.

The hypothesis is stated before the run and is specific, because "the total
went up" is not evidence a lifecycle works:

    historical            should rise, on CORRECT and SUPERSEDE cases
    lifecycle errors      should fall in those classes
    current               should be roughly unchanged
    forgotten             should be roughly unchanged

If unrelated numbers move too, that is coupling to investigate before claiming
anything. A win in the predicted class is evidence; a win spread evenly across
everything is a different change than the one that was made.

C exists to size the remaining headroom. If perfect relation labels barely move
Lifetime, relations are not the bottleneck and the next effort belongs
elsewhere. It is an upper bound, not a product.
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
    Expect, Family, Question, questions_at,
)
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)


def _fold(text: str) -> str:
    import re
    import unicodedata

    return re.sub(r"\s+", " ",
                  unicodedata.normalize("NFC", str(text or ""))).strip().lower()


def retrieval_hit(question: Question, retrieved: list[str]) -> bool | None:
    """The grader, imported in spirit from `lifetime_benchmark`.

    Copied rather than imported because that module runs a whole benchmark on
    import of its `main`; the rule itself is identical and
    `test_lifecycle_ab.py::test_both_scripts_grade_identically` fails if the
    two ever diverge.
    """
    blob = _fold(" ".join(retrieved))
    if question.forbidden:
        return not any(question.leaks_in_memory(m) for m in retrieved)
    if question.expect is Expect.VALUE:
        return _fold(question.value) in blob
    if question.expect is Expect.DECLINE:
        return _fold(question.value) not in blob
    return None


#: Which relation a failing question was *about*, taken from the world.
#:
#: This is the answer key, used only to bucket failures for the report. The
#: runtime never sees it. Without it "historical went up" cannot be turned into
#: "historical went up on the cases a lifecycle should fix", which is the only
#: version of the claim worth making.
RELATION_OF_KIND = {
    EventKind.CORRECT: "correct",
    EventKind.SUPERSEDE: "supersede",
    EventKind.CONTRADICT: "conflict",
    EventKind.REPEAT: "repeat",
}


def _relation_history(events) -> dict[tuple[str, str], list[str]]:
    """Which relations ever touched each slot, in order."""
    out: dict[tuple[str, str], list[str]] = {}
    for event in events:
        if event.subject_id is None or event.attribute is None:
            continue
        name = RELATION_OF_KIND.get(event.kind)
        if name:
            out.setdefault((event.subject_id, event.attribute), []).append(name)
    return out


def contamination(question: Question, retrieved: list[str], ledger) -> tuple[int, int]:
    """(retired claims returned, claims returned) for one question.

    The number the existing grader cannot produce, and the one a lifecycle
    actually moves.

    `retrieval_hit` asks whether the right answer is *somewhere* in what came
    back. Removing a value that stopped being true cannot improve that — the
    right answer was already there or it was not. So an A/B graded on it alone
    reports 0.0000 whatever the lifecycle does, which is what the first three
    runs of this script did.

    This counts the opposite: how much of the returned context is a value that
    was not true at the moment asked. It is what reaches a model, and it is
    what makes a model answer with last year's job title.
    """
    if not question.subject_id or not question.attribute:
        return 0, 0
    live = ledger.at(question.subject_id, question.attribute, question.tick,
                     asked_at=question.tick)
    live_value = _fold(live.value) if live is not None else None
    ever = {_fold(c.value) for c in ledger.claims
            if c.subject_id == question.subject_id
            and c.attribute == question.attribute}

    about_slot = retired = 0
    for memory in retrieved:
        folded = _fold(memory)
        held = [v for v in ever if v and v in folded]
        if not held:
            continue
        about_slot += 1
        if live_value is None or live_value not in folded:
            retired += 1
    return retired, about_slot


def run_arm(name: str, *, mode: str | None, oracle: bool, events, ledger,
            people, embedder, seed: int, per_family: int,
            workdir: Path) -> dict:
    """One arm. Same everything except `mode`."""
    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    adapter = CognitiveAdapter(workdir / f"{name}.db", embedder=embedder,
                               lifecycle_mode=mode)
    adapter.reset()
    if oracle:
        _install_oracle(adapter, events)

    rng = random.Random(seed)
    asked: list[Question] = []
    results: list[tuple[Question, list[str], bool | None]] = []
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
            asked.append(question)
            result = adapter.query(question.text, checkpoint)
            results.append((question, result.retrieved,
                            retrieval_hit(question, result.retrieved)))

    stats = (adapter.lifecycle.stats.as_dict()
             if adapter.lifecycle is not None else {})
    adapter.close()

    by_family: Counter = Counter()
    totals: Counter = Counter()
    retired_total = slot_total = 0
    failures = []
    for question, retrieved, hit in results:
        retired, about = contamination(question, retrieved, ledger)
        retired_total += retired
        slot_total += about
        if hit is None:
            continue
        totals[question.family.value] += 1
        by_family[question.family.value] += int(hit)
        if not hit:
            failures.append({"family": question.family.value,
                             "tick": question.tick,
                             "question": question.text,
                             "subject_id": question.subject_id,
                             "attribute": question.attribute,
                             "expect": question.expect.value,
                             "value": question.value,
                             "retrieved": retrieved[:6]})

    return {"arm": name, "mode": mode, "oracle": oracle,
            "by_family": {f: (by_family[f], totals[f]) for f in totals},
            "stale_context": (retired_total, slot_total),
            "failures": failures, "lifecycle": stats}


def _install_oracle(adapter, events) -> None:
    """Arm C: hand the runtime the world's own answer, and nothing else.

    A ceiling, not a product. It replaces only the *classification*: the
    actions, the audit and the abstention rules stay exactly as they are, so
    what it measures is how much of the remaining gap is relation
    understanding rather than everything downstream of it.
    """
    from bio_agent_os.cognitive.lifecycle_runtime import LifecycleRuntime, SAFE
    from bio_agent_os.cognitive.relations import Judgement, Relation

    if adapter.lifecycle is None:
        adapter.lifecycle = LifecycleRuntime(adapter.memory_os, mode=SAFE)

    truth = {}
    for event in events:
        name = RELATION_OF_KIND.get(event.kind)
        if name and event.text:
            truth[_fold(event.text)] = Relation(name)

    original = adapter.lifecycle._observe

    def observed(*, content: str, memory_id: str, observed_at: str,
                 tenant_id=None):
        relation = truth.get(_fold(content))
        if relation is None:
            return original(content=content, memory_id=memory_id,
                            observed_at=observed_at, tenant_id=tenant_id)
        import bio_agent_os.cognitive.relations as relations_module

        real = relations_module.classify_relation

        def oracle_classify(old_claim, new_event, context=None):
            return Judgement(relation=relation, reason="oracle",
                             evidence=("nhãn của thế giới",), confidence=1.0,
                             predicate=str(getattr(new_event, "predicate", "")))

        relations_module.classify_relation = oracle_classify
        try:
            import bio_agent_os.cognitive.lifecycle_runtime as runtime_module
            runtime_module.classify_relation = oracle_classify
            return original(content=content, memory_id=memory_id,
                            observed_at=observed_at, tenant_id=tenant_id)
        finally:
            relations_module.classify_relation = real
            runtime_module.classify_relation = real

    adapter.lifecycle._observe = observed


def compare(a: dict, b: dict, relation_history) -> dict:
    """Failure classes, so a win can be checked against the hypothesis."""
    def bucket(arm):
        counts: Counter = Counter()
        for failure in arm["failures"]:
            slot = (failure["subject_id"], failure["attribute"])
            relations = relation_history.get(slot, [])
            label = "|".join(sorted(set(relations))) or "no_relation"
            counts[f"{failure['family']}::{label}"] += 1
        return counts

    a_counts, b_counts = bucket(a), bucket(b)
    keys = sorted(set(a_counts) | set(b_counts))
    return {k: {"A": a_counts.get(k, 0), "B": b_counts.get(k, 0),
                "delta": b_counts.get(k, 0) - a_counts.get(k, 0)}
            for k in keys}


def _rate(pair) -> float:
    hit, total = pair
    return round(hit / total, 4) if total else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(prog="lifecycle_ab")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--oracle", action="store_true",
                    help="cũng chạy nhánh C với nhãn quan hệ hoàn hảo")
    ap.add_argument("--out", default="benchmark_reports/lifecycle_ab.json")
    args = ap.parse_args()

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.provenance import identity

    who = identity()
    print("RUNTIME")
    print(f"  vân tay : {who.fingerprint}")
    print(f"  git     : {who.git_sha[:12]}"
          f"{'  ⚠ CHƯA COMMIT' if who.git_dirty else ''}")
    print("  Một tiến trình, một embedder, một bộ câu hỏi. Hai nhánh khác "
          "nhau đúng một tham số.\n")

    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    relation_history = _relation_history(events)
    embedder = Embedder()
    workdir = _REPO / ".staging" / "lifecycle_ab"
    workdir.mkdir(parents=True, exist_ok=True)

    print("GIẢ THUYẾT, nêu trước khi chạy")
    print("  historical TĂNG, và tăng ở các ca CORRECT/SUPERSEDE")
    print("  current và forgotten ĐỨNG YÊN trong biên độ một câu")
    print("  silent_conflict_rate = 0 và forced_transition_rate = 0")
    print("  Nếu các cột không liên quan cũng nhảy thì kiểm coupling trước "
          "khi nhận công.\n")

    arms = [("A_off", None, False), ("B_safe", "safe", False)]
    if args.oracle:
        arms.append(("C_oracle", "safe", True))

    results = {}
    for name, mode, oracle in arms:
        print(f"  đang chạy {name} …")
        results[name] = run_arm(name, mode=mode, oracle=oracle, events=events,
                                ledger=ledger, people=people,
                                embedder=embedder, seed=args.seed,
                                per_family=args.per_family, workdir=workdir)

    # Refused before any number is printed. The first run of this script
    # reported a delta of exactly 0.0000 on every family while every lifecycle
    # action was failing with "Cannot operate on a closed database" — the arm
    # had not run, and the output was indistinguishable from a clean negative
    # result. A comparison whose treatment silently did nothing is not a
    # negative finding, it is a broken experiment.
    broken = {name: r["lifecycle"]["errors"] for name, r in results.items()
              if r["lifecycle"].get("errors")}
    if broken:
        print("\n  DỪNG: nhánh có lỗi khi thi hành lifecycle, nên số đo không "
              "so sánh được.")
        for name, errors in broken.items():
            print(f"    {name}: {len(errors)} lỗi, ví dụ {errors[0]}")
        print("  Một nhánh thí nghiệm không chạy được KHÔNG phải là kết quả âm.")
        return 2

    print("\n" + "=" * 70)
    print("KẾT QUẢ — cùng thế giới, cùng seed, cùng bộ chấm")
    print("=" * 70)
    families = ("current", "historical", "ever", "forgotten")
    header = f"  {'nhánh':<10}" + "".join(f"{f:>13}" for f in families)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name in results:
        row = f"  {name:<10}"
        for family in families:
            pair = results[name]["by_family"].get(family)
            row += f"{_rate(pair):>13.4f}" if pair else f"{'—':>13}"
        print(row)

    base = results["A_off"]
    print("\n  CHÊNH LỆCH so với A")
    for name in results:
        if name == "A_off":
            continue
        row = f"  {name:<10}"
        for family in families:
            here = results[name]["by_family"].get(family)
            there = base["by_family"].get(family)
            if here and there:
                delta = _rate(here) - _rate(there)
                n = there[1]
                row += f"{delta:>+13.4f}"
            else:
                row += f"{'—':>13}"
        print(row)
    sizes = {f: base["by_family"].get(f, (0, 0))[1] for f in families}
    print(f"  cỡ mẫu: {sizes}")

    print("\n  NGỮ CẢNH LỖI THỜI — số đo mà bộ chấm trên KHÔNG nhìn thấy")
    print("  Bộ chấm hỏi 'đáp án đúng có trong tập trả về không'. Bỏ bớt một")
    print("  giá trị đã hết đúng không thể làm nó tăng. Cột dưới đếm điều")
    print("  ngược lại: bao nhiêu ký ức trả về mang giá trị KHÔNG còn đúng")
    print("  vào lúc được hỏi — thứ đi thẳng vào ngữ cảnh của model.\n")
    for name in results:
        retired, about = results[name]["stale_context"]
        rate = round(retired / about, 4) if about else 0.0
        print(f"  {name:<10} {retired}/{about} = {rate:.4f}")
    print("  Với n≈40, một câu đổi làm tỷ lệ nhảy 0.025. Đừng đọc chênh lệch "
          "nhỏ hơn thế thành xu hướng.")

    print("\n" + "=" * 70)
    print("LIFECYCLE ĐÃ LÀM GÌ")
    print("=" * 70)
    for name in results:
        stats = results[name]["lifecycle"]
        if not stats:
            print(f"  {name}: cờ tắt, không gọi lần nào")
            continue
        print(f"  {name}")
        print(f"    relation_calls          {stats['relation_calls']}")
        print(f"    relation_decisions      {stats['relation_decisions']}")
        print(f"    repeat_transitions      {stats['repeat_transitions']}")
        print(f"    supersede_transitions   {stats['supersede_transitions']}")
        print(f"    correct_transitions     {stats['correct_transitions']}")
        print(f"    coexist_transitions     {stats['coexist_transitions']}")
        print(f"    unknown_count           {stats['unknown_count']}")
        print(f"    conflict_count          {stats['conflict_count']}")
        print(f"    silent_conflict_rate    {stats['silent_conflict_rate']}")
        print(f"    forced_transition_rate  {stats['forced_transition_rate']}")
        print(f"    unknown_later_resolved  {stats['unknown_later_resolved']}"
              f"  (còn mở: {stats['unknown_still_open']})")
        print(f"    orphaned_actions        {stats['orphaned_actions']}"
              f"  (claim cũ đã bị lệnh xoá lấy đi trước đó)")
        if stats["unknown_reasons"]:
            print(f"    lý do UNKNOWN           {stats['unknown_reasons']}")
        if stats["conflict_reasons"]:
            print(f"    lý do CONFLICT          {stats['conflict_reasons']}")
        if stats["errors"]:
            print(f"    ⚠ lỗi                   {stats['errors']}")

    print("\n" + "=" * 70)
    print("LỖI THEO LỚP QUAN HỆ — đây mới là chỗ kiểm giả thuyết")
    print("=" * 70)
    print("  Nhãn lấy từ thế giới, chỉ để phân loại lỗi. Runtime không thấy.\n")
    buckets = compare(base, results["B_safe"], relation_history)
    print(f"  {'lớp':<44}{'A':>5}{'B':>5}{'Δ':>7}")
    for key, counts in sorted(buckets.items(),
                              key=lambda kv: kv[1]["delta"]):
        if counts["A"] == 0 and counts["B"] == 0:
            continue
        mark = ""
        if counts["delta"] < 0:
            mark = "  ✓ giảm"
        elif counts["delta"] > 0:
            mark = "  ⚠ tăng"
        print(f"  {key:<44}{counts['A']:>5}{counts['B']:>5}"
              f"{counts['delta']:>+7}{mark}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "runtime": who.as_dict(),
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed, "per_family": args.per_family},
        "hypothesis": {
            "historical": "tăng, ở các ca CORRECT/SUPERSEDE",
            "current": "đứng yên trong biên độ một câu",
            "forgotten": "đứng yên trong biên độ một câu",
            "silent_conflict_rate": 0, "forced_transition_rate": 0},
        "arms": results,
        "failure_classes": buckets,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
