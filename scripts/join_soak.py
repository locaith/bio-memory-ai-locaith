"""Does adding the label projection make the write path worse?

Section 2.2 of the join plan sets the bar, and it is a bar about *not* getting
worse rather than about getting better:

    observe p95 and max   must not degrade
    WAL                   must not be pushed up
    queue lag             measured with a second consumer present
    dead letters          measured

This measures exactly that and nothing else. **No model runs here.** The model
half costs 20 s an event and this machine's power supply has not been replaced,
so nothing that loads the GPU is allowed to run unattended overnight — that rule
is the owner's, it came from three hard power-offs, and a measurement is not
worth a fourth. What runs here is the deterministic builder, which is the half
that actually sits on the write path.

**A/B, alternating, not sequential.** Each cycle runs a baseline window and a
joined window against a fresh database, and alternates which goes first. A
machine that slows down over 36 hours would otherwise hand the second arm a
penalty it did not earn, and this run is long enough for that to matter.

Every cycle appends to the output file as it completes, so a kill at any point
leaves real partial results rather than nothing.

    python scripts/join_soak.py --hours 36 --events 4000
    python scripts/join_soak.py --hours 0.05 --events 300      # smoke
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("BIO_AGENT_TENANT_ID", "soak")
os.environ.setdefault("BIO_AGENT_WORKSPACE_ID", "join")

BODY = (
    "Hợp đồng với đối tác ký ngày 12/03/2026, giá trị 84.000.000 đồng, "
    "thanh toán làm hai đợt, nghiệm thu trước cuối quý."
)
JUNK = "oke em"

# There used to be a BIO_AGENT_HIPPOCAMPUS_LABEL switch here, because labels
# were enqueued inside observe(). They are not any more — the soak measured that
# design at +0.196 ms p95 on every write — so there is nothing left to switch.


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 4)


def _wal_bytes(db: Path) -> int:
    wal = db.with_suffix(db.suffix + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def run_window(db: Path, *, with_label: bool, events: int,
               drain: bool) -> dict[str, Any]:
    """One arm: write `events` observations, then optionally drain the queue.

    `with_label` no longer changes what `observe()` does — that is the whole
    point of the redesign. Labels are enqueued by `backfill_labels()` *after*
    the write window closes, so the latency being compared is the latency of an
    identical write path in both arms. If the two arms now differ, the
    difference is measurement noise and not the feature.
    """
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.hippocampus_label import (
        backfill_labels, pending_count, unlabelled_count,
    )
    from bio_agent_os.cognitive.reconciliation_worker import worker_for

    memory_os = MemoryOS(db, projection_mode="shadow")
    latencies: list[float] = []
    wal_peak = 0

    for index in range(events):
        content = JUNK if index % 7 == 0 else f"{BODY} #{index}"
        started = time.perf_counter()
        memory_os.observe(tenant_id="soak", actor="a", source="u",
                          content=content, workspace_id="join")
        latencies.append((time.perf_counter() - started) * 1000)
        if index % 200 == 0:
            wal_peak = max(wal_peak, _wal_bytes(db))

    wal_peak = max(wal_peak, _wal_bytes(db))

    # Off the write path, after the window that is being timed. Bounded and
    # looped rather than unbounded: an unbounded scan on a large database holds
    # a read snapshot, and a held read snapshot is what ended canary runs 8
    # and 9.
    backfill: dict[str, Any] = {}
    if with_label:
        started = time.perf_counter()
        enqueued = 0
        while True:
            batch = backfill_labels(memory_os.events.conn, memory_os.events.outbox,
                                    limit=1_000)
            enqueued += batch
            if batch == 0:
                break
        backfill = {
            "backfill_seconds": round(time.perf_counter() - started, 3),
            "backfill_enqueued": enqueued,
            "still_unlabelled": unlabelled_count(memory_os.events.conn),
        }

    drained: dict[str, Any] = {}
    if drain:
        worker = worker_for(memory_os)
        started = time.perf_counter()
        # Bounded: a queue that will not drain is a result, not a reason to
        # spin here until the deadline.
        for _ in range(events * 3):
            if worker.run_once() is None:
                break
        metrics = worker.metrics
        drained = {
            "drain_seconds": round(time.perf_counter() - started, 2),
            "completed": metrics.completed,
            "skipped": metrics.skipped,
            "failed": metrics.failed,
            "dead_lettered": metrics.dead_lettered,
            "labels_pending": pending_count(memory_os.memories.conn),
        }

    memory_os.close()
    return {
        "arm": "joined" if with_label else "baseline",
        "events": events,
        "observe_ms": {
            "p50": _pct(latencies, 0.50),
            "p95": _pct(latencies, 0.95),
            "p99": _pct(latencies, 0.99),
            "max": round(max(latencies), 4),
            "mean": round(statistics.fmean(latencies), 4),
        },
        "wal_peak_bytes": wal_peak,
        **backfill,
        **drained,
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="join_soak")
    ap.add_argument("--hours", type=float, default=36.0)
    ap.add_argument("--events", type=int, default=4_000,
                    help="observations per arm per cycle")
    ap.add_argument("--drain", action="store_true", default=True)
    ap.add_argument("--no-drain", dest="drain", action="store_false")
    ap.add_argument("--rest", type=float, default=3.0,
                    help="seconds between cycles; keeps a 36-hour run from "
                         "pinning a core flat out and turning the measurement "
                         "into a thermal-throttling curve")
    ap.add_argument("--aa", action="store_true",
                    help="run BOTH arms as baseline. This measures the noise "
                         "floor — what 'no difference' looks like on this "
                         "machine — so a threshold can be derived from data "
                         "instead of picked because it is a round number. The "
                         "previous run failed a 0.10 ms bar that was chosen "
                         "before anyone knew what 0.10 ms meant here.")
    ap.add_argument("--out", default="reports/join_soak.json")
    args = ap.parse_args()

    deadline = time.time() + args.hours * 3600
    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "deadline_hours": args.hours,
        "events_per_arm": args.events,
        "model_used": False,
        "note": "no model runs here; the PSU has not been replaced and nothing "
                "GPU-bound may run unattended overnight",
        "cycles": [],
    }

    cycle = 0
    workdir = Path(tempfile.mkdtemp(prefix="join-soak-"))
    try:
        while time.time() < deadline:
            cycle += 1
            # Alternate which arm goes first so a machine that drifts over the
            # run cannot hand one arm a penalty it did not earn.
            order = [False, True] if cycle % 2 else [True, False]
            if args.aa:
                order = [False, False]
            record: dict[str, Any] = {"cycle": cycle,
                                      "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                      "first": "joined" if order[0] else "baseline",
                                      "arms": []}
            for slot, with_label in enumerate(order):
                if time.time() >= deadline:
                    break
                db = workdir / f"c{cycle}-{slot}-{'j' if with_label else 'b'}.db"
                try:
                    arm = run_window(db, with_label=with_label,
                                     events=args.events, drain=args.drain)
                    # In A/A both arms are baseline, so they need distinct
                    # names or the comparison collapses into one bucket.
                    if args.aa:
                        arm["arm"] = "baseline" if slot == 0 else "joined"
                    record["arms"].append(arm)
                except Exception as exc:               # a cycle may fail; the run continues
                    record["arms"].append({
                        "arm": "joined" if with_label else "baseline",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                finally:
                    for suffix in ("", "-wal", "-shm"):
                        target = Path(str(db) + suffix)
                        if target.exists():
                            target.unlink(missing_ok=True)

            payload["cycles"].append(record)
            payload["cycles_completed"] = cycle
            payload["last_write"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Written every cycle: a kill at any point must leave real partial
            # results rather than nothing.
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")

            arms = {a.get("arm"): a for a in record["arms"]}
            base, join = arms.get("baseline", {}), arms.get("joined", {})
            if "observe_ms" in base and "observe_ms" in join:
                print(f"  cycle {cycle:>3}  p95 base {base['observe_ms']['p95']:>7.3f} "
                      f"-> joined {join['observe_ms']['p95']:>7.3f} ms   "
                      f"max {base['observe_ms']['max']:>8.2f} -> "
                      f"{join['observe_ms']['max']:>8.2f}", flush=True)

            if args.rest and time.time() < deadline:
                time.sleep(args.rest)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        payload["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")

    print(f"\n  {args.out}  ({cycle} cycles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
