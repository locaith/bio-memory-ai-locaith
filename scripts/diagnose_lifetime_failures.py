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


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).lower().split())


CAUSES = (
    "resolver_subject",        # never identified who the question is about
    "resolver_aspect",         # right person, wrong slot
    "no_history",              # the store held nothing for that slot
    "supersede_missed",        # a replacement happened and the old value won
    "correct_as_supersede",    # a retracted value answered a historical question
    "coexist_killed",          # two claims that can both hold, one dropped
    "interval_wrong",          # history right, window picked wrong
    "ranking_wrong",           # the right claim was retrieved but not chosen
    "forget_leak",             # deleted content came back
    "synthesis",               # everything right, answer text wrong
    "unclassified",
)


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
        return "forget_leak"
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

    if EventKind.CORRECT in kinds:
        return "correct_as_supersede"
    if EventKind.SUPERSEDE in kinds:
        return "supersede_missed"
    if EventKind.CONTRADICT in kinds:
        return "coexist_killed"
    return "unclassified"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default="benchmark_reports/lifetime_2026_08_15_diag.json")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args()

    data = json.loads((_REPO / args.report).read_text(encoding="utf-8"))
    config = data["config"]
    events, ledger, _ = generate(ticks=config["ticks"],
                                 subjects=config["subjects"],
                                 seed=config["seed"])
    fails = data["failures"]

    buckets: dict[str, list[dict]] = collections.defaultdict(list)
    for trace in fails:
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

    lifecycle = sum(len(buckets.get(c) or []) for c in
                    ("supersede_missed", "correct_as_supersede", "coexist_killed"))
    print(f"\n  nhóm VÒNG ĐỜI (supersede/correct/coexist): {lifecycle}/{len(fails)}"
          f" = {lifecycle / len(fails):.0%}")
    resolver = sum(len(buckets.get(c) or []) for c in
                   ("resolver_subject", "resolver_aspect"))
    print(f"  nhóm PHÂN GIẢI (subject/aspect)          : {resolver}/{len(fails)}"
          f" = {resolver / len(fails):.0%}")

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
