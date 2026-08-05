"""The five workloads, plus a deliberate failure distribution.

A. append-only        producers, no worker — how fast can the write path commit
B. projection-only    preloaded queue, workers only — how fast can it drain
C. mixed live         both at once — what an operator will actually see
D. shadow             legacy production plus outbox shadow — what it costs
E. recovery           crash, lock, stale lease, restart — under load, not idle

Each returns a plain dict. Nothing here decides whether a number is good; that
judgement belongs in the report next to the thresholds it is judged against.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.diagnostics import DeepDoctor              # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402
from bio_agent_os.cognitive.models import AccessContext, MemoryType    # noqa: E402
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob     # noqa: E402
from bio_agent_os.cognitive.reconciliation_worker import (             # noqa: E402
    BuildOutcome,
    BuildResult,
    CognitiveMemoryBuilder,
)
from bio_agent_os.cognitive.shadow import (                            # noqa: E402
    COGNITIVE_MEMORY,
    ComparisonStatus,
    ShadowMemoryBuilder,
)
from bio_agent_os.cognitive.shadow_runner import compare_event         # noqa: E402

from benchmarks.reliability import corpus, environment, harness        # noqa: E402
from benchmarks.reliability.metrics import (                           # noqa: E402
    Histogram,
    StageHistograms,
    read_samples,
)

DEFAULT_SEED = 20820805


# -- doctor ------------------------------------------------------------------

def run_doctor(db_path: str, *, deep: bool = True) -> dict[str, Any]:
    """Read-only scan on its own connection. Never reuses a runtime's."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        conn.row_factory = sqlite3.Row
        report = DeepDoctor(conn).run(deep=deep)
        payload = report.as_dict()
    finally:
        conn.close()
    payload["findings_per_second"] = round(
        len(payload["findings"]) / max(1e-9, payload["duration_s"]), 1
    )
    return payload


def doctor_digest(payload: dict[str, Any]) -> dict[str, Any]:
    """The doctor result without 60,000 individual findings attached."""
    codes: dict[str, int] = {}
    for finding in payload.get("findings", []):
        codes[finding["code"]] = codes.get(finding["code"], 0) + 1
    return {
        "exit_code": payload["exit_code"],
        "worst_severity": payload["worst_severity"],
        "checks_run": payload["checks_run"],
        "queries": payload["queries"],
        "duration_s": payload["duration_s"],
        "findings_per_second": payload["findings_per_second"],
        "by_severity": payload["by_severity"],
        "finding_codes": dict(sorted(codes.items(), key=lambda kv: -kv[1])),
        "repairable_findings": payload["repairable_findings"],
        "manual_review_required": payload["manual_review_required"],
        "counts": payload.get("counts", {}),
    }


# -- deliberate failures -----------------------------------------------------

#: What a real queue looks like. Forcing every job to COMPLETED would measure
#: a pipeline nobody operates.
FAULT_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("normal", 0.80),
    ("transient", 0.08),
    ("stale_lease", 0.04),
    ("dependency", 0.03),
    ("skipped", 0.02),
    ("permanent", 0.02),
    ("duplicate", 0.01),
)


def fault_bucket(event_id: str) -> str:
    """Deterministic from the event id, so a rerun injects the same faults."""
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:8]
    position = int(digest, 16) / 0xFFFFFFFF
    cumulative = 0.0
    for name, weight in FAULT_WEIGHTS:
        cumulative += weight
        if position < cumulative:
            return name
    return FAULT_WEIGHTS[-1][0]


class BenchmarkFailure(RuntimeError):
    """Raised by the fault-injecting builder. Recognisable in `last_error`, so
    a deliberate dead-letter is never mistaken for an unexplained one."""


