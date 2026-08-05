"""Where the time actually goes in one projection.

Throughput that falls as workers are added is a contention symptom, not a
diagnosis. This counts the statements and commits a single job costs, so the
bottleneck is named from evidence rather than from a plausible story about
SQLite.

Single process on purpose: the per-job work is the same whatever the worker
count, and isolating it removes the contention that the multi-process runs
already measure.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402

from benchmarks.reliability import corpus, harness                     # noqa: E402


def _classify(sql: str) -> str:
    head = sql.strip().split(None, 1)[0].upper() if sql.strip() else "?"
    if head in ("COMMIT", "END"):
        return "COMMIT"
    if head == "BEGIN":
        return "BEGIN"
    return head


def profile(events: int = 2000) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="profile_"))
    db = str(tmp / "profile.db")

    runtime = MemoryOS(db, projection_mode="shadow")
    append_stmts: Counter[str] = Counter()
    append_tables: Counter[str] = Counter()

    def _append_trace(sql: str) -> None:
        append_stmts[_classify(sql)] += 1
        lowered = sql.lower()
        for table in ("cognitive_events", "projection_outbox"):
            if table in lowered:
                append_tables[table] += 1

    runtime.events.conn.set_trace_callback(_append_trace)
    append_start = time.perf_counter()
    for obs in corpus.generate(events):
        runtime.observe(
            tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
            content=obs.content, workspace_id=obs.workspace_id,
        )
    append_seconds = time.perf_counter() - append_start
    runtime.events.conn.set_trace_callback(None)
    runtime.close()

    runtime = MemoryOS(db, projection_mode="shadow")
    queue_stmts: Counter[str] = Counter()
    projection_stmts: Counter[str] = Counter()
    tables: Counter[str] = Counter()

    def _queue_trace(sql: str) -> None:
        queue_stmts[_classify(sql)] += 1
        _count_tables(sql)

    def _projection_trace(sql: str) -> None:
        projection_stmts[_classify(sql)] += 1
        _count_tables(sql)

    def _count_tables(sql: str) -> None:
        lowered = sql.lower()
        for table in ("cognitive_events", "projection_outbox", "projection_ledger",
                      "cognitive_memories", "shadow_memories"):
            if table in lowered:
                tables[table] += 1

    runtime.events.conn.set_trace_callback(_queue_trace)
    runtime.memories.conn.set_trace_callback(_projection_trace)

    worker = harness.make_worker(runtime, worker_id="profiler", lease_seconds=600)
    drain_start = time.perf_counter()
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=50)
        if worker.metrics.claimed == before:
            break
    drain_seconds = time.perf_counter() - drain_start

    runtime.events.conn.set_trace_callback(None)
    runtime.memories.conn.set_trace_callback(None)
    completed = worker.metrics.completed
    build_seconds = worker.metrics.build_seconds
    runtime.close()

    conn = sqlite3.connect(db)
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    conn.close()

    per_job = lambda n: round(n / max(1, completed), 3)  # noqa: E731
    return {
        "events": events,
        "append": {
            "seconds": round(append_seconds, 3),
            "per_event_ms": round(append_seconds * 1000 / max(1, events), 4),
            "statements": dict(append_stmts),
            "commits_per_event": round(append_stmts["COMMIT"] / max(1, events), 3),
            "table_touches": dict(append_tables),
        },
        "projection": {
            "seconds": round(drain_seconds, 3),
            "completed": completed,
            "throughput_per_s": round(completed / max(1e-9, drain_seconds), 1),
            "per_job_ms": round(drain_seconds * 1000 / max(1, completed), 4),
            "builder_only_ms": round(build_seconds * 1000 / max(1, completed), 4),
            "builder_share_pct": round(100 * build_seconds / max(1e-9, drain_seconds), 1),
            "queue_connection_statements": dict(queue_stmts),
            "projection_connection_statements": dict(projection_stmts),
            "commits_per_job": {
                "queue_connection": per_job(queue_stmts["COMMIT"]),
                "projection_connection": per_job(projection_stmts["COMMIT"]),
                "total": per_job(queue_stmts["COMMIT"] + projection_stmts["COMMIT"]),
            },
            "statements_per_job": per_job(sum(queue_stmts.values()) + sum(projection_stmts.values())),
            "table_touches": dict(tables),
        },
        "database": {"page_size": page_size, "pages": pages, "bytes": page_size * pages},
    }


def main() -> int:
    result = profile(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)
    out = _REPO / "reports" / "v082" / "writepath_profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\n  written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
