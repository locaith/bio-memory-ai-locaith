from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import BeliefState, CognitiveMemory, MemoryType, SecurityLabel, TrustTier


class SQLiteMemoryStore:
    """Versioned bitemporal projection store.

    Memories are never overwritten. ``supersede`` closes the old version and
    inserts a new row, preserving full lineage.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cognitive_memories (
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_json TEXT NOT NULL,
                source_event_ids_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                salience REAL NOT NULL,
                utility REAL NOT NULL,
                trust_tier INTEGER NOT NULL,
                security_label TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                observed_at TEXT NOT NULL,
                superseded_at TEXT,
                reinforcement_count INTEGER NOT NULL,
                contradiction_count INTEGER NOT NULL,
                retrieval_count INTEGER NOT NULL,
                last_accessed_at TEXT,
                lifecycle_state TEXT NOT NULL,
                governed_exception_for TEXT,
                approved_by TEXT,
                approval_expires_at TEXT,
                allowed_agents_json TEXT NOT NULL,
                allowed_roles_json TEXT NOT NULL,
                purpose_allowlist_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY(memory_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_scope
            ON cognitive_memories(tenant_id, workspace_id, memory_type, lifecycle_state);
            CREATE INDEX IF NOT EXISTS idx_memory_temporal
            ON cognitive_memories(tenant_id, valid_from, valid_to, superseded_at);
            """
        )
        self.conn.commit()

    def put(self, memory: CognitiveMemory) -> CognitiveMemory:
        self.conn.execute(
            """
            INSERT INTO cognitive_memories VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                memory.memory_id, memory.version, memory.tenant_id, memory.workspace_id,
                memory.memory_type.value, memory.content,
                json.dumps(memory.structured_content, ensure_ascii=False, sort_keys=True),
                json.dumps(memory.source_event_ids), memory.confidence, memory.importance,
                memory.salience, memory.utility, int(memory.trust_tier),
                memory.security_label.value, memory.valid_from, memory.valid_to,
                memory.observed_at, memory.superseded_at, memory.reinforcement_count,
                memory.contradiction_count, memory.retrieval_count, memory.last_accessed_at,
                memory.lifecycle_state.value, memory.governed_exception_for,
                memory.approved_by, memory.approval_expires_at,
                json.dumps(memory.allowed_agents), json.dumps(memory.allowed_roles),
                json.dumps(memory.purpose_allowlist), memory.created_at,
                json.dumps(memory.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return memory

    def latest(self, memory_id: str, tenant_id: str) -> CognitiveMemory | None:
        row = self.conn.execute(
            """SELECT * FROM cognitive_memories
               WHERE memory_id=? AND tenant_id=? ORDER BY version DESC LIMIT 1""",
            (memory_id, tenant_id),
        ).fetchone()
        return self._row(row) if row else None

    def supersede(self, memory_id: str, tenant_id: str, **changes) -> CognitiveMemory:
        current = self.latest(memory_id, tenant_id)
        if current is None:
            raise KeyError(memory_id)
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE cognitive_memories SET superseded_at=? WHERE memory_id=? AND version=?",
            (now, memory_id, current.version),
        )
        data = current.__dict__.copy()
        data.update(changes)
        data["version"] = current.version + 1
        data["superseded_at"] = None
        data["observed_at"] = changes.get("observed_at", now)
        successor = CognitiveMemory(**data)
        self.put(successor)
        return successor

    def active(self, tenant_id: str, workspace_id: str | None = None, as_of: str | None = None) -> list[CognitiveMemory]:
        query = "SELECT * FROM cognitive_memories WHERE tenant_id=? AND superseded_at IS NULL"
        args: list[object] = [tenant_id]
        if workspace_id is not None:
            query += " AND (workspace_id=? OR workspace_id IS NULL)"
            args.append(workspace_id)
        rows = self.conn.execute(query, args).fetchall()
        result = [self._row(row) for row in rows]
        if as_of:
            result = [m for m in result if self._valid_at(m, as_of)]
        return result

    def history(self, memory_id: str, tenant_id: str) -> list[CognitiveMemory]:
        rows = self.conn.execute(
            "SELECT * FROM cognitive_memories WHERE memory_id=? AND tenant_id=? ORDER BY version",
            (memory_id, tenant_id),
        ).fetchall()
        return [self._row(row) for row in rows]

    def mark_retrieved(self, memory: CognitiveMemory) -> None:
        self.conn.execute(
            """UPDATE cognitive_memories SET retrieval_count=retrieval_count+1,
               last_accessed_at=? WHERE memory_id=? AND version=?""",
            (datetime.now(timezone.utc).isoformat(), memory.memory_id, memory.version),
        )
        self.conn.commit()

    @staticmethod
    def _valid_at(memory: CognitiveMemory, as_of: str) -> bool:
        return (memory.valid_from is None or memory.valid_from <= as_of) and (
            memory.valid_to is None or as_of < memory.valid_to
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> CognitiveMemory:
        return CognitiveMemory(
            memory_id=row["memory_id"], version=row["version"], tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"], memory_type=MemoryType(row["memory_type"]),
            content=row["content"], structured_content=json.loads(row["structured_json"]),
            source_event_ids=json.loads(row["source_event_ids_json"]),
            confidence=row["confidence"], importance=row["importance"], salience=row["salience"],
            utility=row["utility"], trust_tier=TrustTier(row["trust_tier"]),
            security_label=SecurityLabel(row["security_label"]), valid_from=row["valid_from"],
            valid_to=row["valid_to"], observed_at=row["observed_at"],
            superseded_at=row["superseded_at"], reinforcement_count=row["reinforcement_count"],
            contradiction_count=row["contradiction_count"], retrieval_count=row["retrieval_count"],
            last_accessed_at=row["last_accessed_at"], lifecycle_state=BeliefState(row["lifecycle_state"]),
            governed_exception_for=row["governed_exception_for"], approved_by=row["approved_by"],
            approval_expires_at=row["approval_expires_at"],
            allowed_agents=json.loads(row["allowed_agents_json"]),
            allowed_roles=json.loads(row["allowed_roles_json"]),
            purpose_allowlist=json.loads(row["purpose_allowlist_json"]),
            created_at=row["created_at"], metadata=json.loads(row["metadata_json"]),
        )
