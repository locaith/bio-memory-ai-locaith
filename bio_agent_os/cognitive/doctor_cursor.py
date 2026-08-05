"""Where the last scan got to, and when that answer stops being usable.

A deep scan of 366,715 events takes 48 seconds. That is right for an audit and
wrong for something an operator wants to run every few minutes, so the scan
needs to be able to start from where the last one finished.

A cursor that is wrong is worse than no cursor: it makes the doctor skip data
and report clean. Four rules keep it honest.

**It advances only after a scan has completed.** A crash mid-scan leaves the
cursor where it was, so the next run re-covers the same ground. Re-checking is
cheap; missing a row is not.

**It advances only when nothing is outstanding.** If the window produced a
FAIL or a CRITICAL, the cursor stays put — including across a repair, unless
that repair actually resolved the finding. A cursor that steps over an
unhandled problem hides it permanently, which is the one failure mode an
incremental scanner must not have.

**It invalidates itself.** The doctor's own version and a fingerprint of the
schema are stored alongside the position. A new check, or a new column, means
the old ground was covered by a different question, and the next scan is full.

**It never replaces a full scan.** `--full` always exists and always ignores
this table.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

#: Bump when a check is added, removed, or changed in what it would find.
#: A stored cursor from an older doctor covered its ground with a different
#: set of questions, so it cannot be trusted to have covered this one's.
DOCTOR_VERSION = 2

#: Tables whose definitions make up the schema fingerprint. A change to any of
#: them can change what a check means, so it invalidates the cursor.
FINGERPRINTED_TABLES = (
    "cognitive_events",
    "projection_outbox",
    "projection_ledger",
    "cognitive_memories",
    "memory_source_events",
    "shadow_memories",
)

CURSOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS doctor_cursor (
    scope TEXT PRIMARY KEY,
    last_event_rowid INTEGER NOT NULL DEFAULT 0,
    last_ledger_rowid INTEGER NOT NULL DEFAULT 0,
    last_outbox_rowid INTEGER NOT NULL DEFAULT 0,
    last_event_id TEXT,
    last_observed_at TEXT,
    doctor_version INTEGER NOT NULL,
    schema_fingerprint TEXT NOT NULL,
    updated_at REAL NOT NULL,
    blocked_by_findings INTEGER NOT NULL DEFAULT 0,
    blocked_reason TEXT
);
"""

GLOBAL_SCOPE = ""


def schema_fingerprint(conn: sqlite3.Connection) -> str:
    """A hash of the CREATE statements the checks depend on.

    Reads `sqlite_master` rather than a version number someone has to remember
    to bump: a column added without bumping anything would otherwise leave a
    cursor claiming to have covered ground it never saw.
    """
    parts: list[str] = []
    for table in FINGERPRINTED_TABLES:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND tbl_name=?"
            " ORDER BY name", (table,),
        ).fetchall()
        parts.append(table + "::" + "|".join(str(r[0] or "") for r in row))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class Cursor:
    scope: str = GLOBAL_SCOPE
    last_event_rowid: int = 0
    last_ledger_rowid: int = 0
    last_outbox_rowid: int = 0
    last_event_id: str | None = None
    last_observed_at: str | None = None
    doctor_version: int = DOCTOR_VERSION
    schema_fingerprint: str = ""
    updated_at: float = 0.0
    blocked_by_findings: bool = False
    blocked_reason: str | None = None
    #: Why this scan is starting where it is. Reported, never inferred.
    reason: str = "no cursor stored"

    @property
    def usable(self) -> bool:
        return self.last_event_rowid > 0 or self.last_ledger_rowid > 0 or self.last_outbox_rowid > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope or "(all tenants)",
            "last_event_rowid": self.last_event_rowid,
            "last_ledger_rowid": self.last_ledger_rowid,
            "last_outbox_rowid": self.last_outbox_rowid,
            "last_event_id": self.last_event_id,
            "last_observed_at": self.last_observed_at,
            "doctor_version": self.doctor_version,
            "schema_fingerprint": self.schema_fingerprint,
            "updated_at": self.updated_at,
            "blocked_by_findings": self.blocked_by_findings,
            "blocked_reason": self.blocked_reason,
            "reason": self.reason,
        }


