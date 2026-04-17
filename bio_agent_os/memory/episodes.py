"""
Episode storage for Bio-Agent OS V2.
"""

import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bio_agent_os.core.sqlite_store import SQLiteStore


class EpisodeRecord(BaseModel):
    episode_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = Field(default_factory=time.time)
    task_id: Optional[str] = None
    workspace_id: Optional[str] = None
    project_version: Optional[str] = None
    actor: str = "unknown"
    source: str = "unknown"
    observation_type: str = "observation"
    raw_payload: str
    inferred_intent: Optional[str] = None
    topic: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float = 0.5
    tags: List[str] = Field(default_factory=list)
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EpisodeStore:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_episodes.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_episodes"
        self._ensure_table()
        self._migrate_legacy_json()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                episode_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                task_id TEXT,
                workspace_id TEXT,
                project_version TEXT,
                actor TEXT NOT NULL,
                source TEXT NOT NULL,
                observation_type TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                inferred_intent TEXT,
                topic TEXT,
                outcome TEXT,
                confidence REAL NOT NULL,
                tags_json TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT episode_id FROM {self._table} LIMIT 1")
        if existing:
            return
        import json

        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                records = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        rows = []
        for record in records:
            rows.append(
                (
                    record["episode_id"],
                    float(record.get("timestamp", time.time())),
                    record.get("task_id"),
                    record.get("workspace_id"),
                    record.get("project_version"),
                    record.get("actor", "unknown"),
                    record.get("source", "unknown"),
                    record.get("observation_type", "observation"),
                    record.get("raw_payload", ""),
                    record.get("inferred_intent"),
                    record.get("topic"),
                    record.get("outcome"),
                    float(record.get("confidence", 0.5)),
                    self._store.dumps_json(record.get("tags", [])),
                    self._store.dumps_json(record.get("source_refs", [])),
                    self._store.dumps_json(record.get("metadata", {})),
                )
            )
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    episode_id, timestamp, task_id, workspace_id, project_version,
                    actor, source, observation_type, raw_payload, inferred_intent,
                    topic, outcome, confidence, tags_json, source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _normalize_source_refs(self, source_refs: Optional[List[Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for ref in source_refs or []:
            if isinstance(ref, dict):
                normalized.append(ref)
            else:
                normalized.append({"ref": str(ref)})
        return normalized

    def add(
        self,
        raw_payload: str,
        actor: str = "unknown",
        source: str = "unknown",
        observation_type: str = "observation",
        inferred_intent: Optional[str] = None,
        topic: Optional[str] = None,
        outcome: Optional[str] = None,
        confidence: float = 0.5,
        tags: Optional[List[str]] = None,
        source_refs: Optional[List[Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = EpisodeRecord(
            raw_payload=raw_payload,
            actor=actor,
            source=source,
            observation_type=observation_type,
            inferred_intent=inferred_intent,
            topic=topic,
            outcome=outcome,
            confidence=confidence,
            tags=tags or [],
            source_refs=self._normalize_source_refs(source_refs),
            metadata=metadata or {},
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
        ).model_dump()
        self._store.execute(
            f"""
            INSERT OR REPLACE INTO {self._table} (
                episode_id, timestamp, task_id, workspace_id, project_version,
                actor, source, observation_type, raw_payload, inferred_intent,
                topic, outcome, confidence, tags_json, source_refs_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["episode_id"],
                record["timestamp"],
                record.get("task_id"),
                record.get("workspace_id"),
                record.get("project_version"),
                record["actor"],
                record["source"],
                record["observation_type"],
                record["raw_payload"],
                record.get("inferred_intent"),
                record.get("topic"),
                record.get("outcome"),
                record["confidence"],
                self._store.dumps_json(record.get("tags", [])),
                self._store.dumps_json(record.get("source_refs", [])),
                self._store.dumps_json(record.get("metadata", {})),
            ],
        )
        return record

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} ORDER BY timestamp DESC LIMIT ?",
            [limit],
        )
        return [self._row_to_record(row) for row in reversed(rows)]

    def get(self, episode_id: str) -> Optional[Dict[str, Any]]:
        row = self._store.fetchone(
            f"SELECT * FROM {self._table} WHERE episode_id = ?",
            [episode_id],
        )
        return self._row_to_record(row) if row else None

    def get_many(self, episode_ids: List[str]) -> List[Dict[str, Any]]:
        if not episode_ids:
            return []
        placeholders = ",".join(["?"] * len(episode_ids))
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} WHERE episode_id IN ({placeholders}) ORDER BY timestamp ASC",
            episode_ids,
        )
        return [self._row_to_record(row) for row in rows]

    def query(
        self,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        conditions = []
        parameters: List[Any] = []
        if task_id is not None:
            conditions.append("task_id = ?")
            parameters.append(task_id)
        if workspace_id is not None:
            conditions.append("workspace_id = ?")
            parameters.append(workspace_id)
        if project_version is not None:
            conditions.append("project_version = ?")
            parameters.append(project_version)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} {where} ORDER BY timestamp DESC LIMIT ?",
            parameters,
        )
        return [self._row_to_record(row) for row in reversed(rows)]

    @property
    def count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0

    def _row_to_record(self, row) -> Dict[str, Any]:
        return {
            "episode_id": row["episode_id"],
            "timestamp": row["timestamp"],
            "task_id": row["task_id"],
            "workspace_id": row["workspace_id"],
            "project_version": row["project_version"],
            "actor": row["actor"],
            "source": row["source"],
            "observation_type": row["observation_type"],
            "raw_payload": row["raw_payload"],
            "inferred_intent": row["inferred_intent"],
            "topic": row["topic"],
            "outcome": row["outcome"],
            "confidence": row["confidence"],
            "tags": self._store.loads_json(row["tags_json"], []),
            "source_refs": self._store.loads_json(row["source_refs_json"], []),
            "metadata": self._store.loads_json(row["metadata_json"], {}),
        }
