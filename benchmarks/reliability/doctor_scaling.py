"""How long does `doctor --deep` take as the database grows?

`docs/v082/OPERATIONS.md` said the doctor had only been measured at tens of
rows and that sub-millisecond scan time there was "an expectation, not a
measurement". The scale run turned that into a measurement the hard way: the
deep scan of a 50,000-event database held the benchmark up for several
minutes, which is not what a linear scan does.

This isolates it. One process, no workers competing, a fresh database per
size, quick and deep timed separately, with the query count the doctor
reports about itself. Anything super-linear shows up as a rising
seconds-per-1000-events column rather than as a number nobody compares.
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

from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402

from benchmarks.reliability import corpus, environment, harness        # noqa: E402
from benchmarks.reliability.workloads import run_doctor                # noqa: E402


def _build(db: str, events: int, *, drain: bool, shadow_records: int) -> dict[str, Any]:
    """A database with real events, real projections and real shadow rows."""
    runtime = MemoryOS(db, projection_mode="shadow")
    for obs in corpus.generate(events):
        runtime.observe(
            tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
            content=obs.content, workspace_id=obs.workspace_id,
        )
    if drain:
        worker = harness.make_worker(runtime, worker_id="builder", lease_seconds=600)
        while True:
            before = worker.metrics.claimed
            worker.run_once(batch_size=500)
            if worker.metrics.claimed == before:
                break
    runtime.close()

    if shadow_records:
        shadow_runtime = MemoryOS(db, projection_mode="shadow")
        ids = [
            row["event_id"]
            for row in shadow_runtime.events.conn.execute(
                "SELECT event_id FROM cognitive_events LIMIT ?", (shadow_records,)
            ).fetchall()
        ]
        from bio_agent_os.cognitive.outbox import ProjectionJob
        from bio_agent_os.cognitive.shadow import COGNITIVE_MEMORY
        from bio_agent_os.cognitive.shadow_runner import shadow_worker

        for event_id in ids:
            row = shadow_runtime.events.conn.execute(
                "SELECT tenant_id FROM cognitive_events WHERE event_id=?", (event_id,)
            ).fetchone()
            shadow_runtime.events.outbox.enqueue(
                ProjectionJob(event_id=event_id, projection_type=COGNITIVE_MEMORY,
                              tenant_id=row["tenant_id"], projection_version=2),
                commit=False,
            )
        shadow_runtime.events.conn.commit()
        worker = shadow_worker(shadow_runtime, worker_id="shadow-builder", lease_seconds=600)
        while True:
            before = worker.metrics.claimed
            worker.run_once(batch_size=500)
            if worker.metrics.claimed == before:
                break
        shadow_runtime.close()

    return harness.queue_counts(db)


def measure(sizes: list[int], *, shadow_records: int = 1000) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for events in sizes:
        tmp = Path(tempfile.mkdtemp(prefix=f"doctorscale_{events}_"))
        db = str(tmp / "scan.db")
        print(f"\n  building {events:,} events ...", flush=True)
        build_start = time.time()
        counts = _build(db, events, drain=True,
                        shadow_records=min(shadow_records, events))
        build_seconds = time.time() - build_start

        conn = sqlite3.connect(db, timeout=120)
        ledger = conn.execute("SELECT COUNT(*) FROM projection_ledger").fetchone()[0]
        shadow = conn.execute("SELECT COUNT(*) FROM shadow_memories").fetchone()[0]
        conn.close()

        print(f"    built in {build_seconds:.1f}s: {counts['events']:,} events, "
              f"{ledger:,} ledger, {shadow:,} shadow", flush=True)

        quick_start = time.perf_counter()
        quick = run_doctor(db, deep=False)
        quick_seconds = time.perf_counter() - quick_start

        deep_start = time.perf_counter()
        deep = run_doctor(db, deep=True)
        deep_seconds = time.perf_counter() - deep_start

        codes: dict[str, int] = {}
        for finding in deep["findings"]:
            codes[finding["code"]] = codes.get(finding["code"], 0) + 1

        row = {
            "events": counts["events"],
            "projections": ledger,
            "shadow_records": shadow,
            "build_seconds": round(build_seconds, 2),
            "database_bytes": environment.database_footprint(db)["total_bytes"],
            "quick": {
                "seconds": round(quick_seconds, 3),
                "checks_run": quick["checks_run"],
                "queries": quick["queries"],
                "findings": len(quick["findings"]),
                "exit_code": quick["exit_code"],
            },
            "deep": {
                "seconds": round(deep_seconds, 3),
                "checks_run": deep["checks_run"],
                "queries": deep["queries"],
                "findings": len(deep["findings"]),
                "findings_per_second": round(len(deep["findings"]) / max(1e-9, deep_seconds), 1),
                "exit_code": deep["exit_code"],
                "finding_codes": dict(sorted(codes.items(), key=lambda kv: -kv[1])[:12]),
            },
            "deep_seconds_per_1000_events": round(deep_seconds * 1000 / max(1, counts["events"]), 4),
            "peak_rss_mb": round(environment.peak_rss_bytes() / 1048576, 1),
        }
        rows.append(row)
        print(f"    quick {quick_seconds:8.3f}s   deep {deep_seconds:8.3f}s   "
              f"({row['deep_seconds_per_1000_events']}s per 1000 events)  "
              f"queries={deep['queries']}  findings={len(deep['findings'])}", flush=True)
        shutil.rmtree(tmp, ignore_errors=True)

    scaling = None
    if len(rows) > 1:
        first, last = rows[0], rows[-1]
        size_ratio = last["events"] / max(1, first["events"])
        time_ratio = last["deep"]["seconds"] / max(1e-9, first["deep"]["seconds"])
        scaling = {
            "size_ratio": round(size_ratio, 2),
            "deep_time_ratio": round(time_ratio, 2),
            "linear_would_be": round(size_ratio, 2),
            "quadratic_would_be": round(size_ratio ** 2, 2),
            "observed_exponent": round(
                (time_ratio ** 0.5 if size_ratio == 1 else
                 __import__("math").log(time_ratio) / __import__("math").log(size_ratio)), 2
            ),
        }

    return {
        "environment": environment.capture(repo=_REPO),
        "rows": rows,
        "scaling": scaling,
    }


def main() -> int:
    sizes = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 50_000]
    result = measure(sizes)
    out = _REPO / "reports" / "v082" / "doctor_scaling.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if result["scaling"]:
        s = result["scaling"]
        print(f"\n  {s['size_ratio']}x the data took {s['deep_time_ratio']}x the time "
              f"(linear would be {s['linear_would_be']}x, "
              f"quadratic {s['quadratic_would_be']}x) -> exponent {s['observed_exponent']}")
    print(f"  written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