class FaultInjectingBuilder:
    """Wraps the real builder and fails on schedule.

    Transient failures succeed once the attempt count passes the threshold, so
    the retry path and its backoff are exercised end to end rather than
    asserted about. Permanent failures never succeed, so `max_attempts` and
    dead-lettering are exercised too.
    """

    def __init__(self, inner: Any, *, transient_until_attempt: int = 3) -> None:
        self.inner = inner
        self.transient_until_attempt = transient_until_attempt
        self.injected = {"transient": 0, "permanent": 0, "skipped": 0}

    def build(self, event: Any, job: Any, conn: sqlite3.Connection) -> BuildResult:
        bucket = fault_bucket(event.event_id)
        if bucket == "permanent":
            self.injected["permanent"] += 1
            raise BenchmarkFailure("deliberate permanent failure (benchmark)")
        if bucket == "transient" and job.attempts < self.transient_until_attempt:
            self.injected["transient"] += 1
            raise BenchmarkFailure(
                f"deliberate transient failure (benchmark), attempt {job.attempts}"
            )
        if bucket == "skipped":
            self.injected["skipped"] += 1
            return BuildResult(BuildOutcome.SKIPPED.value, reason="deliberate skip (benchmark)")
        return self.inner.build(event, job, conn)


# -- workload A: append only -------------------------------------------------

