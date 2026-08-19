from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .sqlite_utils import connect_sqlite

from . import fault_points as _fault
from .models import EpistemicStatus, EventRecord, Modality, SecurityLabel, TrustTier
from .outbox import (PROJECTION_VERSION, JobStatus, ProjectionJob,
                     ProjectionOutbox)

#: Projection requested when a caller does not say. `remember()` is what
#: actually creates a memory, so an event with no stated intent owes nothing
#: until someone asks — see `append(projection_types=...)`.
DEFAULT_PROJECTION_TYPES: tuple[str, ...] = ()


class ImmutableEventError(RuntimeError):
    pass


class SQLiteEventStore:
    """Append-only event store with SQLite-level UPDATE/DELETE protection."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = connect_sqlite(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        # Shares this connection deliberately: the event row and the record of
        # the projection it owes have to commit together, and only one
        # connection can give that guarantee.
        self.outbox = ProjectionOutbox(self.conn)

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

    def append(
        self,
        record: EventRecord,
        *,
        projection_types: tuple[str, ...] | list[str] | None = None,
        skip_types: tuple[str, ...] | list[str] = (),
    ) -> EventRecord:
        """Append an event, and optionally the projections it owes.

        When `projection_types` is given, an outbox row is written for each on
        **this same connection inside this same transaction**, so the event and
        the record of what it owes become durable together. A crash can no
        longer leave an event whose projection nobody knows about.

        Omitting the argument keeps the previous behaviour exactly: the event
        is appended and nothing is owed. `observe()` without `remember()` is a
        supported call, and inferring intent from silence is what made orphans
        undetectable in the first place.
        """
        checksum = record.checksum or self._checksum(record)
        _fault.fire(_fault.ProjectionFaultPoint.BEFORE_EVENT_TRANSACTION)
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
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_EVENT_INSERT)
        # RC-0 — "KHÔNG nợ gì ở đây" là một QUYẾT ĐỊNH, và nó commit CÙNG
        # event. Một hàng vắng mặt không nói được điều đó: replay sẽ đọc sự
        # vắng mặt thành "chắc ai quên build". Ghi thẳng trạng thái terminal
        # SKIPPED trong cùng transaction — hoặc cả event lẫn quyết định cùng
        # bền, hoặc không cái nào.
        now = time.time()
        for projection_type in skip_types:
            self.outbox.enqueue(
                ProjectionJob(
                    event_id=record.event_id,
                    projection_type=projection_type,
                    tenant_id=record.tenant_id,
                    projection_version=PROJECTION_VERSION,
                    status=JobStatus.SKIPPED.value,
                    last_error="non_substantive",
                    completed_at=now,
                ),
            )
        # Same connection, same open transaction — no commit between these.
        for projection_type in (projection_types or DEFAULT_PROJECTION_TYPES):
            self.outbox.enqueue(
                ProjectionJob(
                    event_id=record.event_id,
                    projection_type=projection_type,
                    tenant_id=record.tenant_id,
                    projection_version=PROJECTION_VERSION,
                    payload={"workspace_id": record.workspace_id},
                ),
                commit=False,
            )
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_OUTBOX_INSERT)
        self.conn.commit()
        _fault.fire(_fault.ProjectionFaultPoint.AFTER_EVENT_COMMIT)
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
