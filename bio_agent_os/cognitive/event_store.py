from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .sqlite_utils import connect_sqlite

from .models import EpistemicStatus, EventRecord, Modality, SecurityLabel, TrustTier


class ImmutableEventError(RuntimeError):
    pass


class SQLiteEventStore:
    """Append-only event store with SQLite-level UPDATE/DELETE protection."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = connect_sqlite(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS cognitive_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT,
                actor TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                trust_tier INTEGER NOT NULL,
                security_label TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                observed_at TEXT NOT NULL,
                checksum TEXT NOT NULL UNIQUE,
                metadata_json TEXT NOT NULL,
                modality TEXT NOT NULL DEFAULT 'text',
                epistemic_status TEXT NOT NULL DEFAULT 'observed'
            );
            CREATE INDEX IF NOT EXISTS idx_events_scope
            ON cognitive_events(tenant_id, workspace_id, observed_at);
            CREATE TRIGGER IF NOT EXISTS cognitive_events_no_update
            BEFORE UPDATE ON cognitive_events
            BEGIN SELECT RAISE(ABORT, 'cognitive_events is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS cognitive_events_no_delete
            BEFORE DELETE ON cognitive_events
            BEGIN SELECT RAISE(ABORT, 'cognitive_events is append-only'); END;
            """
        )
        self._ensure_column("modality", "TEXT NOT NULL DEFAULT 'text'")
        self._ensure_column("epistemic_status", "TEXT NOT NULL DEFAULT 'observed'")
        self.conn.commit()

    def _ensure_column(self, name: str, declaration: str) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(cognitive_events)")}
        if name not in columns:
            self.conn.execute(f"ALTER TABLE cognitive_events ADD COLUMN {name} {declaration}")

    @staticmethod
    def _checksum(record: EventRecord) -> str:
        canonical = json.dumps(
            {
                "event_id": record.event_id,
                "tenant_id": record.tenant_id,
                "workspace_id": record.workspace_id,
                "actor": record.actor,
                "source": record.source,
                "payload": record.payload,
                "observed_at": record.observed_at,
                "modality": record.modality.value,
                "epistemic_status": record.epistemic_status.value,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(self, record: EventRecord) -> EventRecord:
        checksum = record.checksum or self._checksum(record)
        self.conn.execute(
            """
            INSERT INTO cognitive_events(
                event_id, tenant_id, workspace_id, actor, source, payload_json,
                trust_tier, security_label, valid_from, valid_to, observed_at,
                checksum, metadata_json, modality, epistemic_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.event_id, record.tenant_id, record.workspace_id, record.actor,
                record.source, json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                int(record.trust_tier), record.security_label.value, record.valid_from,
                record.valid_to, record.observed_at, checksum,
                json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                record.modality.value, record.epistemic_status.value,
            ),
        )
        self.conn.commit()
        return EventRecord(**{**record.__dict__, "checksum": checksum})


    def append_many(self, records: list[EventRecord]) -> list[EventRecord]:
        """Append a batch atomically while preserving per-event checksums."""
        prepared: list[EventRecord] = []
        rows = []
        for record in records:
            checksum = record.checksum or self._checksum(record)
            prepared.append(EventRecord(**{**record.__dict__, "checksum": checksum}))
            rows.append((
                record.event_id, record.tenant_id, record.workspace_id, record.actor,
                record.source, json.dumps(record.payload, ensure_ascii=False, sort_keys=True),
                int(record.trust_tier), record.security_label.value, record.valid_from,
                record.valid_to, record.observed_at, checksum,
                json.dumps(record.metadata, ensure_ascii=False, sort_keys=True),
                record.modality.value, record.epistemic_status.value,
            ))
        self.conn.executemany(
            """INSERT INTO cognitive_events(
                event_id, tenant_id, workspace_id, actor, source, payload_json,
                trust_tier, security_label, valid_from, valid_to, observed_at,
                checksum, metadata_json, modality, epistemic_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows
        )
        self.conn.commit()
        return prepared

    def get(self, event_id: str, tenant_id: str) -> EventRecord | None:
        row = self.conn.execute(
            "SELECT * FROM cognitive_events WHERE event_id=? AND tenant_id=?",
            (event_id, tenant_id),
        ).fetchone()
        return self._row(row) if row else None

    def list(self, tenant_id: str, workspace_id: str | None = None, limit: int = 100) -> list[EventRecord]:
        if workspace_id is None:
            rows = self.conn.execute(
                "SELECT * FROM cognitive_events WHERE tenant_id=? ORDER BY observed_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM cognitive_events WHERE tenant_id=? AND workspace_id=? ORDER BY observed_at DESC LIMIT ?",
                (tenant_id, workspace_id, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def verify_chain(self, tenant_id: str) -> bool:
        return all((item.checksum or "") == self._checksum(item) for item in self.list(tenant_id, limit=1_000_000))

    @staticmethod
    def _row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            actor=row["actor"], source=row["source"], payload=json.loads(row["payload_json"]),
            trust_tier=TrustTier(row["trust_tier"]), security_label=SecurityLabel(row["security_label"]),
            valid_from=row["valid_from"], valid_to=row["valid_to"], observed_at=row["observed_at"],
            checksum=row["checksum"], metadata=json.loads(row["metadata_json"]),
            modality=Modality(row["modality"]), epistemic_status=EpistemicStatus(row["epistemic_status"]),
        )
