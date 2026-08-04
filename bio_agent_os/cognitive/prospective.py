from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .sqlite_utils import connect_sqlite
from typing import Any

from .models import ProspectiveTrigger


class ProspectiveMemory:
    """Condition-based future memory, not a wall-clock scheduler.

    It answers: "when state X becomes true, what obligation must the agent
    remember?" External schedulers can call ``evaluate`` whenever state changes.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = connect_sqlite(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS prospective_triggers(
                tenant_id TEXT NOT NULL,
                trigger_id TEXT NOT NULL,
                condition_json TEXT NOT NULL,
                action TEXT NOT NULL,
                priority REAL NOT NULL,
                expires_at TEXT,
                requires_approval INTEGER NOT NULL,
                source_memory_id TEXT,
                fired_at TEXT,
                PRIMARY KEY(tenant_id, trigger_id)
            )"""
        )
        self.conn.commit()

    def add(self, tenant_id: str, trigger: ProspectiveTrigger) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO prospective_triggers(
                tenant_id,trigger_id,condition_json,action,priority,expires_at,
                requires_approval,source_memory_id,fired_at
            ) VALUES(?,?,?,?,?,?,?,?,NULL)""",
            (
                tenant_id, trigger.trigger_id, json.dumps(trigger.condition, sort_keys=True),
                trigger.action, trigger.priority, trigger.expires_at,
                int(trigger.requires_approval), trigger.source_memory_id,
            ),
        )
        self.conn.commit()

    def evaluate(self, tenant_id: str, state: dict[str, Any], now: str | None = None) -> list[ProspectiveTrigger]:
        current = now or datetime.now(timezone.utc).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM prospective_triggers WHERE tenant_id=? AND fired_at IS NULL",
            (tenant_id,),
        ).fetchall()
        due: list[ProspectiveTrigger] = []
        for row in rows:
            if row["expires_at"] and current >= row["expires_at"]:
                continue
            condition = json.loads(row["condition_json"])
            if self._matches(condition, state):
                due.append(
                    ProspectiveTrigger(
                        trigger_id=row["trigger_id"], condition=condition, action=row["action"],
                        priority=row["priority"], expires_at=row["expires_at"],
                        requires_approval=bool(row["requires_approval"]),
                        source_memory_id=row["source_memory_id"],
                    )
                )
        return sorted(due, key=lambda item: -item.priority)

    def mark_fired(self, tenant_id: str, trigger_id: str, when: str | None = None) -> None:
        self.conn.execute(
            "UPDATE prospective_triggers SET fired_at=? WHERE tenant_id=? AND trigger_id=?",
            (when or datetime.now(timezone.utc).isoformat(), tenant_id, trigger_id),
        )
        self.conn.commit()

    @classmethod
    def _matches(cls, condition: dict[str, Any], state: dict[str, Any]) -> bool:
        for key, expected in condition.items():
            actual = state.get(key)
            if isinstance(expected, dict):
                if "eq" in expected and actual != expected["eq"]:
                    return False
                if "gte" in expected and (actual is None or actual < expected["gte"]):
                    return False
                if "lte" in expected and (actual is None or actual > expected["lte"]):
                    return False
                if "contains" in expected and expected["contains"] not in (actual or []):
                    return False
            elif actual != expected:
                return False
        return True
