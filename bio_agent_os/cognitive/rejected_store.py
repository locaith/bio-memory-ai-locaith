"""Input the system refused, kept where it can be looked at and replayed.

A watcher ran from 11/08 to 13/08 rejecting every capture it was handed. The
rejections went into `MemoryOS.quarantine`, a list on the instance, and the
instance went away with the process. By the time anyone noticed, the only
evidence that eight inputs had ever arrived was that they were missing.

Three properties, each of which was absent then:

* **Durable.** A refusal is a decision about data, and a decision nobody can
  review later is indistinguishable from a bug.
* **Attributed.** Which runtime refused it, at which version and config. The
  same input judged twice by two builds is the single most useful record this
  table holds, and it is only possible if the runtime says who it was.
* **Replayable.** `pending()` hands back exactly what arrived, so a fixed
  build can be pointed at the backlog rather than asked to reproduce it. A
  rejection you cannot replay is a deletion with extra steps.

Content is stored verbatim, on purpose. Storing a redacted copy would make the
table safe to print and useless to replay, and this is the one place where the
input has *not* yet been accepted into memory — the point is to hold it outside
the system until a person decides. `resolve()` is how it leaves.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_TABLE = "rejected_inputs"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    rejection_id    TEXT PRIMARY KEY,
    tenant_id       TEXT,
    workspace_id    TEXT,
    event_id        TEXT,
    source          TEXT,
    actor           TEXT,
    content         TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{{}}',
    reasons_json    TEXT NOT NULL DEFAULT '[]',
    risk_score      REAL,
    rejected_at     REAL NOT NULL,
    runtime_session TEXT,
    runtime_fingerprint TEXT,
    package_version TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    resolved_at     REAL,
    resolved_by     TEXT,
    resolution      TEXT,
    replay_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rejected_status
    ON {_TABLE} (status, rejected_at);
CREATE INDEX IF NOT EXISTS idx_rejected_fingerprint
    ON {_TABLE} (runtime_fingerprint);
"""

#: What happened to a rejected input in the end.
#:
#: `pending` and `replayed` are the two that matter. `accepted` and `discarded`
#: are terminal and require a person — an automatic path from "refused" to
#: "accepted" would defeat the gate that refused it.
STATUSES = ("pending", "replayed", "accepted", "discarded")


@dataclass
class Rejection:
    content: str
    reasons: list[str] = field(default_factory=list)
    risk_score: float | None = None
    event_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    source: str | None = None
    actor: str | None = None
    #: Everything needed to re-offer this input unchanged.
    payload: dict[str, Any] = field(default_factory=dict)


class RejectedStore:
    """Durable quarantine. Append, list, replay, resolve."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def record(self, rejection: Rejection, *, runtime: Any = None,
               session_id: str | None = None) -> str:
        """Write one refusal down. Returns its id.

        `runtime` is a `RuntimeIdentity`; passing None is allowed and recorded
        as unknown rather than rejected outright, because a store that refuses
        to record a rejection when provenance is missing loses the input to
        protect the metadata.
        """
        rejection_id = uuid.uuid4().hex
        self.conn.execute(
            f"""INSERT INTO {_TABLE} (
                    rejection_id, tenant_id, workspace_id, event_id, source,
                    actor, content, payload_json, reasons_json, risk_score,
                    rejected_at, runtime_session, runtime_fingerprint,
                    package_version, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
            (rejection_id, rejection.tenant_id, rejection.workspace_id,
             rejection.event_id, rejection.source, rejection.actor,
             rejection.content,
             json.dumps(rejection.payload, ensure_ascii=False, default=str),
             json.dumps(list(rejection.reasons), ensure_ascii=False),
             rejection.risk_score, time.time(), session_id,
             getattr(runtime, "fingerprint", None),
             getattr(runtime, "package_version", None)))
        self.conn.commit()
        return rejection_id

    def pending(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The backlog, oldest first — replay order is arrival order."""
        return self._rows(
            f"SELECT * FROM {_TABLE} WHERE status = 'pending' "
            f"ORDER BY rejected_at ASC LIMIT ?", (limit,))

    def all(self, *, limit: int = 500) -> list[dict[str, Any]]:
        return self._rows(
            f"SELECT * FROM {_TABLE} ORDER BY rejected_at DESC LIMIT ?",
            (limit,))

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            f"SELECT status, COUNT(*) FROM {_TABLE} GROUP BY status").fetchall()
        return {status: int(n) for status, n in rows}

    def by_runtime(self) -> list[dict[str, Any]]:
        """How many each build refused.

        The number that would have caught the stale watcher: one fingerprint
        holding every rejection in a two-day window, while the fingerprint on
        disk had refused nothing.
        """
        rows = self.conn.execute(
            f"""SELECT runtime_fingerprint, package_version, COUNT(*),
                       MIN(rejected_at), MAX(rejected_at)
                  FROM {_TABLE} GROUP BY runtime_fingerprint, package_version
                 ORDER BY COUNT(*) DESC""").fetchall()
        return [{"fingerprint": fp, "package_version": version, "count": int(n),
                 "first": first, "last": last}
                for fp, version, n, first, last in rows]

    def mark_replayed(self, rejection_id: str) -> None:
        self.conn.execute(
            f"""UPDATE {_TABLE}
                   SET status = 'replayed', replay_count = replay_count + 1
                 WHERE rejection_id = ?""", (rejection_id,))
        self.conn.commit()

    def resolve(self, rejection_id: str, *, status: str, by: str,
                note: str = "") -> None:
        """Close one out. `accepted` and `discarded` both need a name attached.

        Not automatic and not batched: each of these is a person overruling or
        confirming a safety decision, and the record should read that way a year
        later.
        """
        if status not in STATUSES:
            raise ValueError(f"status không hợp lệ: {status!r}")
        self.conn.execute(
            f"""UPDATE {_TABLE}
                   SET status = ?, resolved_at = ?, resolved_by = ?,
                       resolution = ?
                 WHERE rejection_id = ?""",
            (status, time.time(), by, note, rejection_id))
        self.conn.commit()

    def _rows(self, sql: str, args: tuple) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, args)
        names = [c[0] for c in cursor.description]
        out = []
        for row in cursor.fetchall():
            record = dict(zip(names, row))
            record["reasons"] = json.loads(record.pop("reasons_json") or "[]")
            record["payload"] = json.loads(record.pop("payload_json") or "{}")
            out.append(record)
        return out


__all__ = ["Rejection", "RejectedStore", "STATUSES"]
