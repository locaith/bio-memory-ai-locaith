"""
Persistent dream reports.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List

from bio_agent_os.core.sqlite_store import SQLiteStore


class DreamJournal:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_dream_reports.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_dream_reports"
        self._ensure_table()
        self._migrate_legacy_json()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                report_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT report_id FROM {self._table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                reports = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        rows = []
        for report in reports:
            report_id = report.get("report_id", str(uuid.uuid4()))
            timestamp = float(report.get("timestamp", time.time()))
            payload = dict(report)
            payload["report_id"] = report_id
            payload["timestamp"] = timestamp
            rows.append((report_id, timestamp, self._store.dumps_json(payload)))
        if rows:
            self._store.executemany(
                f"INSERT OR REPLACE INTO {self._table} (report_id, timestamp, report_json) VALUES (?, ?, ?)",
                rows,
            )

    def append(self, report: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "report_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            **report,
        }
        self._store.execute(
            f"INSERT OR REPLACE INTO {self._table} (report_id, timestamp, report_json) VALUES (?, ?, ?)",
            [
                entry["report_id"],
                entry["timestamp"],
                self._store.dumps_json(entry),
            ],
        )
        return entry

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._store.fetchall(
            f"SELECT report_json FROM {self._table} ORDER BY timestamp DESC LIMIT ?",
            [limit],
        )
        return [self._store.loads_json(row["report_json"], {}) for row in reversed(rows)]

    @property
    def count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0
