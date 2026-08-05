"""Pause, resume and drain — the operator's half of a rollback.

A feature flag turns the projection path off for *new* work. It does not tell
a running worker to stop, and it does not say when the queue has finished. Both
are needed during a canary: rolling back to legacy while a worker is mid-job
must not abandon that job, and knowing the queue has drained is what makes the
rollback safe to declare complete.

Pause is a fact in the database, not a signal to a process. A worker that
starts after the pause sees it, a worker that restarts sees it, and a worker
that is halfway through a projection finishes that projection first. Pausing
between claims and never inside one is the whole design: an interrupted
transaction is the crash case, and there is no reason to create one on purpose.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_control (
    scope TEXT PRIMARY KEY,
    paused INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    operator TEXT,
    changed_at REAL NOT NULL
);
"""

GLOBAL_SCOPE = ""


@dataclass(frozen=True, slots=True)
class ControlState:
    scope: str
    paused: bool
    reason: str | None
    operator: str | None
    changed_at: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope or "(all tenants)",
            "paused": self.paused,
            "reason": self.reason,
            "operator": self.operator,
            "changed_at": self.changed_at,
        }


class ProjectionControl:
    """Reads and writes the pause flag. Lives on the queue connection, so a
    worker sees a pause without needing a second database."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(CONTROL_SCHEMA)
        self.conn.commit()

    def state(self, scope: str = GLOBAL_SCOPE) -> ControlState:
        row = self.conn.execute(
            "SELECT * FROM projection_control WHERE scope=?", (scope,)
        ).fetchone()
        if row is None:
            return ControlState(scope, False, None, None, None)
        return ControlState(
            scope=scope, paused=bool(row["paused"]), reason=row["reason"],
            operator=row["operator"], changed_at=float(row["changed_at"]),
        )

    def is_paused(self, tenant_id: str | None = None) -> bool:
        """A global pause covers every tenant; a tenant pause covers only its own."""
        if self.state(GLOBAL_SCOPE).paused:
            return True
        return bool(tenant_id) and self.state(tenant_id).paused

    def pause(self, *, scope: str = GLOBAL_SCOPE, reason: str = "",
              operator: str = "cli") -> ControlState:
        self._set(scope, True, reason, operator)
        return self.state(scope)

    def resume(self, *, scope: str = GLOBAL_SCOPE, reason: str = "",
               operator: str = "cli") -> ControlState:
        self._set(scope, False, reason, operator)
        return self.state(scope)

    def _set(self, scope: str, paused: bool, reason: str, operator: str) -> None:
        self.conn.execute(
            "INSERT INTO projection_control(scope, paused, reason, operator, changed_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(scope) DO UPDATE SET paused=excluded.paused,"
            "  reason=excluded.reason, operator=excluded.operator,"
            "  changed_at=excluded.changed_at",
            (scope, int(paused), reason or None, operator, time.time()),
        )
        self.conn.commit()

    def all_states(self) -> list[ControlState]:
        return [
            self.state(row["scope"])
            for row in self.conn.execute(
                "SELECT scope FROM projection_control ORDER BY scope"
            ).fetchall()
        ]


def drain(
    memory_os: Any,
    *,
    timeout_seconds: float = 300.0,
    batch_size: int = 100,
    poll_seconds: float = 0.2,
    worker_id: str = "drain",
) -> dict[str, Any]:
    """Work the queue to empty, ignoring the pause flag, and report.

    Drain is what makes a pause safe to act on: the queue reaches zero before
    anyone concludes the projection path is idle. It deliberately overrides the
    pause — a paused system that cannot be drained can only be waited out.

    Reports what it could not finish rather than looping: a job in backoff, a
    dependency waiting on a parent and a dead letter are all legitimate reasons
    for a queue not to reach zero, and none of them is fixed by trying harder.
    """
    from .reconciliation_worker import worker_for

    started = time.time()
    # `control=None` deliberately: drain overrides the pause. A worker built
    # with the default control would read the pause it was asked to drain past
    # and do nothing.
    worker = worker_for(memory_os, worker_id=worker_id, manage_wal=False, control=None)
    deadline = started + timeout_seconds
    idle_rounds = 0

    def _outstanding() -> int:
        row = worker.conn.execute(
            "SELECT COUNT(*) FROM projection_outbox WHERE status IN ('pending','in_progress')"
        ).fetchone()
        return int(row[0])

    before = memory_os.events.outbox.counts()
    while time.time() < deadline:
        claimed_before = worker.metrics.claimed
        worker.run_once(batch_size=batch_size)
        if worker.metrics.claimed == claimed_before:
            if _outstanding() == 0:
                break
            idle_rounds += 1
            time.sleep(poll_seconds)
        else:
            idle_rounds = 0

    after = memory_os.events.outbox.counts()
    remaining = after["pending"] + after["in_progress"]
    return {
        "drained": remaining == 0,
        "seconds": round(time.time() - started, 3),
        "timed_out": time.time() >= deadline and remaining > 0,
        "queue_before": before,
        "queue_after": after,
        "remaining": remaining,
        "idle_rounds": idle_rounds,
        "metrics": worker.metrics.as_dict(),
        "note": (
            "" if remaining == 0 else
            "jobs remain: check for backoff windows, dependencies waiting on a "
            "parent, or dead letters — none of which draining harder resolves"
        ),
    }


__all__ = ["CONTROL_SCHEMA", "GLOBAL_SCOPE", "ControlState", "ProjectionControl", "drain"]
