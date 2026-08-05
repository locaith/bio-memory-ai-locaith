"""Long-running mixed load, sampled once a minute.

A one-minute benchmark cannot show a leak, a drift or a queue that grows
slightly faster than it drains. Those only appear over hours, and they are the
failure modes that take a system down in production rather than in a test.

Everything is written incrementally to a JSONL time series, so a soak that is
interrupted at hour four still yields four hours of evidence.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402

from benchmarks.reliability import corpus, environment, harness        # noqa: E402
from benchmarks.reliability.metrics import Histogram, read_samples     # noqa: E402
from benchmarks.reliability.workloads import DEFAULT_SEED, doctor_digest, run_doctor  # noqa: E402


def soak_producer_main(spec: dict[str, Any]) -> None:
    """Append continuously at a target rate until told to stop."""
    stop = spec["stop_event"]
    runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
    latency = Histogram("soak.append")
    appended = 0
    lock_errors = 0
    failures = 0
    restarts = 0
    interval = 1.0 / max(1e-9, float(spec.get("target_rate", 200)))
    offset = int(spec["index"]) * 10_000_000
    cycle = 0
    started = time.time()

    while not stop.is_set():
        batch = list(corpus.generate(500, seed=DEFAULT_SEED + cycle, start=offset + cycle * 500))
        cycle += 1
        for obs in batch:
            if stop.is_set():
                break
            t0 = time.perf_counter()
            try:
                runtime.observe(
                    tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                    content=obs.content, workspace_id=obs.workspace_id,
                )
                appended += 1
            except Exception as exc:
                failures += 1
                if harness.is_lock_error(exc):
                    lock_errors += 1
            latency.add((time.perf_counter() - t0) * 1000)
            elapsed = time.perf_counter() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)
        # Reopen periodically: a connection that is never recycled hides a
        # connection leak, and production processes do get restarted.
        if spec.get("recycle_every_cycles") and cycle % int(spec["recycle_every_cycles"]) == 0:
            runtime.close()
            runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
            restarts += 1

    peak = environment.peak_rss_bytes()
    runtime.close()
    Path(spec["out"]).write_text(json.dumps({
        "role": "soak_producer", "index": spec["index"], "appended": appended,
        "failures": failures, "lock_errors": lock_errors, "runtime_restarts": restarts,
        "elapsed_s": time.time() - started, "latency": latency.as_dict(),
        "peak_rss_bytes": peak,
    }, ensure_ascii=False), encoding="utf-8")


def run(
    *,
    run_dir: Path,
    seconds: float,
    producers: int = 2,
    workers: int = 3,
    target_rate: float = 150.0,
    mode: str = "shadow",
    shadow: bool = False,
    sample_interval: float = 60.0,
    worker_restart_every: float = 900.0,
    doctor_every: int = 5,
) -> dict[str, Any]:
    work = run_dir / "soak"
    work.mkdir(parents=True, exist_ok=True)
    db = str(work / "soak.db")
    series_path = run_dir / "soak_timeseries.jsonl"
    MemoryOS(db, projection_mode=mode).close()

    doctor_before = doctor_digest(run_doctor(db, deep=True))

    ctx = __import__("multiprocessing").get_context("spawn")
    # Two events, not one. Sharing a single stop event tells the workers to
    # exit at the same moment the producers do, and the drain check that
    # follows then waits for a queue nobody is draining. The first version of
    # this function did exactly that and reported `drained=False` for two
    # leftover jobs out of 366,715 - a harness defect that looked like a
    # kernel defect.
    producer_stop = ctx.Event()
    worker_stop = ctx.Event()
    producer_specs = [
        {
            "db": db, "mode": mode, "index": i, "stop_event": producer_stop,
            "target_rate": target_rate / producers, "out": str(work / f"soak_producer_{i}.json"),
            "recycle_every_cycles": 20,
        }
        for i in range(producers)
    ]
    producer_procs = harness.spawn(soak_producer_main, producer_specs)

    def _start_workers(generation: int) -> tuple[list[Any], list[dict[str, Any]]]:
        gate = ctx.Event()
        specs = [
            {
                "db": db, "mode": mode, "worker_id": f"g{generation}w{i}", "shadow": shadow,
                "out": str(work / f"worker_g{generation}_{i}.json"), "start_event": gate,
                "stop_event": worker_stop, "batch_size": 50, "idle_polls": 10**9,
                "stay_alive": True, "max_seconds": seconds + 300, "poll_seconds": 0.05,
                "lease_seconds": 60.0, "flush_samples_every": 5.0,
            }
            for i in range(workers)
        ]
        procs = harness.spawn(harness.worker_main, specs)
        gate.set()
        return procs, specs

    generation = 0
    worker_procs, worker_specs = _start_workers(generation)
    all_worker_specs = list(worker_specs)

    started = time.time()
    deadline = started + seconds
    next_restart = started + worker_restart_every
    series: list[dict[str, Any]] = []
    last_counts = harness.queue_counts(db)
    last_time = started
    series_file = series_path.open("w", encoding="utf-8")

    try:
        while time.time() < deadline:
            time.sleep(min(sample_interval, max(0.0, deadline - time.time())))
            now = time.time()
            counts = harness.queue_counts(db)
            window = max(1e-9, now - last_time)

            # Latency from samples written so far by the live workers.
            recent = Histogram("soak.window")
            for spec in all_worker_specs:
                path = Path(spec["out"]).with_suffix(".samples.jsonl")
                if not path.exists():
                    continue
                for sample in read_samples(path, tail=5000):
                    if sample.completed_at >= last_time:
                        recent.add(sample.end_to_end_ms)

            point = {
                "t": round(now - started, 1),
                "wall": now,
                "events": counts["events"],
                "completed": counts["completed"],
                "queue_depth": counts["pending"],
                "in_progress": counts["in_progress"],
                "dead_letter": counts["dead_letter"],
                "skipped": counts["skipped"],
                "append_per_s": round((counts["events"] - last_counts["events"]) / window, 1),
                "complete_per_s": round(
                    (counts["completed"] - last_counts["completed"]) / window, 1
                ),
                "p95_end_to_end_ms": round(recent.percentile(0.95), 2) if recent.count else None,
                "p99_end_to_end_ms": round(recent.percentile(0.99), 2) if recent.count else None,
                "samples_in_window": recent.count,
                "rss_mb": round(environment.rss_bytes() / 1048576, 1),
                "database": environment.database_footprint(db),
                "worker_generation": generation,
            }
            # Read-only, and only every Nth sample. "Quick" is quick relative
            # to deep, not relative to a 60-second window: it is a full scan
            # and grows with the database, so by the end of a long soak it
            # would take a quarter of every window and distort what it is
            # supposed to observe. Instrumentation that changes the
            # measurement is not instrumentation.
            if len(series) % max(1, int(doctor_every)) == 0:
                try:
                    doctor_started = time.time()
                    quick = run_doctor(db, deep=False)
                    point["doctor_quick_exit"] = quick["exit_code"]
                    point["doctor_quick_worst"] = quick["worst_severity"]
                    point["doctor_quick_seconds"] = round(time.time() - doctor_started, 3)
                except Exception as exc:
                    point["doctor_quick_exit"] = 3
                    point["doctor_quick_error"] = f"{type(exc).__name__}: {exc}"

            series.append(point)
            series_file.write(json.dumps(point, ensure_ascii=False) + "\n")
            series_file.flush()
            last_counts, last_time = counts, now

            if time.time() >= next_restart and time.time() < deadline - 60:
                # Restart the worker generation: a worker that cannot claim
                # after several restarts is a failure mode a steady run hides.
                for proc in worker_procs:
                    proc.terminate()
                harness.join_all(worker_procs, timeout=60)
                generation += 1
                worker_procs, worker_specs = _start_workers(generation)
                all_worker_specs.extend(worker_specs)
                next_restart = time.time() + worker_restart_every
    finally:
        series_file.close()

    producer_stop.set()
    harness.join_all(producer_procs, timeout=300)

    # Producers have stopped and the workers are deliberately still running:
    # the queue has to reach zero on its own, which is the property being
    # checked. Stopping the workers here would test nothing.
    drain_start = time.time()
    drained = False
    while time.time() < drain_start + 600:
        counts = harness.queue_counts(db)
        if counts["pending"] == 0 and counts["in_progress"] == 0:
            drained = True
            break
        time.sleep(2.0)
    drain_seconds = time.time() - drain_start
    worker_stop.set()
    harness.join_all(worker_procs, timeout=300)

    producer_results = harness.read_results([Path(s["out"]) for s in producer_specs])
    worker_results = harness.read_results([Path(s["out"]) for s in all_worker_specs])
    final_counts = harness.queue_counts(db)
    doctor_after = doctor_digest(run_doctor(db, deep=True))

    conn = sqlite3.connect(db, timeout=120.0)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    rss = [p["rss_mb"] for p in series if p.get("rss_mb")]
    depth = [p["queue_depth"] for p in series]
    p95 = [p["p95_end_to_end_ms"] for p in series if p.get("p95_end_to_end_ms")]
    first_half, second_half = p95[: len(p95) // 2], p95[len(p95) // 2:]

    return {
        "workload": "soak",
        "seconds_requested": seconds,
        "seconds_actual": round(time.time() - started, 1),
        "producers": producers,
        "workers_per_generation": workers,
        "worker_generations": generation + 1,
        "target_append_rate_per_s": target_rate,
        "doctor_every_n_samples": doctor_every,
        "samples": len(series),
        "appended": sum(r["appended"] for r in producer_results),
        "completed": final_counts["completed"],
        "queue_drained_after_stop": drained,
        "drain_seconds": round(drain_seconds, 1),
        "final_queue": final_counts,
        "sqlite_integrity_check": integrity,
        "rss_mb_first": rss[0] if rss else None,
        "rss_mb_last": rss[-1] if rss else None,
        "rss_mb_peak": max(rss) if rss else None,
        "rss_growth_mb": round(rss[-1] - rss[0], 1) if len(rss) > 1 else None,
        "queue_depth_max": max(depth) if depth else 0,
        "queue_depth_last": depth[-1] if depth else 0,
        "p95_first_half_mean_ms": round(sum(first_half) / len(first_half), 2) if first_half else None,
        "p95_second_half_mean_ms": round(sum(second_half) / len(second_half), 2) if second_half else None,
        "latency_drift_pct": (
            round(100 * (sum(second_half) / len(second_half)) / (sum(first_half) / len(first_half)) - 100, 1)
            if first_half and second_half else None
        ),
        "producer_lock_errors": sum(r["lock_errors"] for r in producer_results),
        "worker_lock_errors": sum(r["lock_errors"] for r in worker_results),
        "runtime_restarts": sum(r.get("runtime_restarts", 0) for r in producer_results),
        "peak_rss_mb": round(
            max((r["peak_rss_bytes"] for r in producer_results + worker_results), default=0)
            / 1048576, 1,
        ),
        "doctor_before": doctor_before,
        "doctor_after": doctor_after,
        "database": environment.database_footprint(db),
        "timeseries_path": str(series_path),
        "db_path": db,
    }


__all__ = ["run", "soak_producer_main"]