def workload_a_append_only(
    *,
    run_dir: Path,
    events: int,
    producers: int,
    mode: str = "shadow",
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Commit events and their projection debt. No worker runs."""
    label = f"A_append_{events}_{producers}p"
    db = str(run_dir / f"{label}.db")
    work = run_dir / label
    work.mkdir(parents=True, exist_ok=True)

    MemoryOS(db, projection_mode=mode).close()  # create schema before the race

    ctx = __import__("multiprocessing").get_context("spawn")
    gate = ctx.Event()
    per = events // producers
    specs = [
        {
            "db": db, "mode": mode, "index": i, "count": per + (events % producers if i == 0 else 0),
            "start": i * per, "seed": seed, "out": str(work / f"producer_{i}.json"),
            "start_event": gate, "keep_event_ids": False,
        }
        for i in range(producers)
    ]
    procs = harness.spawn(harness.producer_main, specs)
    time.sleep(1.0)  # let every child finish importing before the clock starts
    wall_start = time.time()
    gate.set()
    codes = harness.join_all(procs)
    wall = time.time() - wall_start

    results = harness.read_results([Path(s["out"]) for s in specs])
    appended = sum(r["appended"] for r in results)
    # Per-producer latency is reported per producer. Averaging four processes'
    # percentiles into one number is arithmetic on percentiles, which is not a
    # percentile; aggregate speed is the wall clock, which is measurable.
    counts = harness.queue_counts(db)
    return {
        "workload": "A_append_only",
        "events_requested": events,
        "producers": producers,
        "mode": mode,
        "exit_codes": codes,
        "wall_seconds": round(wall, 3),
        "appended": appended,
        "append_throughput_per_s": round(appended / max(1e-9, wall), 1),
        "outbox_enqueue_throughput_per_s": round(counts["pending"] / max(1e-9, wall), 1),
        "lock_errors": sum(r["lock_errors"] for r in results),
        "failures": sum(r["failures"] for r in results),
        "per_producer": [
            {
                "index": r["index"], "appended": r["appended"],
                "throughput_per_s": round(r["throughput_per_s"], 1),
                "latency": r["latency"], "peak_rss_mb": round(r["peak_rss_bytes"] / 1048576, 1),
            }
            for r in results
        ],
        "queue": counts,
        "database": environment.database_footprint(db),
        "db_path": db,
    }


# -- workload B: projection only ---------------------------------------------

def workload_b_projection_only(
    *,
    run_dir: Path,
    events: int,
    workers: int,
    mode: str = "shadow",
    shadow: bool = False,
    seed: int = DEFAULT_SEED,
    preload_from: str | None = None,
) -> dict[str, Any]:
    """Preload the queue, then drain it. Nothing is being appended meanwhile."""
    label = f"B_projection_{events}_{workers}w"
    db = str(run_dir / f"{label}.db")
    work = run_dir / label
    work.mkdir(parents=True, exist_ok=True)

    if preload_from and Path(preload_from).exists():
        import shutil

        for suffix in ("", "-wal", "-shm"):
            src = Path(preload_from + suffix)
            if src.exists():
                shutil.copy2(src, db + suffix)
        preload_seconds = 0.0
    else:
        t0 = time.time()
        runtime = MemoryOS(db, projection_mode=mode)
        for obs in corpus.generate(events, seed=seed):
            runtime.observe(
                tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                content=obs.content, workspace_id=obs.workspace_id,
            )
        runtime.close()
        preload_seconds = time.time() - t0

    before = harness.queue_counts(db)
    doctor_before = doctor_digest(run_doctor(db, deep=True))

    ctx = __import__("multiprocessing").get_context("spawn")
    gate = ctx.Event()
    specs = [
        {
            "db": db, "mode": mode, "worker_id": f"w{i}", "shadow": shadow,
            "out": str(work / f"worker_{i}.json"), "start_event": gate,
            "batch_size": 50, "idle_polls": 8, "max_seconds": 1800,
        }
        for i in range(workers)
    ]
    procs = harness.spawn(harness.worker_main, specs)
    time.sleep(1.0)
    wall_start = time.time()
    gate.set()
    codes = harness.join_all(procs)
    wall = time.time() - wall_start

    results = harness.read_results([Path(s["out"]) for s in specs])
    stages = StageHistograms()
    samples = 0
    for spec in specs:
        for sample in read_samples(Path(spec["out"]).with_suffix(".samples.jsonl")):
            stages.add(sample)
            samples += 1

    after = harness.queue_counts(db)
    doctor_after = doctor_digest(run_doctor(db, deep=True))
    completed = sum(r["metrics"]["completed"] for r in results)

    return {
        "workload": "B_projection_only",
        "events": events,
        "workers": workers,
        "shadow": shadow,
        "preload_seconds": round(preload_seconds, 3),
        "exit_codes": codes,
        "wall_seconds": round(wall, 3),
        "drain_seconds": round(wall, 3),
        "completed": completed,
        "projection_throughput_per_s": round(completed / max(1e-9, wall), 1),
        "samples": samples,
        "latency": stages.as_dict(),
        "metrics": _sum_metrics(results),
        "lock_errors": sum(r["lock_errors"] for r in results),
        "peak_rss_mb": round(max((r["peak_rss_bytes"] for r in results), default=0) / 1048576, 1),
        "queue_before": before,
        "queue_after": after,
        "doctor_before": doctor_before,
        "doctor_after": doctor_after,
        "database": environment.database_footprint(db),
        "db_path": db,
    }


def _sum_metrics(results: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for result in results:
        for key, value in result["metrics"].items():
            if isinstance(value, (int, float)) and key not in ("mean_build_ms",):
                out[key] = round(out.get(key, 0) + value, 4)
    return out


# -- workload C: mixed live --------------------------------------------------

def workload_c_mixed(
    *,
    run_dir: Path,
    events: int,
    producers: int,
    workers: int,
    mode: str = "shadow",
    shadow: bool = False,
    seed: int = DEFAULT_SEED,
    sample_interval: float = 0.5,
) -> dict[str, Any]:
    """Producers and workers at the same time — the configuration an operator runs."""
    label = f"C_mixed_{events}_{producers}p_{workers}w"
    db = str(run_dir / f"{label}.db")
    work = run_dir / label
    work.mkdir(parents=True, exist_ok=True)
    MemoryOS(db, projection_mode=mode).close()

    ctx = __import__("multiprocessing").get_context("spawn")
    gate = ctx.Event()
    stop = ctx.Event()
    per = events // producers
    producer_specs = [
        {
            "db": db, "mode": mode, "index": i,
            "count": per + (events % producers if i == 0 else 0),
            "start": i * per, "seed": seed, "out": str(work / f"producer_{i}.json"),
            "start_event": gate,
        }
        for i in range(producers)
    ]
    worker_specs = [
        {
            "db": db, "mode": mode, "worker_id": f"w{i}", "shadow": shadow,
            "out": str(work / f"worker_{i}.json"), "start_event": gate,
            "stop_event": stop, "batch_size": 50, "idle_polls": 10**9,
            "stay_alive": True, "max_seconds": 1800, "poll_seconds": 0.02,
        }
        for i in range(workers)
    ]

    worker_procs = harness.spawn(harness.worker_main, worker_specs)
    producer_procs = harness.spawn(harness.producer_main, producer_specs)
    time.sleep(1.5)

    wall_start = time.time()
    gate.set()

    depth: list[dict[str, float]] = []
    while any(p.is_alive() for p in producer_procs):
        depth.append({"t": round(time.time() - wall_start, 3), **harness.queue_counts(db)})
        time.sleep(sample_interval)
    producers_done = time.time()
    harness.join_all(producer_procs, timeout=600)

    # Producers have stopped. The queue must reach zero on its own.
    drain_deadline = time.time() + 600
    while time.time() < drain_deadline:
        counts = harness.queue_counts(db)
        depth.append({"t": round(time.time() - wall_start, 3), **counts})
        if counts["pending"] == 0 and counts["in_progress"] == 0:
            break
        time.sleep(sample_interval)
    drained = time.time()
    stop.set()
    worker_codes = harness.join_all(worker_procs, timeout=300)

    producer_results = harness.read_results([Path(s["out"]) for s in producer_specs])
    worker_results = harness.read_results([Path(s["out"]) for s in worker_specs])
    stages = StageHistograms()
    for spec in worker_specs:
        for sample in read_samples(Path(spec["out"]).with_suffix(".samples.jsonl")):
            stages.add(sample)

    counts = harness.queue_counts(db)
    appended = sum(r["appended"] for r in producer_results)
    completed = sum(r["metrics"]["completed"] for r in worker_results)

    return {
        "workload": "C_mixed",
        "events": events,
        "producers": producers,
        "workers": workers,
        "shadow": shadow,
        "worker_exit_codes": worker_codes,
        "produce_seconds": round(producers_done - wall_start, 3),
        "queue_drain_seconds_after_producers_stop": round(drained - producers_done, 3),
        "total_seconds": round(drained - wall_start, 3),
        "appended": appended,
        "completed": completed,
        "producer_throughput_per_s": round(appended / max(1e-9, producers_done - wall_start), 1),
        "worker_throughput_per_s": round(completed / max(1e-9, drained - wall_start), 1),
        "latency": stages.as_dict(),
        "metrics": _sum_metrics(worker_results),
        "lock_errors": (
            sum(r["lock_errors"] for r in producer_results)
            + sum(r["lock_errors"] for r in worker_results)
        ),
        "peak_queue_depth": max((int(d["pending"]) for d in depth), default=0),
        "queue_depth_series": depth,
        "queue_final": counts,
        "queue_drained": counts["pending"] == 0 and counts["in_progress"] == 0,
        "peak_rss_mb": round(
            max((r["peak_rss_bytes"] for r in producer_results + worker_results), default=0)
            / 1048576, 1,
        ),
        "doctor_after": doctor_digest(run_doctor(db, deep=True)),
        "database": environment.database_footprint(db),
        "db_path": db,
    }


# -- workload D: shadow ------------------------------------------------------

def workload_d_shadow(
    *,
    run_dir: Path,
    events: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Legacy production and outbox shadow from one canonical input.

    Two runs over the same corpus: one with the shadow queue off, one with it
    on. The difference in `observe()` latency is the overhead shadow mode puts
    on the response path, which is the only number that matters to a caller.
    """
    work = run_dir / f"D_shadow_{events}"
    work.mkdir(parents=True, exist_ok=True)
    legacy_db = str(work / "legacy.db")
    shadow_db = str(work / "shadow.db")

    def _observe_all(db: str, mode: str) -> tuple[Histogram, Histogram, list[str], MemoryOS]:
        runtime = MemoryOS(db, projection_mode=mode)
        observe_ms = Histogram(f"observe.{mode}")
        remember_ms = Histogram(f"remember.{mode}")
        ids: list[str] = []
        for obs in corpus.generate(events, seed=seed):
            t0 = time.perf_counter()
            event = runtime.observe(
                tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                content=obs.content, workspace_id=obs.workspace_id,
            )
            observe_ms.add((time.perf_counter() - t0) * 1000)
            t1 = time.perf_counter()
            runtime.remember(
                event=event, memory_type=MemoryType.EPISODIC, content=obs.content,
            )
            remember_ms.add((time.perf_counter() - t1) * 1000)
            ids.append(event.event_id)
        return observe_ms, remember_ms, ids, runtime

    legacy_observe, legacy_remember, _, legacy_runtime = _observe_all(legacy_db, "legacy")
    legacy_runtime.close()

    shadow_observe, shadow_remember, event_ids, runtime = _observe_all(shadow_db, "shadow")

    # Drain the shadow queue with a worker, exactly as the runtime would.
    worker = harness.make_worker(runtime, worker_id="shadow-1", shadow=True, lease_seconds=0)
    drain_start = time.time()
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=200)
        if worker.metrics.claimed == before:
            break
    drain_seconds = time.time() - drain_start

    stages = StageHistograms()
    for sample in worker.samples:
        stages.add(sample)

    # Compare every observation, then confirm production recall never sees a
    # shadow row: isolation by table, not by filter.
    statuses: dict[str, int] = {}
    compare_start = time.time()
    for event_id in event_ids:
        result = compare_event(runtime, event_id)
        statuses[result.status] = statuses.get(result.status, 0) + 1
    compare_seconds = time.time() - compare_start

    leaked = runtime.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE memory_id IN "
        "(SELECT shadow_projection_key FROM shadow_memories)"
    ).fetchone()[0]
    recall_rows = runtime.recall(
        "quy trình duyệt đơn hàng approval queue",
        context=AccessContext(tenant_id="tenant_00", agent_id="bench"),
        limit=50,
    )
    recall_ids = {
        getattr(getattr(item, "memory", item), "memory_id", None) for item in recall_rows
    }
    shadow_keys = {
        row["shadow_projection_key"]
        for row in runtime.memories.conn.execute(
            "SELECT shadow_projection_key FROM shadow_memories LIMIT 5000"
        ).fetchall()
    }
    recall_leak = len(recall_ids & shadow_keys)

    shadow_count = runtime.shadow_memories.count()
    footprint = environment.database_footprint(shadow_db)
    doctor_after = doctor_digest(run_doctor(shadow_db, deep=True))
    runtime.close()

    # MATCH_NORMALIZED counts: the comparator's normalisation rules are
    # documented and deterministic, and a difference they resolve is not a
    # difference in what the two paths produced.
    matched = statuses.get(ComparisonStatus.MATCH.value, 0) + statuses.get(
        ComparisonStatus.MATCH_NORMALIZED.value, 0
    )
    return {
        "workload": "D_shadow",
        "events": events,
        "legacy_observe_ms": legacy_observe.as_dict(),
        "shadow_observe_ms": shadow_observe.as_dict(),
        "legacy_remember_ms": legacy_remember.as_dict(),
        "shadow_remember_ms": shadow_remember.as_dict(),
        "observe_overhead_p50_pct": _overhead(legacy_observe, shadow_observe, 0.50),
        "observe_overhead_p95_pct": _overhead(legacy_observe, shadow_observe, 0.95),
        "observe_overhead_mean_pct": round(
            100 * (shadow_observe.total / max(1, shadow_observe.count))
            / max(1e-9, legacy_observe.total / max(1, legacy_observe.count)) - 100, 2
        ),
        "shadow_drain_seconds": round(drain_seconds, 3),
        "shadow_drain_throughput_per_s": round(
            worker.metrics.completed / max(1e-9, drain_seconds), 1
        ),
        "shadow_latency": stages.as_dict(),
        "compare_seconds": round(compare_seconds, 3),
        "comparison_statuses": statuses,
        "match_rate_pct": round(100 * matched / max(1, len(event_ids)), 4),
        "mismatches": len(event_ids) - matched,
        "shadow_projections": shadow_count,
        "shadow_rows_visible_in_production": int(leaked),
        "shadow_rows_returned_by_recall": recall_leak,
        "worker_metrics": worker.metrics.as_dict(),
        "doctor_after": doctor_after,
        "database": footprint,
        "db_path": shadow_db,
    }


