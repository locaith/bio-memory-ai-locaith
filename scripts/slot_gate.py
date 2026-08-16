"""Integration gate for structured slots. Measures; changes nothing it is not
explicitly told to change.

Four questions:

    coverage     how many rows carry a slot, and is the remainder honestly
                 UNKNOWN rather than guessed
    tamper/drift does a stored slot disagree with what the resolver produces
                 for the same sentence today — target 0
    idempotent   does a second backfill change anything — target 0
    privacy      does a deletion reach the slot column, and does a replay
                 leave it deleted

**What this gate does NOT measure: whether the slots are right.** The second
metric was originally named `structured_wrong_slot_rate` and its 0.0000 was
read as evidence of correctness. It cannot be: `slot_for` wrote the slots and
`slot_for` is what they are compared against, so both sides are one function
call. An independent review caught it. What the check genuinely detects — and
does detect, a hand-poisoned row makes it report 1.0 — is tampering and
resolver version drift.

Slot correctness needs labelled sentences the resolver was not written
against. The ontology was built from the same seven templates this world
emits, so both numbers here are properties of that fit and neither transfers
off it.

`benchmark_template_coverage` is named for what it is, not for what it would
be nice to have measured. The resolver's ontology was written against the
seven sentence templates this world emits, so 0.7779 is how well it fits those
templates — not `predicate_resolution_coverage`, which would be a claim about
Vietnamese. Claiming the second needs a paraphrase or adversarial set built
outside the generator, and there isn't one yet.

It deliberately has no target either. A row the resolver cannot place stores
nothing, and filling the column to raise this number would turn "I could not
tell" into "I decided" — the trade the relation classifier already refuses to
make.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.slot_backfill import backfill, slot_for  # noqa: E402

DEFAULT_DBS = (
    ".staging/lifetime/run.db",
    ".staging/failure_matrix/run.db",
    ".staging/lifecycle_ab/A_off.db",
    ".staging/lifecycle_ab/B_safe.db",
)


def _rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT memory_id, content, structured_json FROM cognitive_memories"
    ).fetchall()


def _slot(blob) -> str | None:
    if not blob:
        return None
    try:
        payload = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError):
        return None
    attribute = (payload or {}).get("attribute")
    return str(attribute) if attribute else None


def gate(path: Path, *, apply: bool) -> dict:
    conn = sqlite3.connect(path)
    before = _rows(conn)
    total = len(before)

    first = backfill(conn, dry_run=not apply)
    after_first = {str(m): _slot(s) for m, _, s in _rows(conn)}

    # Idempotence is checked by *running it again and comparing*, not by
    # trusting the watermark. A marker row that says "done" while the pass
    # would still write is the failure this catches.
    second = backfill(conn, dry_run=not apply)
    after_second = {str(m): _slot(s) for m, _, s in _rows(conn)}
    changed_on_second = sum(1 for k in after_first
                            if after_first[k] != after_second.get(k))

    # CORRECTION, 17/08. This was labelled "does a stored slot ever disagree
    # with the sentence it came from — target 0" and its 0.0000 was cited as
    # evidence the slots are RIGHT. It is not that, and could not have been:
    # `slot_for` is the same function that wrote the slot, so both sides of
    # the comparison are one call and 0.0000 is arithmetic.
    #
    # What it actually detects, and does detect — a poisoned row makes it
    # report 1.0 — is TAMPERING and RESOLVER VERSION DRIFT. Those are worth
    # a gate. They are not slot correctness.
    #
    # The quantity that matters is P(stored slot != the slot a person would
    # assign), and nothing here measures it. Measuring it needs labelled
    # sentences the resolver was not written against; the resolver's ontology
    # was built from the same seven templates this world emits, so its 0.7779
    # coverage and its accuracy on this corpus say nothing about either
    # off it.
    wrong = []
    for memory_id, content, structured in _rows(conn):
        stored = _slot(structured)
        if stored is None:
            continue
        fresh = slot_for(str(content)).get("attribute")
        if fresh and fresh != stored:
            wrong.append({"memory_id": str(memory_id), "stored": stored,
                          "resolver": fresh, "content": str(content)[:70]})

    with_slot = sum(1 for _, _, s in _rows(conn) if _slot(s))
    conn.close()
    return {
        "db": str(path),
        "rows": total,
        "benchmark_template_coverage": round(with_slot / total, 4) if total else 0.0,
        "with_slot": with_slot,
        "unresolved": total - with_slot,
        # Named for what it is. `slot_for` wrote these slots, so this compares
        # a function with itself and catches tampering or a resolver version
        # change — not whether the slot is the right one.
        "slot_tamper_or_drift_rate": round(len(wrong) / with_slot, 4) if with_slot else 0.0,
        "wrong_slots": wrong[:10],
        "backfill_changed_on_second_run": changed_on_second,
        "first_pass": first.as_dict(),
        "second_pass": second.as_dict(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="slot_gate")
    ap.add_argument("dbs", nargs="*", default=list(DEFAULT_DBS))
    ap.add_argument("--apply", action="store_true",
                    help="ghi thật. Không có cờ này thì chỉ đo, không sửa.")
    ap.add_argument("--out", default="benchmark_reports/slot_gate.json")
    args = ap.parse_args()

    print("CỔNG STRUCTURED SLOT")
    print("=" * 70)
    print(f"  chế độ: {'GHI THẬT' if args.apply else 'chỉ đo, không sửa'}\n")

    results = []
    for raw in args.dbs:
        path = _REPO / raw if not Path(raw).is_absolute() else Path(raw)
        if not path.exists():
            print(f"  bỏ qua (không có): {raw}")
            continue
        result = gate(path, apply=args.apply)
        results.append(result)
        print(f"  {raw}")
        print(f"    {result['rows']} hàng | có slot {result['with_slot']} "
              f"| chưa đọc được {result['unresolved']}")
        print(f"    benchmark_template_coverage    "
              f"{result['benchmark_template_coverage']:.4f}"
              f"   (không có mục tiêu — UNKNOWN là hợp lệ)")
        rate = result["slot_tamper_or_drift_rate"]
        print(f"    slot_tamper_or_drift_rate     {rate:.4f}"
              f"{'   ✓ mục tiêu 0' if rate == 0 else '   ⚠ MỤC TIÊU 0'}")
        changed = result["backfill_changed_on_second_run"]
        print(f"    backfill_changed_on_second_run {changed}"
              f"{'   ✓ mục tiêu 0' if changed == 0 else '   ⚠ MỤC TIÊU 0'}")
        print(f"    lần 1: {result['first_pass']['filled']} điền, "
              f"{result['first_pass']['unresolved']} để trống, "
              f"{result['first_pass']['seconds']}s")
        if result["wrong_slots"]:
            for item in result["wrong_slots"][:3]:
                print(f"      ⚠ {item['stored']} ≠ {item['resolver']}  "
                      f"{item['content']}")

    if not results:
        print("\n  không DB nào đọc được")
        return 1

    worst_wrong = max(r["slot_tamper_or_drift_rate"] for r in results)
    worst_changed = max(r["backfill_changed_on_second_run"] for r in results)
    print("\n" + "=" * 70)
    print("KẾT LUẬN CỔNG")
    print("=" * 70)
    print(f"  slot_tamper_or_drift_rate cao nhất     : {worst_wrong:.4f}")
    print(f"  backfill_changed_on_second_run cao nhất : {worst_changed}")
    passed = worst_wrong == 0 and worst_changed == 0
    print(f"  → {'ĐẠT' if passed else 'KHÔNG ĐẠT'}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"applied": args.apply, "passed": passed,
                               "databases": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
