"""A deletion that survives a rebuild.

Reproduced on 2026-08-15 in forty lines:

    remember  "Số điện thoại của Hoàng Yến là 0977123456."
    forget_derived            -> deleted=1, verified_clean=True
    cognitive_memories        -> 0                          gone
    replay + rebuild + drain
    cognitive_memories        -> 1                          back, verbatim

No piece was wrong on its own. `forget_derived` runs at derived level and says
so; the event log is append-only by design; the replay engine rebuilds
projections from that log. Together they mean a deletion holds until the first
recovery operation — and recovery is exactly when nobody is watching.

The fix is not to delete the event. The log is what makes the history
explainable and what `erase_history` exists to redact under its own audit. The
fix is that **the log is no longer the only input to a rebuild**. A tombstone
is a second append-only fact, of equal standing:

    E100  PHONE(A) = 0977123456          the event
    E500  FORGET  target = E100          the tombstone

Replaying both, in order, yields a serving layer without the phone number.
Replaying only the first yields the bug. The event still exists, still explains
what the system once believed, and still cannot be materialised.

Two checks rather than one, because a queue outlives the decision that filled
it:

    at enqueue     a tombstoned event is never offered to the outbox
    at apply       a job already queued is skipped when it comes up

Either alone leaves a window. A deletion between a `replay()` and the worker
draining it would slip through the first; a caller enqueueing directly would
slip through the second.

**Lifting a tombstone is a write, not a delete.** `lift()` appends a row saying
who reversed it and why. A tombstone table where entries disappear cannot
answer "was this ever deleted", which is the question an audit asks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

TABLE = "memory_tombstones"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    tombstone_id  TEXT PRIMARY KEY,
    event_id      TEXT NOT NULL,
    memory_id     TEXT,
    tenant_id     TEXT,
    scope         TEXT NOT NULL,
    reason        TEXT NOT NULL,
    actor         TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    at            TEXT NOT NULL,
    lifted        INTEGER NOT NULL DEFAULT 0,
    lifted_by     TEXT,
    lifted_reason TEXT,
    lifted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_event ON {TABLE}(event_id, lifted);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_memory ON {TABLE}(memory_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def place(conn: sqlite3.Connection, *, event_ids: Iterable[str],
          scope: str, actor: str, reason: str = "",
          memory_id: str | None = None, tenant_id: str | None = None,
          evidence: list[str] | None = None) -> list[str]:
    """Mark these events as not to be materialised again.

    Refuses without an actor, like every other write that removes something.
    A deletion nobody is named for is a deletion nobody can be asked about.
    """
    if not str(actor).strip():
        raise ValueError("đặt bia mộ phải ghi rõ actor")
    ensure_schema(conn)
    placed = []
    for event_id in {str(e) for e in event_ids if e}:
        tombstone_id = uuid4().hex
        conn.execute(
            f"""INSERT INTO {TABLE} (tombstone_id, event_id, memory_id,
                    tenant_id, scope, reason, actor, evidence_json, at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
            (tombstone_id, event_id, memory_id, tenant_id, scope, reason,
             actor, json.dumps(evidence or [], ensure_ascii=False), _now()))
        placed.append(tombstone_id)
    conn.commit()
    return placed


def buried(conn: sqlite3.Connection, event_id: str) -> bool:
    """Is this event tombstoned right now?

    Cheap on purpose: it is called once per event per replay scan and once per
    job application, so a slow answer here would make the safe path the
    expensive one and invite somebody to skip it.
    """
    try:
        row = conn.execute(
            f"SELECT 1 FROM {TABLE} WHERE event_id=? AND lifted=0 LIMIT 1",
            (str(event_id),)).fetchone()
    except sqlite3.OperationalError:
        # No table means no deletions have ever been recorded, which is a
        # different thing from an error. A store that has never deleted
        # anything must not fail closed here or nothing would ever project.
        return False
    return row is not None


def buried_among(conn: sqlite3.Connection,
                 event_ids: Iterable[str]) -> set[str]:
    """The tombstoned subset. One query for a whole replay scan."""
    ids = [str(e) for e in event_ids if e]
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            f"SELECT DISTINCT event_id FROM {TABLE} "
            f"WHERE lifted=0 AND event_id IN ({marks})", ids).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[0]) for row in rows}


def lift(conn: sqlite3.Connection, event_id: str, *, actor: str,
         reason: str) -> int:
    """Undo a tombstone, on the record.

    Marked rather than removed. "Was this ever deleted?" has to stay
    answerable, and a table whose rows disappear cannot answer it.
    """
    if not str(actor).strip() or not str(reason).strip():
        raise ValueError("gỡ bia mộ phải ghi rõ actor và lý do")
    ensure_schema(conn)
    cursor = conn.execute(
        f"""UPDATE {TABLE} SET lifted=1, lifted_by=?, lifted_reason=?,
                lifted_at=? WHERE event_id=? AND lifted=0""",
        (actor, reason, _now(), str(event_id)))
    conn.commit()
    return cursor.rowcount


def tombstones(conn: sqlite3.Connection, *, event_id: str | None = None,
               include_lifted: bool = True,
               limit: int = 500) -> list[dict[str, Any]]:
    ensure_schema(conn)
    sql = f"SELECT * FROM {TABLE}"
    clauses, params = [], []
    if event_id:
        clauses.append("event_id=?")
        params.append(str(event_id))
    if not include_lifted:
        clauses.append("lifted=0")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY at DESC LIMIT {int(limit)}"
    cursor = conn.execute(sql, params)
    names = [c[0] for c in cursor.description]
    out = []
    for row in cursor.fetchall():
        record = dict(zip(names, row))
        record["evidence"] = json.loads(record.pop("evidence_json") or "[]")
        record["lifted"] = bool(record["lifted"])
        out.append(record)
    return out


__all__ = ["SCHEMA", "TABLE", "buried", "buried_among", "ensure_schema",
           "lift", "place", "tombstones"]
