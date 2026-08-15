"""Where does an answer first go wrong?

`resolver_aspect` carried 11 failures while `aspect_resolution_accuracy` was
136/136 and `wrong_slot_rate` was 0. Both cannot be true, so the label was
lying — it named a module that had already been fixed, because the classifier
guessed from the shape of the output rather than watching the pipeline.

This walks each failed question through the stages in order and records the
**first** one whose result diverges from the ledger:

    subject resolution     is it about the right person
    aspect resolution      is it about the right predicate
    candidate generation   is the answer among the memories considered
    claim_history          did it survive into the ordered spans
    temporal filtering     was the span covering this tick chosen
    lifecycle relation     supersede / correct / repeat read correctly
    ranking                is the right span first
    answer synthesis       does the text contain it

A stage is only judged when every stage before it was right. Otherwise the
report would blame the last thing that touched a value that was already wrong,
which is exactly how 11 failures came to be filed under a resolver that had
stopped failing.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import unicodedata
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect  # noqa: E402
from bio_agent_os.cognitive.temporal_operator import (  # noqa: E402
    answer_temporal, claim_history, parse_temporal,
)
from bio_agent_os.evals.lifetime_adapter import CognitiveAdapter  # noqa: E402
from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402

STAGES = (
    "subject_resolution",
    "aspect_resolution",
    "candidate_generation",
    "claim_history_selection",
    "temporal_interval",
    "lifecycle_relation",
    "ranking",
    "answer_synthesis",
)

#: The six buckets the reclassification must land in.
BUCKETS = {
    "subject_resolution": "other_new_class",
    "aspect_resolution": "other_new_class",
    "candidate_generation": "claim_history_selection",
    "claim_history_selection": "claim_history_selection",
    "temporal_interval": "temporal_interval",
    "lifecycle_relation": "lifecycle_relation",
    "ranking": "claim_history_selection",
    "answer_synthesis": "answer_synthesis",
}


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


def trace(adapter, trace_row: dict, *, name: str, attribute: str,
          events) -> dict:
    """Walk the stages, stop at the first divergence."""
    question = trace_row["question"]
    wanted = _fold(trace_row.get("value") or "")
    tick = trace_row["checkpoint"]
    result: dict = {"stages": {}, "first_wrong_stage": None, "note": ""}

    def mark(stage: str, ok: bool | None, detail: str = "") -> bool:
        result["stages"][stage] = {"ok": ok, "detail": detail}
        if ok is False and result["first_wrong_stage"] is None:
            result["first_wrong_stage"] = stage
        return ok is not False

    intent = parse_temporal(question)
    if not mark("subject_resolution",
                bool(intent.subject) and all(
                    w.lower() in _fold(intent.subject) for w in name.split()),
                f"{intent.subject!r} vs {name!r}"):
        return result

    resolved = resolve_aspect(question)
    if not mark("aspect_resolution",
                resolved.predicate is not Predicate.UNKNOWN
                and resolved.predicate.attribute == attribute,
                f"{resolved.predicate.name} vs {attribute}"):
        return result

    conn = adapter.memory_os.memories.conn
    rows = conn.execute("SELECT content FROM cognitive_memories").fetchall()
    in_store = [c for (c,) in rows if wanted and wanted in _fold(c)
                and all(w.lower() in _fold(c) for w in name.split())]
    if not in_store:
        result["note"] = ("câu trả lời không hề có trong kho ở mốc này — "
                          "câu hỏi không trả lời được")
        mark("candidate_generation", False, "kho không chứa đáp án")
        result["bucket"] = "benchmark_invalid"
        return result
    mark("candidate_generation", True, f"{len(in_store)} ký ức chứa đáp án")

    spans = claim_history(adapter.memory_os, subject=intent.subject,
                          aspect=intent.aspect, context=None)
    carried = [s for s in spans if wanted in _fold(s.content)]
    if not mark("claim_history_selection", bool(carried),
                f"{len(spans)} span, {len(carried)} mang đáp án"):
        return result

    answer = answer_temporal(adapter.memory_os, question, context=None)
    if not answer.executed:
        mark("temporal_interval", False,
             f"{answer.stage_failed}: {answer.note[:60]}")
        return result

    chosen_has_it = wanted in _fold(answer.answer_text)
    if chosen_has_it:
        mark("temporal_interval", True, "chọn đúng khoảng")
        mark("lifecycle_relation", True)
        mark("ranking", True)
        mark("answer_synthesis", False, "đúng span nhưng câu trả lời sai")
        return result

    # The right span existed and a different one was returned. Which relation
    # produced the one that won?
    returned = _fold(answer.answer_text)
    kinds = {e.kind for e in events
             if e.value and _fold(e.value) in returned and e.tick <= tick}
    if kinds & {EventKind.CORRECT, EventKind.SUPERSEDE, EventKind.CONTRADICT}:
        mark("temporal_interval", True, "khoảng hợp lệ")
        mark("lifecycle_relation", False,
             f"trả về giá trị từ {sorted(k.value for k in kinds)}")
        return result

    mark("temporal_interval", False,
         f"span đúng có mặt nhưng chọn: {answer.answer_text[:56]}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default="benchmark_reports/lifetime_2026_08_15_step2b.json")
    ap.add_argument("--only", default="resolver_aspect",
                    help="nhãn cũ cần tái phân loại")
    args = ap.parse_args()

    sys.path.insert(0, str(_REPO / "scripts"))
    from diagnose_lifetime_failures import _NAME_OF, classify

    data = json.loads((_REPO / args.report).read_text(encoding="utf-8"))
    config = data["config"]
    events, ledger, people = generate(ticks=config["ticks"],
                                      subjects=config["subjects"],
                                      seed=config["seed"])
    _NAME_OF.update({p.subject_id: p.name for p in people})
    deletions = data.get("deletions") or []

    targets = []
    for row in data["failures"]:
        row["_deletions"] = deletions
        if classify(row, events, ledger) == args.only:
            targets.append(row)

    print(f"{len(targets)} lỗi mang nhãn {args.only!r}, "
          f"đi lại từng giai đoạn\n")

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    embedder = Embedder()
    results = []
    workdir = _REPO / ".staging" / "stagetrace"
    workdir.mkdir(parents=True, exist_ok=True)

    for checkpoint in sorted({t["checkpoint"] for t in targets}):
        adapter = CognitiveAdapter(workdir / f"cp{checkpoint}.db",
                                   embedder=embedder)
        adapter.reset()
        for event in events[:checkpoint]:
            if event.kind is not EventKind.FORGET:
                adapter.ingest(event)
        while backfill_embeddings(adapter.memory_os.memories.conn,
                                  embedder, limit=200):
            pass

        for row in [t for t in targets if t["checkpoint"] == checkpoint]:
            name = _NAME_OF.get(row.get("subject_id"), "")
            out = trace(adapter, row, name=name,
                        attribute=row.get("attribute", ""), events=events)
            out["question"] = row["question"]
            out["value"] = row.get("value")
            out["checkpoint"] = checkpoint
            out.setdefault("bucket",
                           BUCKETS.get(out["first_wrong_stage"] or "",
                                       "other_new_class"))
            results.append(out)
        adapter.close()

    print("=" * 72)
    print("PIPELINE FAILURE DISTRIBUTION")
    print("=" * 72)
    stages = collections.Counter(r["first_wrong_stage"] for r in results)
    for stage in STAGES:
        print(f"  {stage:<26}{stages.get(stage, 0)}")
    unknown = sum(1 for r in results if r["first_wrong_stage"] is None)
    if unknown:
        print(f"  {'(không xác định)':<26}{unknown}")

    print("\n" + "=" * 72)
    print("NHÃN MỚI")
    print("=" * 72)
    for bucket, n in collections.Counter(r["bucket"] for r in results).most_common():
        print(f"  {n:>3}  {bucket}")

    print("\n" + "=" * 72)
    print("TÁI HIỆN TỐI THIỂU")
    print("=" * 72)
    for result in results:
        print(f"\n  mốc {result['checkpoint']} | {result['bucket']} | "
              f"first_wrong_stage = {result['first_wrong_stage']}")
        print(f"    hỏi : {result['question'][:78]}")
        print(f"    cần : {result['value']!r}")
        for stage in STAGES:
            entry = result["stages"].get(stage)
            if entry is None:
                continue
            flag = {True: "ok  ", False: "SAI ", None: "n/a "}[entry["ok"]]
            print(f"      {flag}{stage:<26}{entry['detail'][:44]}")
        if result.get("note"):
            print(f"    ghi : {result['note']}")

    out = _REPO / "benchmark_reports" / "pipeline_stages_2026_08_15.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