def _overhead(baseline: Histogram, measured: Histogram, q: float) -> float:
    base = baseline.percentile(q)
    if base <= 0:
        return 0.0
    return round(100 * (measured.percentile(q) / base - 1), 2)


# -- deliberate failure distribution ----------------------------------------

def fault_worker_main(spec: dict[str, Any]) -> None:
    """A worker whose builder fails on the benchmark's schedule."""
    sys.path.insert(0, str(_REPO))
    from benchmarks.reliability import harness as _h
    from benchmarks.reliability.metrics import write_samples as _write

    runtime = MemoryOS(spec["db"], projection_mode=spec.get("mode", "shadow"))
    inner = (
        ShadowMemoryBuilder(runtime.shadow_memories) if spec.get("shadow")
        else CognitiveMemoryBuilder(runtime.memories)
    )
    injector = FaultInjectingBuilder(inner)
    worker = _h.make_worker(
        runtime, worker_id=spec["worker_id"],
        lease_seconds=float(spec.get("lease_seconds", 30.0)),
        max_attempts=int(spec.get("max_attempts", 5)),
        builders={COGNITIVE_MEMORY: injector},
    )
    start = spec.get("start_event")
    if start is not None:
        start.wait(timeout=120)
    deadline = time.time() + float(spec.get("max_seconds", 600))

    def _outstanding() -> int:
        """Jobs still in a non-terminal state, read from the queue itself.

        Exiting on "I claimed nothing this cycle" would stop while permanent
        failures sit in backoff — up to 30s at the default schedule — and
        report them as `pending` rather than the dead-letters they become.
        The queue's own state is the only honest stop condition.
        """
        row = worker.conn.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE status IN (?,?)",
            (JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value),
        ).fetchone()
        return int(row[0])

    while time.time() < deadline:
        before = worker.metrics.claimed
        worker.run_once(batch_size=int(spec.get("batch_size", 50)))
        if worker.metrics.claimed == before:
            if _outstanding() == 0:
                break
            time.sleep(0.25)   # let backoff windows open
    if worker.samples:
        _write(worker.samples, Path(spec["out"]).with_suffix(".samples.jsonl"))
    runtime.close()
    Path(spec["out"]).write_text(json.dumps({
        "role": "fault_worker", "worker_id": spec["worker_id"],
        "metrics": worker.metrics.as_dict(), "injected": injector.injected,
        "lock_errors": worker.lock_errors, "peak_rss_bytes": environment.peak_rss_bytes(),
    }), encoding="utf-8")