class CursorStore:
    """Reads and writes the cursor. Writing is the only write the doctor does,
    and it happens after the read-only scan has finished."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self._migrated = False

    def _migrate(self) -> None:
        if self._migrated:
            return
        self.conn.executescript(CURSOR_SCHEMA)
        self.conn.commit()
        self._migrated = True

    def load(self, scope: str = GLOBAL_SCOPE) -> Cursor:
        """The stored cursor, or a fresh one with the reason it is fresh."""
        self._migrate()
        fingerprint = schema_fingerprint(self.conn)
        row = self.conn.execute(
            "SELECT * FROM doctor_cursor WHERE scope=?", (scope,)
        ).fetchone()
        if row is None:
            return Cursor(scope=scope, schema_fingerprint=fingerprint,
                          reason="no cursor stored for this scope")
        if int(row["doctor_version"]) != DOCTOR_VERSION:
            return Cursor(
                scope=scope, schema_fingerprint=fingerprint,
                reason=f"doctor version changed {row['doctor_version']} -> {DOCTOR_VERSION}",
            )
        if str(row["schema_fingerprint"]) != fingerprint:
            return Cursor(
                scope=scope, schema_fingerprint=fingerprint,
                reason="schema fingerprint changed since the last scan",
            )
        if int(row["blocked_by_findings"]):
            return Cursor(
                scope=scope, schema_fingerprint=fingerprint,
                last_event_rowid=int(row["last_event_rowid"]),
                last_ledger_rowid=int(row["last_ledger_rowid"]),
                last_outbox_rowid=int(row["last_outbox_rowid"]),
                last_event_id=row["last_event_id"],
                last_observed_at=row["last_observed_at"],
                updated_at=float(row["updated_at"]),
                blocked_by_findings=True,
                blocked_reason=row["blocked_reason"],
                reason=f"held: {row['blocked_reason']}",
            )
        return Cursor(
            scope=scope,
            last_event_rowid=int(row["last_event_rowid"]),
            last_ledger_rowid=int(row["last_ledger_rowid"]),
            last_outbox_rowid=int(row["last_outbox_rowid"]),
            last_event_id=row["last_event_id"],
            last_observed_at=row["last_observed_at"],
            doctor_version=int(row["doctor_version"]),
            schema_fingerprint=fingerprint,
            updated_at=float(row["updated_at"]),
            reason="resuming from the stored position",
        )

    def save(self, cursor: Cursor) -> None:
        """One statement, one commit, after the scan. Nothing partial."""
        self._migrate()
        self.conn.execute(
            "INSERT INTO doctor_cursor("
            " scope, last_event_rowid, last_ledger_rowid, last_outbox_rowid,"
            " last_event_id, last_observed_at, doctor_version, schema_fingerprint,"
            " updated_at, blocked_by_findings, blocked_reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(scope) DO UPDATE SET"
            "  last_event_rowid=excluded.last_event_rowid,"
            "  last_ledger_rowid=excluded.last_ledger_rowid,"
            "  last_outbox_rowid=excluded.last_outbox_rowid,"
            "  last_event_id=excluded.last_event_id,"
            "  last_observed_at=excluded.last_observed_at,"
            "  doctor_version=excluded.doctor_version,"
            "  schema_fingerprint=excluded.schema_fingerprint,"
            "  updated_at=excluded.updated_at,"
            "  blocked_by_findings=excluded.blocked_by_findings,"
            "  blocked_reason=excluded.blocked_reason",
            (
                cursor.scope, cursor.last_event_rowid, cursor.last_ledger_rowid,
                cursor.last_outbox_rowid, cursor.last_event_id, cursor.last_observed_at,
                DOCTOR_VERSION, cursor.schema_fingerprint or schema_fingerprint(self.conn),
                time.time(), int(cursor.blocked_by_findings), cursor.blocked_reason,
            ),
        )
        self.conn.commit()

    def reset(self, scope: str | None = None) -> int:
        self._migrate()
        if scope is None:
            cur = self.conn.execute("DELETE FROM doctor_cursor")
        else:
            cur = self.conn.execute("DELETE FROM doctor_cursor WHERE scope=?", (scope,))
        self.conn.commit()
        return cur.rowcount

    def high_water(self, scope: str = GLOBAL_SCOPE) -> tuple[int, int, int, str | None, str | None]:
        """The largest rowids present right now, and the newest event's identity."""
        def _max(table: str, tenant_column: str | None = None) -> int:
            sql = f"SELECT MAX(rowid) FROM {table}"
            params: tuple = ()
            if scope and tenant_column:
                sql += f" WHERE {tenant_column}=?"
                params = (scope,)
            try:
                row = self.conn.execute(sql, params).fetchone()
            except sqlite3.OperationalError:
                return 0
            return int(row[0]) if row and row[0] is not None else 0

        events = _max("cognitive_events", "tenant_id")
        ledger = _max("projection_ledger", "tenant_id")
        outbox = _max("projection_outbox", "tenant_id")
        event_id = observed_at = None
        if events:
            try:
                row = self.conn.execute(
                    "SELECT event_id, observed_at FROM cognitive_events WHERE rowid=?",
                    (events,),
                ).fetchone()
                if row:
                    event_id, observed_at = row["event_id"], row["observed_at"]
            except sqlite3.OperationalError:
                pass
        return events, ledger, outbox, event_id, observed_at

    def rowid_of_event(self, event_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT rowid FROM cognitive_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return int(row[0]) if row else None

    def rowid_at_time(self, timestamp: str) -> int:
        """Largest event rowid at or before `timestamp`, 0 if none.

        `observed_at` is an ISO string, so comparison is lexicographic, which
        is only correct for the format this store writes. Events are appended
        in order, so the rowid is what actually bounds the window.
        """
        row = self.conn.execute(
            "SELECT MAX(rowid) FROM cognitive_events WHERE observed_at <= ?", (timestamp,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0


__all__ = [
    "CURSOR_SCHEMA",
    "DOCTOR_VERSION",
    "FINGERPRINTED_TABLES",
    "GLOBAL_SCOPE",
    "Cursor",
    "CursorStore",
    "schema_fingerprint",
]
