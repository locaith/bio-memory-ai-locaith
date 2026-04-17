"""
Human approval queue for sensitive rule promotion and deprecation.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


SENSITIVE_MARKERS = {
    "force push",
    "git push -f",
    "auth",
    "authentication",
    "security",
    "credential",
    "secret",
    "migration",
    "payment",
    "production",
    "prod",
    "delete",
}


class ApprovalQueue:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_approval_queue.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_approval_queue"
        self._ensure_table()
        self._migrate_legacy_json()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                request_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL,
                action_type TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                scope TEXT NOT NULL,
                confidence REAL NOT NULL,
                target_rule_id TEXT,
                metadata_json TEXT NOT NULL,
                reviewed_by TEXT
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT request_id FROM {self._table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                requests = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        rows = []
        for request in requests:
            rows.append(
                (
                    request.get("request_id", str(uuid.uuid4())),
                    float(request.get("created_at", time.time())),
                    float(request.get("updated_at", time.time())),
                    request.get("status", "pending"),
                    request.get("action_type", "unknown"),
                    request.get("rule_text", ""),
                    request.get("scope", "project"),
                    float(request.get("confidence", 0.0)),
                    request.get("target_rule_id"),
                    self._store.dumps_json(request.get("metadata", {})),
                    request.get("reviewed_by"),
                )
            )
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    request_id, created_at, updated_at, status, action_type, rule_text,
                    scope, confidence, target_rule_id, metadata_json, reviewed_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _row_to_request(self, row) -> Dict[str, Any]:
        request = {
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": row["status"],
            "action_type": row["action_type"],
            "rule_text": row["rule_text"],
            "scope": row["scope"],
            "confidence": row["confidence"],
            "target_rule_id": row["target_rule_id"],
            "metadata": self._store.loads_json(row["metadata_json"], {}),
        }
        if row["reviewed_by"]:
            request["reviewed_by"] = row["reviewed_by"]
        return request

    def requires_approval(self, rule_text: str, scope: str = "project", confidence: float = 0.0) -> bool:
        normalized = rule_text.lower()
        if scope == "core":
            return True
        if confidence >= 0.9:
            return True
        return any(marker in normalized for marker in SENSITIVE_MARKERS)

    def submit(
        self,
        action_type: str,
        rule_text: str,
        scope: str = "project",
        confidence: float = 0.0,
        target_rule_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = self._store.fetchone(
            f"""
            SELECT * FROM {self._table}
            WHERE status = 'pending'
              AND action_type = ?
              AND rule_text = ?
              AND COALESCE(target_rule_id, '') = COALESCE(?, '')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [action_type, rule_text, target_rule_id],
        )
        if existing:
            return self._row_to_request(existing)

        request = {
            "request_id": str(uuid.uuid4()),
            "created_at": time.time(),
            "updated_at": time.time(),
            "status": "pending",
            "action_type": action_type,
            "rule_text": rule_text,
            "scope": scope,
            "confidence": confidence,
            "target_rule_id": target_rule_id,
            "metadata": metadata or {},
        }
        self._store.execute(
            f"""
            INSERT OR REPLACE INTO {self._table} (
                request_id, created_at, updated_at, status, action_type, rule_text,
                scope, confidence, target_rule_id, metadata_json, reviewed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                request["request_id"],
                request["created_at"],
                request["updated_at"],
                request["status"],
                request["action_type"],
                request["rule_text"],
                request["scope"],
                request["confidence"],
                request["target_rule_id"],
                self._store.dumps_json(request["metadata"]),
                None,
            ],
        )
        return request

    def resolve(self, request_id: str, decision: str, reviewer: str = "human") -> Optional[Dict[str, Any]]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected.")
        existing = self._store.fetchone(
            f"SELECT * FROM {self._table} WHERE request_id = ?",
            [request_id],
        )
        if not existing:
            return None
        updated_at = time.time()
        self._store.execute(
            f"""
            UPDATE {self._table}
            SET status = ?, reviewed_by = ?, updated_at = ?
            WHERE request_id = ?
            """,
            [decision, reviewer, updated_at, request_id],
        )
        resolved = self._store.fetchone(
            f"SELECT * FROM {self._table} WHERE request_id = ?",
            [request_id],
        )
        return self._row_to_request(resolved) if resolved else None

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        row = self._store.fetchone(
            f"SELECT * FROM {self._table} WHERE request_id = ?",
            [request_id],
        )
        return self._row_to_request(row) if row else None

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if status:
            rows = self._store.fetchall(
                f"""
                SELECT * FROM {self._table}
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [status, limit],
            )
        else:
            rows = self._store.fetchall(
                f"SELECT * FROM {self._table} ORDER BY created_at DESC LIMIT ?",
                [limit],
            )
        return [self._row_to_request(row) for row in reversed(rows)]

    @property
    def pending_count(self) -> int:
        row = self._store.fetchone(
            f"SELECT COUNT(*) AS total FROM {self._table} WHERE status = 'pending'"
        )
        return int(row["total"]) if row else 0
