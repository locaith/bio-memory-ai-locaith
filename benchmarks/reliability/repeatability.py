"""How much does the same configuration vary between runs?

The baseline measured 4p+4w at 10,000 events and got 1,164 events/s. The scale
run measured the identical configuration minutes later and got 328/s — a
factor of 3.5 on the same machine, same code, same corpus.

Quoting either number as "the throughput" would be wrong. This runs one
configuration N times back to back and reports the spread, so the report can
state a range and size the operating envelope from the low end rather than
from the luckiest run.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.reliability import environment, workloads                # noqa: E402


def _spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {}
    return {
        "n": len(values),
        "min": round(min(values), 1),
        "median": round(statistics.median(values), 1),
        "mean": round(statistics.fmean(values), 1),
        "max": round(max(values), 1),
        "stdev": round(statistics.stdev(values), 1) if len(values) > 1 else 0.0,
        "max_over_min": round(max(values) / min(values), 2) if min(values) > 0 else None,
        "samples": [round(v, 1) for v in values],
    }


def run(*, events: int = 10_000, producers: int = 4, workers: int = 4,
        repeats: int = 5, settle_seconds: float = 5.0) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for i in range(repeats):
        run_dir = Path(tempfile.mkdtemp(prefix=f"bench_repeat{i}_"))
        started = time.time()
        try:
            result = workloads.workload_c_mixed(
                run_dir=run_dir, events=events, producers=producers,
                workers=workers, sample_interval=1.0,
            )
        finally:
            pass
        latency = result["latency"]["end_to_end_visibility"]
        runs.append({
            "run": i,
            "wall_clock_start": started,
            "producer_throughput_per_s": result["producer_throughput_per_s"],
            "worker_throughput_per_s": result["worker_throughput_per_s"],
            "total_throughput_per_s": round(
                result["producer_throughput_per_s"] + result["worker_throughput_per_s"], 1
            ),
            "p50_visibility_ms": latency.get("p50_ms"),
            "p95_visibility_ms": latency.get("p95_ms"),
            "p99_visibility_ms": latency.get("p99_ms"),
            "peak_queue_depth": result["peak_queue_depth"],
            "drain_after_stop_s": result["queue_drain_seconds_after_producers_stop"],
            "drained": result["queue_drained"],
            "lock_errors": result["lock_errors"],
            "doctor_exit": result["doctor_after"]["exit_code"],
        })
        print(f"   run {i}: produce={runs[-1]['producer_throughput_per_s']:.1f}/s  "
              f"project={runs[-1]['worker_throughput_per_s']:.1f}/s  "
              f"p95={runs[-1]['p95_visibility_ms']}ms  "
              f"peak_queue={runs[-1]['peak_queue_depth']}", flush=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        # Let the filesystem settle: deleting a 30MB WAL set while the next run
        # is starting would make the next run pay for this one's cleanup.
        time.sleep(settle_seconds)

    return {
        "configuration": {"events": events, "producers": producers, "workers": workers},
        "repeats": repeats,
        "environment": environment.capture(repo=_REPO),
        "runs": runs,
        "producer_throughput_per_s": _spread([r["producer_throughput_per_s"] for r in runs]),
        "worker_throughput_per_s": _spread([r["worker_throughput_per_s"] for r in runs]),
        "p95_visibility_ms": _spread([r["p95_visibility_ms"] for r in runs]),
        "peak_queue_depth": _spread([float(r["peak_queue_depth"]) for r in runs]),
        "all_drained": all(r["drained"] for r in runs),
        "all_doctor_clean": all(r["doctor_exit"] == 0 for r in runs),
        "total_lock_errors": sum(r["lock_errors"] for r in runs),
    }


def main() -> int:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"  repeating 4p+4w at 10,000 events, {repeats} times", flush=True)
    result = run(repeats=repeats)
    out = _REPO / "reports" / "v082" / "reliability_repeatability.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for key in ("producer_throughput_per_s", "worker_throughput_per_s",
                "p95_visibility_ms", "peak_queue_depth"):
        spread = result[key]
        print(f"\n  {key}")
        print(f"    min={spread['min']}  median={spread['median']}  max={spread['max']}  "
              f"stdev={spread['stdev']}  max/min={spread['max_over_min']}x")
    print(f"\n  all drained: {result['all_drained']}   "
          f"all doctor clean: {result['all_doctor_clean']}   "
          f"lock errors: {result['total_lock_errors']}")
    print(f"  written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
