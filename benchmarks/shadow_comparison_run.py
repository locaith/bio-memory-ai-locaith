"""Shadow comparison at scale — the run that decides whether cutover is arguable.

Deliberately not a fixture-sized run. Twenty clean observations prove nothing
about a path that will carry every memory the system ever writes.
"""

from __future__ import annotations

import argparse
import random
import time
import unicodedata
from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.projection_capability import render as capability_render
from bio_agent_os.cognitive.shadow import ComparisonStatus
from bio_agent_os.cognitive.shadow_runner import (
    check_determinism,
    compare_event,
    legacy_projection,
    run_shadow_comparison,
    shadow_worker,
    write_report,
)

TENANTS = ("acme", "globex", "initech", "umbrella")
WORKSPACES = (None, "ws-eng", "ws-legal", "ws-ops")

#: Content shaped to exercise the comparator rather than flatter it: unicode,
#: mixed whitespace, long text, punctuation, and non-Latin scripts.
TEMPLATES = (
    "Deployment {i} finished on the staging cluster.",
    "Khách hàng {i} yêu cầu đổi lịch họp sang thứ Sáu.",
    "  spaced   out    content   {i}  ",
    "Multi\nline\nnote {i}",
    "Café naïve résumé {i} — unicode composition test",
    "Contract {i}: payment terms net-30, renewal automatic.",
    "エラー {i} が発生しました",
    "A" * 400 + " {i}",
)


def seed(runtime: MemoryOS, n: int, rng: random.Random) -> list[str]:
    """Write `n` observations through the legacy path, in shadow mode."""
    ids: list[str] = []
    latencies: list[float] = []
    for i in range(n):
        content = rng.choice(TEMPLATES).format(i=i)
        tenant = rng.choice(TENANTS)
        workspace = rng.choice(WORKSPACES)
        started = time.perf_counter()
        event = runtime.observe(
            tenant_id=tenant, actor="bench", source="shadow-bench",
            content=content, workspace_id=workspace,
        )
        runtime.remember(event=event, memory_type=MemoryType.EPISODIC, content=content)
        latencies.append((time.perf_counter() - started) * 1000)
        ids.append(event.event_id)
    return ids, latencies


def measure_legacy_overhead(db_path: Path, n: int, rng: random.Random) -> list[float]:
    """Same workload with shadow off, so the overhead figure has a baseline."""
    runtime = MemoryOS(db_path, projection_mode="legacy")
    _, latencies = seed(runtime, n, rng)
    return latencies


def main() -> int:
    parser = argparse.ArgumentParser(description="Shadow comparison at scale")
    parser.add_argument("--observations", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--reports", default="reports")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    tmp = Path(args.db) if args.db else Path("reports") / "_shadow_bench.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(tmp) + suffix).unlink(missing_ok=True)

    baseline_db = tmp.with_name("_shadow_baseline.db")
    for suffix in ("", "-wal", "-shm"):
        Path(str(baseline_db) + suffix).unlink(missing_ok=True)

    print(capability_render())
    print()

    legacy_only = measure_legacy_overhead(baseline_db, min(args.observations, 300), rng)

    runtime = MemoryOS(tmp, projection_mode="shadow")
    print(f"seeding {args.observations} observations in shadow mode ...")
    event_ids, shadow_mode_latency = seed(runtime, args.observations, rng)

    # Restart the runtime before draining, so the worker recovers a queue it
    # did not create — the state a real deployment would be in.
    runtime.events.conn.close()
    runtime.memories.conn.close()
    runtime = MemoryOS(tmp, projection_mode="shadow")

    print("draining the shadow queue ...")
    started = time.perf_counter()
    report = run_shadow_comparison(runtime, event_ids, batch_size=200,
                                   determinism_sample=100)
    drain_seconds = time.perf_counter() - started

    report.legacy_latency_ms = legacy_only
    report.shadow_latency_ms = shadow_mode_latency

    print(report.render())
    print()
    print(f"  drain wall time      : {drain_seconds:.2f}s")
    print(f"  observations/sec     : {len(event_ids) / max(drain_seconds, 1e-6):,.0f}")

    overhead_p50 = report.latency()["shadow_projection_ms"]["p50"]
    baseline_p50 = report.latency()["legacy_observe_ms"]["p50"]
    print(f"  legacy observe p50   : {baseline_p50:.4f} ms")
    print(f"  shadow observe p50   : {overhead_p50:.4f} ms")
    if baseline_p50:
        print(f"  overhead             : {(overhead_p50 - baseline_p50):+.4f} ms "
              f"({(overhead_p50 / baseline_p50 - 1) * 100:+.1f}%)")

    json_path, md_path = write_report(report, Path(args.reports))
    print(f"\n  wrote {json_path}")
    print(f"  wrote {md_path}")

    return 0 if report.clean else 2


if __name__ == "__main__":
    raise SystemExit(main())
