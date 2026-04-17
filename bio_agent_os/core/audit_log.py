"""
Persistent memory audit trail and replay.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


class AuditLog:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_audit_log.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_audit_log"
        self._ensure_table()
        self._migrate_legacy_json()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                event_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT event_id FROM {self._table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                events = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        rows = []
        for event in events:
            rows.append(
                (
                    event.get("event_id", str(uuid.uuid4())),
                    float(event.get("timestamp", time.time())),
                    event.get("event_type", "unknown"),
                    event.get("message", ""),
                    self._store.dumps_json(event.get("payload", {})),
                )
            )
        if rows:
            self._store.executemany(
                f"INSERT OR REPLACE INTO {self._table} (event_id, timestamp, event_type, message, payload_json) VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def _row_to_event(self, row) -> Dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "event_type": row["event_type"],
            "message": row["message"],
            "payload": self._store.loads_json(row["payload_json"], {}),
        }

    def append(
        self,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "event_type": event_type,
            "message": message,
            "payload": payload or {},
        }
        self._store.execute(
            f"INSERT OR REPLACE INTO {self._table} (event_id, timestamp, event_type, message, payload_json) VALUES (?, ?, ?, ?, ?)",
            [
                event["event_id"],
                event["timestamp"],
                event["event_type"],
                event["message"],
                self._store.dumps_json(event["payload"]),
            ],
        )
        return event

    def recent(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if event_type:
            rows = self._store.fetchall(
                f"SELECT * FROM {self._table} WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
                [event_type, limit],
            )
        else:
            rows = self._store.fetchall(
                f"SELECT * FROM {self._table} ORDER BY timestamp DESC LIMIT ?",
                [limit],
            )
        return [self._row_to_event(row) for row in reversed(rows)]

    def replay(self, since: float = 0.0, until: Optional[float] = None) -> List[Dict[str, Any]]:
        until = until or time.time()
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
            [since, until],
        )
        return [self._row_to_event(row) for row in rows]

    @property
    def count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0
