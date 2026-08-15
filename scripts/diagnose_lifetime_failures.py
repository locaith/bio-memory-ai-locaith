"""Group failures by what went wrong semantically, not by which file they hit.

`historical = 0.368` says where it hurts. It does not say whether the engine
failed to notice a supersession, treated a correction as one, killed two claims
that could both be true, or simply picked the wrong interval — and those need
four different fixes.

Each failed question is re-derived against the ledger: what the world did to
that slot before the question was asked, and what the system returned. The
classification comes from the comparison, never from the trace's own opinion.
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

from bio_agent_os.evals.lifetime_world import EventKind, generate  # noqa: E402


#: subject_id -> name, filled once the world is generated.
_NAME_OF: dict[str, str] = {}


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


CAUSES = (
    # resolution
    "resolver_subject",        # never identified who the question is about
    "resolver_aspect",         # right person, wrong slot
    "ambiguous_subject",       # two people could answer; picked the other one
    # history and intervals
    "no_history",              # the store held nothing for that slot
    "interval_wrong",          # history right, window picked wrong
    "ranking_wrong",           # the right claim was retrieved but not chosen
    "belief_time_mismatch",    # answered from a tick the system had not reached
    # lifecycle relations
    "supersede_missed",        # a replacement happened and the old value won
    "correct_as_supersede",    # a retracted value answered a historical question
    "coexist_killed",          # two claims that can both hold, one dropped
    "conflict_unresolved",     # sources disagree and one was picked silently
    # deletion, split by the layer the value survived in
    "forget_scope_no_match",           # the request matched nothing
    "forget_primary_survives",         # the row itself is still there
    "forget_embedding_survives",       # vector outlived the row
    "forget_label_survives",           # hippocampus label outlived the row
    "forget_consolidation_survives",   # a merged memory still quotes it
    "forget_event_survives",           # the append-only log still holds it
    "forget_new_fact_same_value",      # a *later* fact reuses the value
    "grader_false_positive",           # nothing actually leaked
    # everything else
    "synthesis",               # everything right, answer text wrong
    "unsupported_query_type",  # the shape has no operator at all
    "unclassified",
)

#: Which bucket a surviving value maps to, by the store it survived in.
_LEAK_LAYER = {
    "cognitive_memories.content": "forget_primary_survives",
    "cognitive_memories.metadata_json": "forget_consolidation_survives",
    "cognitive_events.payload_json": "forget_event_survives",
    "shadow_memories.content": "forget_primary_survives",
    "hippocampus_labels.topic": "forget_label_survives",
}


def _classify_leak(trace: dict, events, ledger) -> str:
    """A leak is at least six defects wearing one name. Name the layer.

    The order matters. "A later fact happens to reuse the deleted value" is
    checked first, because it is not a leak at all — the deletion worked and
    the world simply said the same thing again, which a substring grader cannot
    tell apart from a survival.
    """
    slot = (trace.get("subject_id"), trace.get("attribute"))
    tick = trace["checkpoint"]
    forbidden = _fold(trace.get("forbidden") or "")

    deletions = trace.get("_deletions") or []
    mine = [d for d in deletions if d.get("slot") == f"{slot[0]}/{slot[1]}"]
    if mine and not any(d.get("deleted_claims") for d in mine):
        return "forget_scope_no_match"

    # Did the world re-assert this value *after* the deletion?
    when_deleted = min((d.get("tick", 0) for d in mine), default=0)
    later = [e for e in events
             if (e.subject_id, e.attribute) == slot and e.value
             and when_deleted < e.tick <= tick
             and _fold(e.value) == forbidden]
    if later:
        return "forget_new_fact_same_value"

    stores = trace.get("leak_store") or {}
    if not stores:
        return "grader_false_positive"
    for where in ("cognitive_memories.content", "shadow_memories.content",
                  "cognitive_memories.metadata_json", "hippocampus_labels.topic",
                  "cognitive_events.payload_json"):
        if where in stores:
            return _LEAK_LAYER[where]
    return "forget_embedding_survives"


def classify(trace: dict, events, ledger) -> str:
    slot = (trace.get("subject_id"), trace.get("attribute"))
    tick = trace["checkpoint"]

    stage = trace.get("stage_failed") or ""
    if stage == "resolve_subject":
        return "resolver_subject"
    if stage == "claim_history":
        return "no_history"
    if stage == "interval_selection":
        return "interval_wrong"

    retrieved = _fold(" ".join(trace.get("retrieved") or []))
    wanted = _fold(trace.get("value") or "")
    forbidden = _fold(trace.get("forbidden") or "")

    if forbidden and forbidden in retrieved:
        return _classify_leak(trace, events, ledger)
    if not retrieved:
        return "no_history"

    # What the world did to this slot before the question.
    history = [e for e in events
               if (e.subject_id, e.attribute) == slot and e.tick <= tick
               and e.value]
    kinds = {e.kind for e in history}

    if wanted and wanted in retrieved:
        # The right value was there and the answer still failed.
        return "ranking_wrong" if trace.get("retrieval_hit") is False else "synthesis"

    # The right value was not retrieved at all. Which shape of change was it?
    values = [_fold(e.value) for e in history]
    if wanted and wanted not in values:
        return "resolver_aspect"        # the slot never held it — wrong slot

    # Whose sentence came back, and about what? Answering with the right
    # person's wrong aspect and answering with a different person entirely are
    # different bugs, and both were hiding in `unclassified`.
    name = _fold(_NAME_OF.get(slot[0], ""))
    if name:
        subject_words = set(name.split())
        got_words = set(retrieved.split())
        if not subject_words <= got_words:
            return "ambiguous_subject"
        slot_values = {_fold(e.value) for e in events
                       if (e.subject_id, e.attribute) == slot and e.value}
        if slot_values and not any(v in retrieved for v in slot_values):
            return "resolver_aspect"

    # What was returned decides the name, not what merely happened in the slot.
    #
    # A disputed value winning is `conflict_unresolved`, whatever else the
    # slot has been through. Checking SUPERSEDE first put two cases under
    # "supersede missed" whose answers came from a CONTRADICT event — the
    # engine had not failed to notice a replacement, it had silently picked a
    # side in a disagreement, which is a different missing capability.
    returned_from = {e.kind for e in history
                     if e.value and _fold(e.value) in retrieved}
    if EventKind.CONTRADICT in returned_from:
        return "conflict_unresolved"
    if EventKind.CORRECT in returned_from:
        return "correct_as_supersede"

    if EventKind.CORRECT in kinds:
        return "correct_as_supersede"
    if EventKind.SUPERSEDE in kinds:
        return "supersede_missed"
    if EventKind.CONTRADICT in kinds:
        return "coexist_killed"
    if EventKind.REPEAT in kinds:
        return "ranking_wrong"
    return "unclassified"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default="benchmark_reports/lifetime_2026_08_15_diag.json")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args()

    data = json.loads((_REPO / args.report).read_text(encoding="utf-8"))
    config = data["config"]
    events, ledger, people = generate(ticks=config["ticks"],
                                      subjects=config["subjects"],
                                      seed=config["seed"])
    _NAME_OF.update({p.subject_id: p.name for p in people})
    fails = data["failures"]
    deletions = data.get("deletions") or []

    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for trace in fails:
        trace["_deletions"] = deletions
        buckets[classify(trace, events, ledger)].append(trace)

    print(f"{len(fails)} câu sai, phân theo NGUYÊN NHÂN NGỮ NGHĨA\n")
    print(f"  {'nguyên nhân':<24}{'số':>5}{'tỷ lệ':>9}   nhóm câu hỏi")
    print("  " + "-" * 66)
    for cause in CAUSES:
        items = buckets.get(cause) or []
        if not items:
            continue
        families = collections.Counter(i["family"] for i in items)
        share = len(items) / len(fails)
        detail = " ".join(f"{k}:{v}" for k, v in families.most_common())
        print(f"  {cause:<24}{len(items):>5}{share:>8.0%}   {detail}")

    groups = {
        "VÒNG ĐỜI (supersede/correct/coexist/conflict)": (
            "supersede_missed", "correct_as_supersede", "coexist_killed",
            "conflict_unresolved"),
        "PHÂN GIẢI (subject/aspect)": (
            "resolver_subject", "resolver_aspect", "ambiguous_subject"),
        "XOÁ — lỗi SẢN PHẨM": (
            "forget_scope_no_match", "forget_primary_survives",
            "forget_embedding_survives", "forget_label_survives",
            "forget_consolidation_survives", "forget_event_survives"),
        "XOÁ — lỗi PHÉP ĐO": (
            "forget_new_fact_same_value", "grader_false_positive"),
        "KHOẢNG THỜI GIAN": ("interval_wrong", "ranking_wrong",
                             "belief_time_mismatch"),
    }
    print()
    for label, causes in groups.items():
        n = sum(len(buckets.get(c) or []) for c in causes)
        if n:
            print(f"  {label:<46}{n:>3}/{len(fails)} = {n / len(fails):.0%}")

    left = len(buckets.get("unclassified") or [])
    if left:
        print(f"\n  ⚠  CÒN {left} LỖI CHƯA PHÂN LOẠI — chưa được kết luận gì")
    else:
        print("\n  không còn lỗi nào chưa phân loại")

    print("\n" + "=" * 70)
    print("TÁI HIỆN TỐI THIỂU")
    print("=" * 70)
    for cause in CAUSES:
        items = buckets.get(cause) or []
        if not items:
            continue
        print(f"\n── {cause} ({len(items)}) ──")
        for trace in items[:args.examples]:
            print(f"  mốc {trace['checkpoint']} | {trace['family']} | "
                  f"{trace['route']}")
            print(f"    hỏi   : {trace['question'][:82]}")
            print(f"    cần   : {trace['value']!r}"
                  + (f"  CẤM: {trace['forbidden']!r}" if trace.get("forbidden") else ""))
            got = (trace.get("retrieved") or ["(rỗng)"])[0]
            print(f"    trả về: {got[:82]}")
            if trace.get("note"):
                print(f"    ghi   : {trace['note'][:78]}")
            # The world's own record for that slot. A bucket nobody can explain
            # is a bucket nobody should act on, and the explanation is here
            # rather than in the trace's opinion of itself.
            slot = (trace.get("subject_id"), trace.get("attribute"))
            history = [e for e in events
                       if (e.subject_id, e.attribute) == slot
                       and e.tick <= trace["checkpoint"]]
            print(f"    thế giới đã làm gì với ô {slot[0]}/{slot[1]}:")
            for e in history[-5:]:
                print(f"        T{e.tick:<5}{e.kind.value:<11}{str(e.value)[:34]}")
            if not history:
                print("        (không sự kiện nào)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
