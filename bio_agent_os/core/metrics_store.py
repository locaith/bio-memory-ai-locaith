"""
Persistent metrics store for benchmark and real-world evaluation runs.
"""

import time
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


class MetricsStore:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_metrics_runs"
        self._ensure_table()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                run_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                suite TEXT NOT NULL,
                model TEXT NOT NULL,
                metrics_json TEXT NOT NULL
            )
            """
        )

    def append(self, suite: str, model: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "run_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "suite": suite,
            "model": model,
            "metrics": metrics,
        }
        self._store.execute(
            f"INSERT OR REPLACE INTO {self._table} (run_id, timestamp, suite, model, metrics_json) VALUES (?, ?, ?, ?, ?)",
            [
                entry["run_id"],
                entry["timestamp"],
                entry["suite"],
                entry["model"],
                self._store.dumps_json(entry["metrics"]),
            ],
        )
        return entry

    def recent(self, limit: int = 10, suite: Optional[str] = None) -> List[Dict[str, Any]]:
        if suite:
            rows = self._store.fetchall(
                f"SELECT * FROM {self._table} WHERE suite = ? ORDER BY timestamp DESC LIMIT ?",
                [suite, limit],
            )
        else:
            rows = self._store.fetchall(
                f"SELECT * FROM {self._table} ORDER BY timestamp DESC LIMIT ?",
                [limit],
            )
        return [
            {
                "run_id": row["run_id"],
                "timestamp": row["timestamp"],
                "suite": row["suite"],
                "model": row["model"],
                "metrics": self._store.loads_json(row["metrics_json"], {}),
            }
            for row in reversed(rows)
        ]
