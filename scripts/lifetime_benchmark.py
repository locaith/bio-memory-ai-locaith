"""Run a memory system through a thousand events and measure it along the way.

One life, measured at checkpoints — the store is not rebuilt between them, so
what is being measured is a system that has been *lived in*, not seven separate
short runs.

Two scores per question, always:

    retrieval_hit    did the right value appear in what was retrieved
    answer_correct   did the model's answer contain it

A single end-to-end number cannot separate "the memory did not find it" from
"the model was handed it and answered badly", and those need different fixes.
Without `--engine` only the retrieval column runs, which costs nothing.

THE INTEGRITY REPORT

Printed every run, before the scores, because scores from a broken instrument
are worse than no scores. It states the denominators, the answer balance, and
what the reference systems scored — and refuses to endorse a category whose
sample is too small or whose expected answers all point one way.

That check exists because of what happened while this benchmark was being
built. Every one of these was found by printing numbers *after* the tests went
green:

    the deletion family had 4 questions out of 94
    all 30 EVER questions expected "yes", so a fake answering "Có" scored 30/30
    the grader read "Tôi không biết" as "no", because it contains "không"

Green tests prove the implementation satisfies the assertions. They do not
prove the assertions measure what anyone thinks they measure.

Nothing here is tuned. A first run exists to find out where the system stands,
not to show it in a good light, so a bad number is the point.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from bio_agent_os.evals.lifetime_questions import (  # noqa: E402
    Expect, Family, Question, questions_at,
)
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

CHECKPOINTS = (10, 50, 100, 250, 500, 750, 1000)

#: Below this, a category's rate is arithmetic rather than measurement.
MIN_SAMPLE = 8
#: Beyond this share pointing one way, a category can be passed by answering
#: that way every time.
MAX_SKEW = 0.85

#: Families where the expected answer can legitimately vary, and where a
#: one-sided split therefore means the family is passable by rote. CURRENT and
#: HISTORICAL always want a value — that is what they are — so they are checked
#: by `plausible_wrong` instead.
_MIXABLE = {Family.EVER, Family.FORGOTTEN}


def _fold(text: str) -> str:
    import re
    import unicodedata

    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


def retrieval_hit(question: Question, retrieved: list[str]) -> bool | None:
    """Was the answer present in what came back? None where it does not apply.

    Independent of any model, so it isolates the memory system. For a question
    whose right answer is a refusal, the test is the opposite: the forbidden
    value must be absent.

    A leak is judged per memory, not across the whole blob. Joining everything
    first lets one memory's subject name pair with another memory's value and
    read as a leak — the twenty subjects of this world share five phone
    numbers, so that pairing happens constantly.
    """
    blob = _fold(" ".join(retrieved))
    if question.forbidden:
        return not any(question.leaks_in_memory(memory) for memory in retrieved)
    if question.expect is Expect.VALUE:
        return _fold(question.value) in blob
    if question.expect is Expect.DECLINE:
        return _fold(question.value) not in blob
    return None                      # yes/no needs reasoning, not retrieval


def _where_it_survives(adapter, needle: str,
                       subject_name: str = "") -> dict[str, dict[str, int]]:
    """Which store still holds a value that was supposed to be gone.

    "The deletion leaked" is one word for at least six different defects —
    the primary row surviving, a vector surviving, a label, a consolidated
    copy, a replay rebuilding it, or the grader mistaking a *new* fact with the
    same value for the old one. They live in different layers and need
    different fixes, so the layer is recorded rather than inferred later.

    Two counts per layer, because one of them was misleading me:

        this_subject  rows holding the value *and* the name it was deleted for
        any_subject   rows holding the value at all

    `any_subject` counted 21 rows for "phó giám đốc" when four people held that
    title, and reading it as leakage is what turned five other people's true
    records into a privacy incident. It stays in the report because it answers
    a different question — whether the string exists anywhere — but only
    `this_subject` is evidence that a deletion failed.

    Read in Python with the same fold used everywhere else. An earlier version
    compared with SQL LIKE while the deleter compared with Python `.lower()`,
    so the deleter could reach case variants the verifier could not see.
    """
    import sqlite3

    if not needle:
        return {}
    conn = adapter.memory_os.memories.conn
    value = _fold(needle)
    parts = [_fold(p) for p in subject_name.split()]
    found: dict[str, dict[str, int]] = {}
    for table, column in (("cognitive_memories", "content"),
                          ("cognitive_memories", "metadata_json"),
                          ("cognitive_events", "payload_json"),
                          ("shadow_memories", "content"),
                          ("hippocampus_labels", "topic")):
        try:
            rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        any_n = scoped_n = 0
        for (cell,) in rows:
            text = _fold(cell or "")
            if value not in text:
                continue
            any_n += 1
            if not parts or all(p in text for p in parts):
                scoped_n += 1
        if any_n:
            found[f"{table}.{column}"] = {"this_subject": scoped_n,
                                          "any_subject": any_n}
    return found


def _read_back(adapter, ledger, people, event) -> dict[str, Any]:
    """Check the store directly after a deletion, on every request.

    Two properties this has and `forget()`'s own report does not:

    * It runs whether or not a later question failed. Checking only on failure
      makes the check disappear exactly when the grader stops noticing, which
      is how a measurement change can pass for a product improvement.
    * It reads the tables, not the deleter's accounting. `forget_scoped`
      verifying its own work answers "did I do what I decided to do", never
      "is it gone".

    The values come from the ledger — the answer key, which the adapter is
    never handed — and are scoped to the subject the request named.
    """
    from bio_agent_os.cognitive.forgetting import EVENT_COLUMNS

    log_layers = {f"{table}.{column}" for table, column in EVENT_COLUMNS}

    name = next((p.name for p in people if p.subject_id == event.subject_id), "")
    values = sorted({c.value for c in ledger.claims
                     if c.subject_id == event.subject_id
                     and c.attribute == event.attribute
                     and c.forgotten_at == event.tick})
    residue: dict[str, dict[str, dict[str, int]]] = {}
    undoable: dict[str, dict[str, dict[str, int]]] = {}
    for value in values:
        where = _where_it_survives(adapter, value, name)
        scoped = {layer: counts for layer, counts in where.items()
                  if counts["this_subject"]}
        # Content surviving in the append-only log is the design, not a defect:
        # `forget_scoped` runs at derived level and says so. It does mean a
        # replay puts the value back, which is worth a number of its own —
        # counting it as residue would report eighteen privacy failures where
        # there are none, and counting it nowhere would hide that every one of
        # these deletions is undoable.
        in_log = {k: v for k, v in scoped.items() if k in log_layers}
        in_store = {k: v for k, v in scoped.items() if k not in log_layers}
        if in_store:
            residue[value] = in_store
        if in_log:
            undoable[value] = in_log
    return {"asked_to_remove": values, "subject_name": name,
            "store_residue": residue,
            "primary_present_after_store": bool(residue),
            "survives_in_event_log": undoable,
            "reversible_by_replay": bool(undoable)}


def integrity_report(questions: list[Question], events, ledger,
                     reference: dict[str, float]) -> dict:
    """Can this run's numbers be believed? Printed before any of them."""
    warnings: list[str] = []
    by_family = Counter(q.family.value for q in questions)
    by_expect = Counter(q.expect.value for q in questions)

    for family in Family:
        n = by_family.get(family.value, 0)
        if n < MIN_SAMPLE:
            warnings.append(
                f"nhóm {family.value}: chỉ {n} câu — dưới {MIN_SAMPLE}, "
                f"tỷ lệ tính trên đó là số học chứ không phải phép đo")

        # Skew only matters where a mix is possible. CURRENT and HISTORICAL ask
        # for a value by definition, and warning about that trains the reader
        # to skip warnings — which is how the one that matters gets missed.
        # What guards those two is `plausible_wrong`, a fake that answers a
        # valid-looking but incorrect value; if it scores well, the rubric is
        # rewarding fluency.
        if family in _MIXABLE:
            answers = Counter(q.expect.value for q in questions
                              if q.family is family)
            total = sum(answers.values())
            if total >= MIN_SAMPLE:
                top, count = answers.most_common(1)[0]
                if count / total > MAX_SKEW:
                    warnings.append(
                        f"nhóm {family.value}: {count}/{total} câu cùng kỳ vọng "
                        f"'{top}' — trả lời '{top}' cho mọi câu là qua được")

    counts = Counter(e.kind.value for e in events)
    for kind in (EventKind.REPEAT, EventKind.SUPERSEDE, EventKind.CORRECT,
                 EventKind.FORGET):
        if counts.get(kind.value, 0) < 10:
            warnings.append(
                f"thế giới chỉ có {counts.get(kind.value, 0)} sự kiện "
                f"{kind.value} — phân biệt này gần như không được kiểm")

    proven = sum(1 for q in questions if q.forbidden)
    changed = sum(1 for q in questions if q.after_supersession)

    if reference.get("perfect", 0) < 0.95:
        warnings.append(
            f"oracle hoàn hảo chỉ đạt {reference.get('perfect', 0):.3f} — "
            f"rubric không thể đạt được, mọi con số khác vô nghĩa")
    for name, score in reference.items():
        if name != "perfect" and reference.get("perfect", 1) - score < 0.15:
            warnings.append(
                f"hệ hỏng '{name}' chỉ kém oracle {reference['perfect'] - score:.3f}"
                f" — benchmark không phân biệt được kiểu hỏng này")

    return {
        "n": len(questions),
        "by_family": dict(by_family),
        "by_expect": dict(by_expect),
        "deletion_questions_with_proven_value": proven,
        "questions_after_a_real_change": changed,
        "world_events": dict(counts),
        "reference_scores": reference,
        "warnings": warnings,
    }


