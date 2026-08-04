from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bio_agent_os.cognitive.sqlite_utils import connect_sqlite

from .models import AgentCheckpoint


class CheckpointManager:
    """Durable, versioned agent continuity checkpoints."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = connect_sqlite(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS agent_checkpoints(
                checkpoint_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                workspace_id TEXT,
                goal TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoint_latest ON agent_checkpoints(tenant_id,agent_id,workspace_id,created_at)"
        )
        self.conn.commit()

    def save(self, checkpoint: AgentCheckpoint) -> AgentCheckpoint:
        payload = {
            "completed_steps": list(checkpoint.completed_steps),
            "pending_steps": list(checkpoint.pending_steps),
            "open_hypotheses": list(checkpoint.open_hypotheses),
            "uncertainties": list(checkpoint.uncertainties),
            "tool_state": checkpoint.tool_state,
            "world_state": checkpoint.world_state,
            "self_state": checkpoint.self_state,
            "active_memory_ids": list(checkpoint.active_memory_ids),
        }
        self.conn.execute(
            """INSERT INTO agent_checkpoints(
               checkpoint_id,tenant_id,agent_id,workspace_id,goal,payload_json,
               parent_checkpoint_id,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                checkpoint.checkpoint_id, checkpoint.tenant_id, checkpoint.agent_id,
                checkpoint.workspace_id, checkpoint.goal,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                checkpoint.parent_checkpoint_id, checkpoint.created_at,
                json.dumps(checkpoint.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return checkpoint

    def get(self, checkpoint_id: str, tenant_id: str) -> AgentCheckpoint | None:
        row = self.conn.execute(
            "SELECT * FROM agent_checkpoints WHERE checkpoint_id=? AND tenant_id=?", (checkpoint_id, tenant_id)
        ).fetchone()
        return self._row(row) if row else None

    def latest(self, tenant_id: str, agent_id: str, workspace_id: str | None = None) -> AgentCheckpoint | None:
        row = self.conn.execute(
            """SELECT * FROM agent_checkpoints WHERE tenant_id=? AND agent_id=?
               AND workspace_id IS ? ORDER BY created_at DESC LIMIT 1""",
            (tenant_id, agent_id, workspace_id),
        ).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> AgentCheckpoint:
        payload = json.loads(row["payload_json"])
        return AgentCheckpoint(
            checkpoint_id=row["checkpoint_id"], tenant_id=row["tenant_id"], agent_id=row["agent_id"],
            workspace_id=row["workspace_id"], goal=row["goal"],
            completed_steps=tuple(payload["completed_steps"]), pending_steps=tuple(payload["pending_steps"]),
            open_hypotheses=tuple(payload["open_hypotheses"]), uncertainties=tuple(payload["uncertainties"]),
            tool_state=payload["tool_state"], world_state=payload["world_state"], self_state=payload["self_state"],
            active_memory_ids=tuple(payload["active_memory_ids"]), parent_checkpoint_id=row["parent_checkpoint_id"],
            created_at=row["created_at"], metadata=json.loads(row["metadata_json"]),
        )