def workload_faults(
    *,
    run_dir: Path,
    events: int = 10_000,
    workers: int = 4,
    mode: str = "shadow",
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """80/8/4/3/2/2/1. Terminal states must match the semantics, not all be COMPLETED."""
    work = run_dir / f"F_faults_{events}"
    work.mkdir(parents=True, exist_ok=True)
    db = str(work / "faults.db")

    runtime = MemoryOS(db, projection_mode=mode)
    buckets: dict[str, int] = {name: 0 for name, _ in FAULT_WEIGHTS}
    ids_by_bucket: dict[str, list[str]] = {name: [] for name, _ in FAULT_WEIGHTS}
    for obs in corpus.generate(events, seed=seed):
        event = runtime.observe(
            tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
            content=obs.content, workspace_id=obs.workspace_id,
        )
        bucket = fault_bucket(event.event_id)
        buckets[bucket] += 1
        ids_by_bucket[bucket].append(event.event_id)

    # dependency: a child projection whose parent has not completed yet.
    # Ordered *before* its parent on purpose — the worker claims by
    # `created_at`, so a child enqueued afterwards usually finds its parent
    # already done and the PENDING_DEPENDENCY path never runs. Putting the
    # child first is what makes the state observable rather than incidental.
    for event_id in ids_by_bucket["dependency"]:
        parent = runtime.events.conn.execute(
            "SELECT tenant_id, created_at FROM projection_outbox"
            " WHERE event_id=? AND projection_type=?",
            (event_id, COGNITIVE_MEMORY),
        ).fetchone()
        if parent is None:
            continue
        runtime.events.outbox.enqueue(
            ProjectionJob(
                event_id=event_id, projection_type="context_block",
                tenant_id=parent["tenant_id"],
                created_at=float(parent["created_at"]) - 0.001,
                available_at=float(parent["created_at"]) - 0.001,
            ),
            commit=False,
        )
    runtime.events.conn.commit()

    # duplicate: the same job enqueued twice, which must stay one job.
    duplicate_attempts = 0
    for event_id in ids_by_bucket["duplicate"]:
        row = runtime.events.conn.execute(
            "SELECT tenant_id FROM cognitive_events WHERE event_id=?", (event_id,)
        ).fetchone()
        runtime.events.outbox.enqueue(
            ProjectionJob(event_id=event_id, projection_type=COGNITIVE_MEMORY,
                          tenant_id=row["tenant_id"]),
            commit=False,
        )
        duplicate_attempts += 1
    runtime.events.conn.commit()

    # stale lease: claimed by a worker that is never coming back.
    ghost = 0
    now = time.time()
    for event_id in ids_by_bucket["stale_lease"]:
        cur = runtime.events.conn.execute(
            "UPDATE projection_outbox SET status=?, locked_by='ghost-worker',"
            " locked_at=?, attempts=1 WHERE event_id=? AND projection_type=? AND status=?",
            (JobStatus.IN_PROGRESS.value, now - 3600, event_id, COGNITIVE_MEMORY,
             JobStatus.PENDING.value),
        )
        ghost += cur.rowcount
    runtime.events.conn.commit()
    runtime.close()

    before = harness.queue_counts(db)
    ctx = __import__("multiprocessing").get_context("spawn")
    gate = ctx.Event()
    specs = [
        {
            "db": db, "mode": mode, "worker_id": f"f{i}",
            "out": str(work / f"fault_worker_{i}.json"), "start_event": gate,
            "batch_size": 50, "idle_polls": 40, "max_seconds": 900,
            "lease_seconds": 30.0, "max_attempts": 5,
        }
        for i in range(workers)
    ]
    procs = harness.spawn(fault_worker_main, specs)
    time.sleep(1.5)
    started = time.time()
    gate.set()
    harness.join_all(procs, timeout=1200)
    wall = time.time() - started

    results = harness.read_results([Path(s["out"]) for s in specs])
    after = harness.queue_counts(db)

    conn = sqlite3.connect(db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    dead_letters = conn.execute(
        "SELECT last_error, COUNT(*) AS n FROM projection_outbox WHERE status=?"
        " GROUP BY last_error", (JobStatus.DEAD_LETTER.value,)
    ).fetchall()
    attempts_rows = conn.execute(
        "SELECT status, MAX(attempts) AS mx, AVG(attempts) AS avg FROM projection_outbox"
        " GROUP BY status"
    ).fetchall()
    orphan_claimed = conn.execute(
        "SELECT COUNT(*) FROM projection_outbox WHERE status=? AND locked_at <= ?",
        (JobStatus.IN_PROGRESS.value, time.time() - 30),
    ).fetchone()[0]
    conn.close()

    def _classify(error: str | None) -> str:
        text = error or ""
        if "deliberate permanent failure" in text:
            return "expected: injected permanent failure"
        if "no builder registered for context_block" in text:
            return "expected: unsupported capability (context_block has no builder)"
        if "deliberate transient failure" in text:
            return "expected: injected transient failure exhausted attempts"
        return "UNEXPLAINED"

    dead_letter_breakdown = [
        {"reason": row["last_error"], "count": int(row["n"]), "classification": _classify(row["last_error"])}
        for row in dead_letters
    ]

    return {
        "workload": "F_fault_distribution",
        "events": events,
        "workers": workers,
        "planned_distribution": {name: round(weight * 100, 1) for name, weight in FAULT_WEIGHTS},
        "actual_buckets": buckets,
        "actual_distribution_pct": {
            name: round(100 * n / max(1, events), 2) for name, n in buckets.items()
        },
        "ghost_claims_planted": ghost,
        "duplicate_enqueue_attempts": duplicate_attempts,
        "wall_seconds": round(wall, 3),
        "queue_before": before,
        "queue_after": after,
        "terminal_states": {
            k: v for k, v in after.items()
            if k in ("pending", "in_progress", "completed", "dead_letter", "skipped")
        },
        "dead_letter_breakdown": dead_letter_breakdown,
        "unexplained_dead_letters": sum(
            d["count"] for d in dead_letter_breakdown if d["classification"] == "UNEXPLAINED"
        ),
        "attempts_by_status": [
            {"status": r["status"], "max_attempts": r["mx"], "mean_attempts": round(float(r["avg"]), 2)}
            for r in attempts_rows
        ],
        "stale_claims_remaining": int(orphan_claimed),
        "injected": {
            k: sum(r["injected"].get(k, 0) for r in results)
            for k in ("transient", "permanent", "skipped")
        },
        "metrics": _sum_metrics(results),
        "doctor_after": doctor_digest(run_doctor(db, deep=True)),
        "database": environment.database_footprint(db),
        "db_path": db,
    }


__all__ = [
    "DEFAULT_SEED",
    "FAULT_WEIGHTS",
    "BenchmarkFailure",
    "FaultInjectingBuilder",
    "doctor_digest",
    "fault_bucket",
    "fault_worker_main",
    "run_doctor",
    "workload_a_append_only",
    "workload_b_projection_only",
    "workload_c_mixed",
    "workload_d_shadow",
    "workload_faults",
]