def _reference_scores(questions, events, ledger) -> dict[str, float]:
    """The oracles, so the integrity report can say whether the rubric works."""
    sys.path.insert(0, str(_REPO / "tests"))
    from test_lifetime_benchmark import (  # noqa: E402
        _always_declines, _first_value, _latest_mention, _never_declines,
        _perfect, _plausible_wrong,
    )
    from bio_agent_os.evals.lifetime_questions import score  # noqa: E402

    return {
        "perfect": score(questions, _perfect(ledger)).accuracy,
        "first_value": score(questions, _first_value(ledger)).accuracy,
        "latest_mention": score(questions, _latest_mention(events)).accuracy,
        "never_declines": score(questions, _never_declines(ledger)).accuracy,
        "always_declines": score(questions, _always_declines(ledger)).accuracy,
        "plausible_wrong": score(questions, _plausible_wrong(ledger)).accuracy,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="lifetime_benchmark")
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--engine", action="store_true",
                    help="cũng chấm câu trả lời của model (tốn tiền)")
    ap.add_argument("--embed", action="store_true", default=True)
    ap.add_argument("--out", default="benchmark_reports/lifetime_2026_08_15.json")
    args = ap.parse_args()

    from bio_agent_os.cognitive.semantic_index import (
        backfill_embeddings, calibrate_with_probes,
    )
    from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter

    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    rng = random.Random(args.seed)

    embedder = engine = None
    if args.embed:
        from bio_agent_os.core.embedder import Embedder
        embedder = Embedder()
    if args.engine:
        from bio_agent_os.core.llm_engine import LLMEngine
        engine = LLMEngine.from_env()

    workdir = _REPO / ".staging" / "lifetime"
    workdir.mkdir(parents=True, exist_ok=True)
    adapter = CognitiveAdapter(workdir / "run.db", embedder=embedder,
                               engine=engine)
    adapter.reset()

    print(f"thế giới: {len(events)} sự kiện, {len(people)} chủ thể, "
          f"seed={args.seed}")
    print(f"embedder: {getattr(embedder, 'backend', 'không')} | "
          f"engine: {getattr(engine, 'model_id', 'không — chỉ chấm truy xuất')}\n")

    all_questions: list[Question] = []
    rows: list[dict] = []
    traces: list[dict] = []
    deletions: list[dict] = []
    fed = 0

    for checkpoint in CHECKPOINTS:
        if checkpoint > args.ticks:
            break
        while fed < checkpoint:
            event = events[fed]
            fed += 1
            # A deletion request is an instruction, not a fact to remember.
            # Storing the sentence and doing nothing is what the first run did,
            # and every deleted value came back — the same defect that made
            # `behaviour_benchmark`'s forgetting cases meaningless, repeated in
            # a new tool on the same day.
            #
            # SUPERSEDE and CORRECT are deliberately NOT routed to
            # `claim_lifecycle`. Telling the system which events replace and
            # which retract hands over the distinction it is being measured on.
            # Detecting that is a capability; if the system does not have it,
            # the historical column should say so.
            if event.kind is EventKind.FORGET:
                report = adapter.forget(event.text)
                report["request"] = event.text
                report["tick"] = event.tick
                report["slot"] = f"{event.subject_id}/{event.attribute}"
                report.update(_read_back(adapter, ledger, people, event))
                deletions.append(report)
            else:
                adapter.ingest(event)
        if embedder is not None:
            while backfill_embeddings(adapter.memory_os.memories.conn,
                                      embedder, limit=200):
                pass
            calibrate_with_probes(adapter.memory_os.memories.conn, embedder)

        questions = questions_at(ledger, people, checkpoint, rng=rng,
                                 per_family=args.per_family)
        all_questions.extend(questions)

        hits = Counter()
        totals = Counter()
        answers = Counter()
        answer_totals = Counter()
        routes = Counter()
        latencies: list[float] = []
        started = time.perf_counter()

        for question in questions:
            result = adapter.query(question.text, tick=checkpoint)
            latencies.append(result.latency_ms)
            routes[f"{result.query_kind}->{result.route}"] += 1

            got = retrieval_hit(question, result.retrieved)
            if got is not None:
                totals[question.family.value] += 1
                hits[question.family.value] += int(got)

            said = None
            if engine is not None:
                said = question.grade(result.answer)
                answer_totals[question.family.value] += 1
                answers[question.family.value] += int(said)

            if got is False or said is False:
                # Where a forbidden value survives decides which layer is at
                # fault, and the store is gone by the time the report is read.
                # Recorded at the moment of failure or not at all.
                leak_store = (
                    _where_it_survives(adapter, question.forbidden,
                                       question.subject_name)
                    if question.forbidden else {})
                traces.append({
                    "leak_store": leak_store,
                    "checkpoint": checkpoint, "family": question.family.value,
                    "question": question.text, "expect": question.expect.value,
                    "value": question.value, "forbidden": question.forbidden,
                    "route": result.route, "query_kind": result.query_kind,
                    "stage_failed": result.stage_failed, "note": result.note,
                    "subject_id": question.subject_id,
                    "attribute": question.attribute,
                    # Was 4, which made a leak unattributable: the memory that
                    # held the forbidden value sat outside the slice, so the
                    # trace showed four innocent memories and no cause. Keep
                    # enough of the set to say whose record it was.
                    "retrieved": result.retrieved[:12],
                    "answer": result.answer[:200],
                    "retrieval_hit": got, "answer_correct": said,
                })

        # Kept apart from `historical` on purpose. When a dated question is
        # answered wrongly, "the memory was wrong" and "the operator never ran"
        # need different fixes, and one combined number cannot tell them apart.
        # Measured once already: the operator was correct and 28 of 28
        # questions stopped at its first stage, which read as no effect at all.
        temporal_total = sum(n for r, n in routes.items() if r.startswith("temporal->"))
        temporal_ran = routes.get("temporal->temporal_operator", 0)

        snapshot = adapter.snapshot()
        row = {
            "checkpoint": checkpoint,
            "routes": dict(routes),
            "temporal_execution_accuracy": (
                round(temporal_ran / temporal_total, 4) if temporal_total else None),
            "temporal_fell_back": temporal_total - temporal_ran,
            "retrieval": {f: round(hits[f] / totals[f], 4)
                          for f in totals if totals[f]},
            "retrieval_n": dict(totals),
            "answer": {f: round(answers[f] / answer_totals[f], 4)
                       for f in answer_totals if answer_totals[f]},
            "memories": snapshot["memories"],
            "bytes": snapshot["bytes"],
            "p50_ms": round(statistics.median(latencies), 1) if latencies else 0,
            "wall_s": round(time.perf_counter() - started, 1),
        }
        rows.append(row)
        overall = (sum(hits.values()) / sum(totals.values())
                   if sum(totals.values()) else 0.0)
        print(f"  mốc {checkpoint:>4} | {row['memories']:>4} ký ức | "
              f"truy xuất {overall:.3f} | p50 {row['p50_ms']:>6.1f} ms")

    report = integrity_report(all_questions, events, ledger,
                              _reference_scores(all_questions, events, ledger))

    print("\n" + "=" * 70)
    print("BENCHMARK INTEGRITY REPORT")
    print("=" * 70)
    print(f"  {report['n']} câu hỏi | theo nhóm: {report['by_family']}")
    print(f"  kỳ vọng: {report['by_expect']}")
    print(f"  câu về việc xoá có giá trị cấm cụ thể: "
          f"{report['deletion_questions_with_proven_value']}")
    print(f"  câu hỏi sau một thay đổi thật: "
          f"{report['questions_after_a_real_change']}")
    acted = sum(1 for d in deletions if d.get("memories_deleted"))
    print(f"  yêu cầu xoá đã THỰC SỰ xoá được gì: {acted}/{len(deletions)}"
          + ("  ⚠ chưa yêu cầu nào xoá được — cột forgotten vô nghĩa"
             if deletions and not acted else ""))

    # Read straight from the tables, on every request, whether or not any
    # question later noticed. This is the number that says a deletion happened;
    # the family score above only says the grader could not see it.
    survived = [d for d in deletions if d.get("primary_present_after_store")]
    undoable = [d for d in deletions if d.get("reversible_by_replay")]
    print(f"  ĐỌC LẠI TỪ KHO — còn sót ở tầng phục vụ: "
          f"{len(survived)}/{len(deletions)}")
    for d in survived:
        for value, layers in d["store_residue"].items():
            where = ", ".join(f"{layer}×{c['this_subject']}"
                              for layer, c in layers.items())
            print(f"    ⚠ t={d['tick']:<5} {d['subject_name']:<12} "
                  f"{value!r} còn ở {where}")
    print(f"  còn trong nhật ký sự kiện (thiết kế, nhưng replay là quay lại): "
          f"{len(undoable)}/{len(deletions)}")
    print("\n  các hệ tham chiếu:")
    for name, value in report["reference_scores"].items():
        print(f"    {name:<18} {value:.3f}")
    if report["warnings"]:
        print("\n  ⚠  CẢNH BÁO:")
        for warning in report["warnings"]:
            print(f"    - {warning}")
    else:
        print("\n  không có cảnh báo — mẫu đủ, không lệch, oracle phân biệt được")

    print("\n" + "=" * 70)
    print("ĐƯỜNG CONG THEO VÒNG ĐỜI — truy xuất, theo nhóm")
    print("=" * 70)
    # Each cell shows its denominator. A per-checkpoint family holds about six
    # questions, so one answer moves the rate by 0.167 — and a curve of bare
    # rates invites reading that as a trend. It nearly was: `historical` went
    # 0.333 -> 0.500 between two runs and looked like an improvement, on a
    # single question changing. The integrity check guards the *total* sample
    # per family; the curve is drawn per checkpoint, where the total says
    # nothing.
    families = sorted({f for r in rows for f in r["retrieval"]})
    print(f"  {'mốc':>6}{'ký ức':>8}" + "".join(f"{f[:12]:>14}" for f in families))
    for row in rows:
        line = f"  {row['checkpoint']:>6}{row['memories']:>8}"
        for family in families:
            value = row["retrieval"].get(family)
            n = row["retrieval_n"].get(family, 0)
            line += f"{f'{value:.3f} /{n}':>14}" if value is not None else f"{'-':>14}"
        print(line)
    print("\n  Mỗi ô kèm cỡ mẫu. Với n≈6, một câu đổi làm tỷ lệ nhảy 0.167 —")
    print("  đừng đọc chênh lệch nhỏ giữa hai mốc như một xu hướng.")

    # The aggregate is the number to compare between runs. Each cell above is
    # six questions; the column is thirty-eight.
    print("\n  TỔNG HỢP toàn bộ mốc (đây mới là số để so giữa hai lần chạy):")
    for family in families:
        hit = sum(round(r["retrieval"][family] * r["retrieval_n"][family])
                  for r in rows if family in r["retrieval"])
        n = sum(r["retrieval_n"].get(family, 0) for r in rows)
        if n:
            print(f"    {family:<12} {hit:>3}/{n:<4} = {hit / n:.3f}")

    # Aspect resolution, measured against the slot the question came from.
    #
    # `wrong_slot_rate` is the one that matters: a memory system that answers
    # the right person's wrong predicate, confidently, is worse than one that
    # says it does not know — nobody re-checks a confident answer, and 14 of
    # 34 failures were exactly that.
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    right = wrong = unknown = 0
    for question in all_questions:
        resolved = resolve_aspect(question.text)
        if resolved.predicate is Predicate.UNKNOWN:
            unknown += 1
        elif resolved.predicate.attribute == question.attribute:
            right += 1
        else:
            wrong += 1
    n = max(right + wrong + unknown, 1)
    print("\n" + "=" * 70)
    print("ASPECT RESOLUTION")
    print("=" * 70)
    print(f"  aspect_resolution_accuracy : {right}/{n} = {right / n:.3f}")
    print(f"  wrong_slot_rate            : {wrong}/{n} = {wrong / n:.3f}"
          + ("   <- đúng người, sai vị từ, trả lời tự tin" if wrong else ""))
    print(f"  aspect_unknown_rate        : {unknown}/{n} = {unknown / n:.3f}"
          + ("   (UNKNOWN an toàn hơn đoán sai)" if unknown else ""))

    print("\n" + "=" * 70)
    print("TEMPORAL EXECUTION — toán tử có CHẠY không, tách khỏi đúng/sai")
    print("=" * 70)
    ran = sum(r.get("routes", {}).get("temporal->temporal_operator", 0) for r in rows)
    asked = sum(n for r in rows for k, n in r.get("routes", {}).items()
                if k.startswith("temporal->"))
    print(f"  câu được phân loại TEMPORAL : {asked}")
    print(f"  thực sự chạy toán tử        : {ran}"
          + (f"  ({ran / asked:.1%})" if asked else ""))
    print(f"  rơi về recall               : {asked - ran}")
    if asked and ran < asked:
        print("\n  Rơi về recall nghĩa là toán tử dừng ở một giai đoạn nào đó.")
        print("  Xem `stage_failed` trong các vết lỗi để biết giai đoạn nào —")
        print("  câu trả lời sai vì kho sai, và vì toán tử không chạy, là hai")
        print("  lỗi khác nhau cần hai bản vá khác nhau.")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {"ticks": args.ticks, "subjects": args.subjects,
                   "seed": args.seed, "per_family": args.per_family,
                   "embedder": getattr(embedder, "backend", None),
                   "engine": getattr(engine, "model_id", None)},
        "integrity": report, "checkpoints": rows, "failures": traces,
        "deletions": deletions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  {len(traces)} câu sai đã lưu vết đầy đủ -> {out}")
    adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
