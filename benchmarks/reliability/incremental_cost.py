"""What an incremental scan actually costs, next to a full one.

The deep scan is 65.7s at 366,715 events. The whole argument for incremental
mode is that an operator can run it every few minutes instead, and that
argument is worth nothing unmeasured.

Four numbers on one database, built once:

    deep         the audit
    quick        four checks, dominated by PRAGMA integrity_check
    incremental  first run - the window is everything
    incremental  second run - the window is empty, which is the steady state
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.diagnostics import DeepDoctor                  # noqa: E402
from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor    # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                         # noqa: E402

from benchmarks.reliability import corpus, environment, harness            # noqa: E402


def _build(db: str, events: int) -> dict[str, Any]:
    runtime = MemoryOS(db, projection_mode="shadow")
    for obs in corpus.generate(events):
        runtime.observe(tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                        content=obs.content, workspace_id=obs.workspace_id)
    worker = harness.make_worker(runtime, worker_id="builder", lease_seconds=600)
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=500)
        if worker.metrics.claimed == before:
            break
    runtime.close()
    return harness.queue_counts(db)


def _timed(fn) -> tuple[float, Any]:
    started = time.perf_counter()
    report = fn()
    return time.perf_counter() - started, report


def measure(events: int, *, appended_between: int = 500) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix=f"inccost_{events}_"))
    db = str(tmp / "scan.db")
    print(f"  building {events:,} events ...", flush=True)
    build_start = time.time()
    counts = _build(db, events)
    print(f"    built in {time.time() - build_start:.1f}s", flush=True)

    def _open() -> sqlite3.Connection:
        conn = sqlite3.connect(db, timeout=120)
        conn.row_factory = sqlite3.Row
        return conn

    conn = _open()
    deep_s, deep = _timed(lambda: DeepDoctor(conn).run(deep=True))
    quick_s, quick = _timed(lambda: DeepDoctor(conn).run(deep=False))
    conn.close()

    conn = _open()
    first_s, first = _timed(lambda: IncrementalDoctor(conn).run_incremental())
    conn.close()

    conn = _open()
    steady_s, steady = _timed(lambda: IncrementalDoctor(conn).run_incremental())
    conn.close()

    # A realistic steady state: a few minutes of arrivals, then a scan.
    runtime = MemoryOS(db, projection_mode="shadow")
    for obs in corpus.generate(appended_between, start=events + 1):
        runtime.observe(tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                        content=obs.content, workspace_id=obs.workspace_id)
    worker = harness.make_worker(runtime, worker_id="topup", lease_seconds=600)
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=500)
        if worker.metrics.claimed == before:
            break
    runtime.close()

    conn = _open()
    window_s, window = _timed(lambda: IncrementalDoctor(conn).run_incremental())
    conn.close()

    footprint = environment.database_footprint(db)
    shutil.rmtree(tmp, ignore_errors=True)

    return {
        "events": counts["events"],
        "database_mb": round(footprint["total_bytes"] / 1048576, 1),
        "deep_seconds": round(deep_s, 3),
        "deep_findings": len(deep.findings),
        "quick_seconds": round(quick_s, 3),
        "incremental_first_run_seconds": round(first_s, 3),
        "incremental_first_window_events": first.counts.get("window_events"),
        "incremental_steady_seconds": round(steady_s, 3),
        "incremental_steady_window_events": steady.counts.get("window_events"),
        "incremental_after_arrivals_seconds": round(window_s, 3),
        "incremental_after_arrivals_window_events": window.counts.get("window_events"),
        "appended_between": appended_between,
        "deep_over_incremental_steady": round(deep_s / max(steady_s, 1e-6), 1),
        "note": (
            "quick mode is dominated by PRAGMA integrity_check, which reads "
            "every page; incremental uses quick_check instead, which is why it "
            "is cheaper than quick mode despite running more checks"
        ),
    }


def main() -> int:
    sizes = [int(a) for a in sys.argv[1:]] or [100_000]
    rows = [measure(n) for n in sizes]
    for row in rows:
        print(f"\n  {row['events']:,} events  ({row['database_mb']} MB)")
        print(f"    deep                        {row['deep_seconds']:>9.3f}s")
        print(f"    quick                       {row['quick_seconds']:>9.3f}s")
        print(f"    incremental, first run      {row['incremental_first_run_seconds']:>9.3f}s"
              f"  ({row['incremental_first_window_events']:,} in window)")
        print(f"    incremental, empty window   {row['incremental_steady_seconds']:>9.3f}s")
        print(f"    incremental, {row['appended_between']} new       "
              f"{row['incremental_after_arrivals_seconds']:>9.3f}s")
        print(f"    deep / incremental steady   {row['deep_over_incremental_steady']:>9.1f}x")
    out = _REPO / "reports" / "v082" / "incremental_doctor_cost.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows,
                               "environment": environment.capture(repo=_REPO)},
                              indent=2), encoding="utf-8")
    print(f"\n  written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
