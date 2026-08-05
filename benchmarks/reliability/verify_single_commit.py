"""Prove the four invariants after removing the redundant commit.

Each scenario kills a real process at a named point inside the projection
transaction, restarts a worker, and then asserts on the closed database file:

    projection count = 1
    ledger count     = 1
    duplicate        = 0
    integrity_check  = ok

`os._exit` is used rather than an exception so no finaliser, flush or atexit
hook runs — the same thing a power loss does.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bio_agent_os.cognitive import fault_points as _fault              # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                     # noqa: E402
from bio_agent_os.cognitive.models import EventRecord                  # noqa: E402
from bio_agent_os.cognitive.projection_registry import ProjectionType  # noqa: E402
from bio_agent_os.cognitive.reconciliation_worker import worker_for    # noqa: E402

MEMORY = ProjectionType.COGNITIVE_MEMORY.value

#: Every point inside or adjacent to the projection transaction. A crash at
#: any of them must leave the four invariants intact after recovery.
POINTS = (
    "before_ledger_insert",
    "after_ledger_insert",
    "before_projection_write",
    "after_projection_write_before_commit",
    "after_projection_commit",
    "before_outbox_complete",
    "after_outbox_complete",
)


def _crash_child(spec: dict[str, Any]) -> None:
    sys.path.insert(0, str(_REPO))

    def hook(point: str) -> None:
        if point == spec["point"]:
            Path(spec["marker"]).write_text(point, encoding="utf-8")
            os._exit(70)

    runtime = MemoryOS(spec["db"])
    worker = worker_for(runtime, worker_id="crasher", lease_seconds=0.5)
    _fault.install(hook)
    try:
        worker.run_once(batch_size=1)
    finally:
        _fault.clear()
    runtime.close()


def _recover_child(spec: dict[str, Any]) -> None:
    sys.path.insert(0, str(_REPO))
    runtime = MemoryOS(spec["db"])
    worker = worker_for(runtime, worker_id="recovery", lease_seconds=0.5)
    deadline = time.time() + 30
    while time.time() < deadline:
        before = worker.metrics.claimed
        worker.run_once(batch_size=5)
        if worker.metrics.claimed == before:
            row = worker.conn.execute(
                "SELECT COUNT(*) FROM projection_outbox WHERE status IN ('pending','in_progress')"
            ).fetchone()
            if int(row[0]) == 0:
                break
            time.sleep(0.3)
    Path(spec["out"]).write_text(json.dumps(worker.metrics.as_dict()), encoding="utf-8")
    runtime.close()


def _inspect(db: str) -> dict[str, Any]:
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return {
            "projections": conn.execute(
                "SELECT COUNT(*) FROM cognitive_memories"
            ).fetchone()[0],
            "ledger": conn.execute(
                "SELECT COUNT(*) FROM projection_ledger"
            ).fetchone()[0],
            "duplicate_ledger": conn.execute(
                "SELECT COUNT(*) FROM (SELECT projection_key FROM projection_ledger"
                " GROUP BY projection_key HAVING COUNT(*) > 1)"
            ).fetchone()[0],
            "duplicate_projection": conn.execute(
                "SELECT COUNT(*) FROM (SELECT json_extract(metadata_json,'$.projection_key') AS k,"
                " COUNT(*) AS n FROM cognitive_memories"
                " WHERE json_extract(metadata_json,'$.projection_key') IS NOT NULL"
                " GROUP BY k HAVING n > 1)"
            ).fetchone()[0],
            "ledger_without_target": conn.execute(
                "SELECT COUNT(*) FROM projection_ledger WHERE target_id IS NULL"
            ).fetchone()[0],
            "outbox": dict(
                (r["status"], r["n"]) for r in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM projection_outbox GROUP BY status"
                ).fetchall()
            ),
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
        }
    finally:
        conn.close()


def run_point(point: str, ctx) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix=f"singlecommit_{point}_"))
    db = str(tmp / "runtime.db")
    marker = tmp / "crashed.txt"

    runtime = MemoryOS(db)
    runtime.events.append(
        EventRecord(tenant_id="t1", actor="tester", source="unit",
                    payload={"content": "one event, one projection"},
                    event_id="evt-single"),
        projection_types=(MEMORY,),
    )
    runtime.close()

    crash = ctx.Process(target=_crash_child,
                        args=({"db": db, "point": point, "marker": str(marker)},))
    crash.start()
    crash.join(timeout=60)

    out = tmp / "recovery.json"
    recover = ctx.Process(target=_recover_child, args=({"db": db, "out": str(out)},))
    recover.start()
    recover.join(timeout=120)

    state = _inspect(db)
    metrics = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    ok = (
        state["projections"] == 1
        and state["ledger"] == 1
        and state["duplicate_ledger"] == 0
        and state["duplicate_projection"] == 0
        and state["ledger_without_target"] == 0
        and state["integrity_check"] == "ok"
        and state["outbox"].get("completed", 0) == 1
    )
    return {
        "fault_point": point,
        "crashed_as_designed": marker.exists() and crash.exitcode == 70,
        "crash_exit_code": crash.exitcode,
        "already_built_on_retry": metrics.get("already_built", 0),
        **state,
        "invariants_hold": ok,
    }


def main() -> int:
    ctx = mp.get_context("spawn")
    rows = [run_point(point, ctx) for point in POINTS]
    for row in rows:
        mark = "OK  " if row["invariants_hold"] else "FAIL"
        print(f"  [{mark}] {row['fault_point']:<38} "
              f"crashed={str(row['crashed_as_designed']):<5} "
              f"proj={row['projections']} ledger={row['ledger']} "
              f"dup={row['duplicate_ledger'] + row['duplicate_projection']} "
              f"already_built={row['already_built_on_retry']} "
              f"integrity={row['integrity_check']}", flush=True)
    failed = [r for r in rows if not r["invariants_hold"]]
    payload = {
        "invariants": {
            "projection count": 1, "ledger count": 1, "duplicate": 0,
            "ledger rows without target_id": 0, "integrity_check": "ok",
        },
        "points_tested": len(rows),
        "points_holding": len(rows) - len(failed),
        "all_hold": not failed,
        "results": rows,
    }
    out = _REPO / "reports" / "v082" / "single_commit_verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  {payload['points_holding']}/{payload['points_tested']} fault points hold "
          f"every invariant")
    print(f"  written: {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
