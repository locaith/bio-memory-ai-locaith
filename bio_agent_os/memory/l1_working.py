"""
Attention-based working memory for Bio-Agent OS.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bio_agent_os.core.sqlite_store import SQLiteStore


class MemoryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    source: str = "unknown"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
    status: str = "raw"
    nights_passed: int = 0
    ttl: int = 2
    episode_id: Optional[str] = None
    task_id: Optional[str] = None
    workspace_id: Optional[str] = None
    project_version: Optional[str] = None
    salience: float = 0.5
    recency_score: float = 1.0
    novelty: float = 0.5
    severity: float = 0.5
    task_relevance: float = 0.5
    unresolved_status: float = 0.0
    attention_score: float = 0.5


class L1WorkingMemory:
    """
    Short-term working memory with an attention scheduler.

    Instead of a pure FIFO buffer, entries compete for the focus set using:
    - task relevance
    - novelty
    - unresolvedness
    - recency
    - severity
    """

    def __init__(
        self,
        agent_name: str = "Bio-AI",
        storage_dir: str = "data",
        max_entries: int = 100,
    ):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self.max_entries = max_entries
        self._entries: List[Dict[str, Any]] = []
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_l1_memory.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_l1_memory"
        self._ensure_table()
        self._migrate_legacy_json()
        self.load()

    def _homeostasis_state(self) -> Dict[str, float]:
        recent = self._entries[-12:]
        if not recent:
            return {
                "stress_level": 0.0,
                "global_gain": 1.0,
                "failure_streak": 0.0,
                "task_weight": 0.30,
                "novelty_weight": 0.20,
                "recency_weight": 0.15,
                "severity_weight": 0.15,
                "unresolved_weight": 0.20,
            }

        unresolved_ratio = sum(float(entry.get("unresolved_status", 0.0)) for entry in recent) / max(len(recent), 1)
        severity_avg = sum(float(entry.get("severity", 0.5)) for entry in recent) / max(len(recent), 1)
        raw_failure_streak = 0
        last_failure_timestamp = None
        for entry in reversed(recent):
            if float(entry.get("unresolved_status", 0.0)) >= 0.5 or float(entry.get("severity", 0.0)) >= 0.75:
                raw_failure_streak += 1
                last_failure_timestamp = float(entry.get("timestamp", time.time()))
            else:
                break
        failure_streak = min(1.0, raw_failure_streak / 5.0)
        if last_failure_timestamp is None:
            last_failure_timestamp = max(float(entry.get("timestamp", time.time())) for entry in recent)
        hours_since_last_failure = max(0.0, (time.time() - last_failure_timestamp) / 3600.0)
        decay_factor = max(0.35, 1.0 - min(hours_since_last_failure / 8.0, 0.65))
        stress_level = self._clamp(((0.45 * unresolved_ratio) + (0.35 * severity_avg) + (0.20 * failure_streak)) * decay_factor)
        severity_weight = 0.15 + (0.20 * stress_level)
        unresolved_weight = 0.20 + (0.10 * stress_level)
        recency_weight = max(0.05, 0.15 - (0.05 * stress_level))
        novelty_weight = max(0.10, 0.20 - (0.05 * stress_level))
        task_weight = max(0.15, 1.0 - (severity_weight + unresolved_weight + recency_weight + novelty_weight))
        total = task_weight + novelty_weight + unresolved_weight + recency_weight + severity_weight
        return {
            "stress_level": stress_level,
            "global_gain": round(1.0 + stress_level, 3),
            "failure_streak": float(raw_failure_streak),
            "stress_decay_factor": decay_factor,
            "hours_since_last_failure": hours_since_last_failure,
            "task_weight": task_weight / total,
            "novelty_weight": novelty_weight / total,
            "unresolved_weight": unresolved_weight / total,
            "recency_weight": recency_weight / total,
            "severity_weight": severity_weight / total,
        }

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                entry_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL,
                nights_passed INTEGER NOT NULL,
                ttl INTEGER NOT NULL,
                episode_id TEXT,
                task_id TEXT,
                workspace_id TEXT,
                project_version TEXT,
                salience REAL NOT NULL,
                recency_score REAL NOT NULL,
                novelty REAL NOT NULL,
                severity REAL NOT NULL,
                task_relevance REAL NOT NULL,
                unresolved_status REAL NOT NULL,
                attention_score REAL NOT NULL
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT entry_id FROM {self._table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                entries = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return

        rows = []
        for entry in entries:
            rows.append(
                (
                    entry.get("entry_id", str(uuid.uuid4())),
                    entry.get("content", ""),
                    entry.get("source", "unknown"),
                    self._store.dumps_json(entry.get("metadata", {})),
                    float(entry.get("timestamp", time.time())),
                    entry.get("status", "raw"),
                    int(entry.get("nights_passed", 0)),
                    int(entry.get("ttl", 2)),
                    entry.get("episode_id"),
                    entry.get("task_id"),
                    entry.get("workspace_id"),
                    entry.get("project_version"),
                    float(entry.get("salience", 0.5)),
                    float(entry.get("recency_score", 1.0)),
                    float(entry.get("novelty", 0.5)),
                    float(entry.get("severity", 0.5)),
                    float(entry.get("task_relevance", 0.5)),
                    float(entry.get("unresolved_status", 0.0)),
                    float(entry.get("attention_score", 0.5)),
                )
            )
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    entry_id, content, source, metadata_json, timestamp, status,
                    nights_passed, ttl, episode_id, task_id, workspace_id, project_version,
                    salience, recency_score, novelty, severity, task_relevance,
                    unresolved_status, attention_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))

    def _infer_severity(self, content: str, metadata: Dict[str, Any]) -> float:
        if "severity" in metadata:
            return self._clamp(metadata["severity"])
        importance = float(metadata.get("importance_score", 5)) / 10.0
        lowered = content.lower()
        if any(token in lowered for token in ["fatal", "panic", "critical", "failed", "error"]):
            importance = max(importance, 0.8)
        if metadata.get("is_junk_or_transient"):
            importance = min(importance, 0.25)
        return self._clamp(importance)

    def _infer_task_relevance(self, metadata: Dict[str, Any]) -> float:
        if "task_relevance" in metadata:
            return self._clamp(metadata["task_relevance"])
        return self._clamp(float(metadata.get("importance_score", 5)) / 10.0)

    def _infer_unresolved_status(self, content: str, metadata: Dict[str, Any]) -> float:
        if "unresolved_status" in metadata:
            return self._clamp(metadata["unresolved_status"])
        if "unresolved" in metadata:
            return 1.0 if metadata["unresolved"] else 0.0
        lowered = content.lower()
        unresolved_markers = ["todo", "fixme", "failed", "error", "retry", "blocked", "exception"]
        return 1.0 if any(marker in lowered for marker in unresolved_markers) else 0.0

    def _infer_novelty(self, content: str, source: str, metadata: Dict[str, Any]) -> float:
        topic = str(metadata.get("topic", "")).strip().lower()
        if not self._entries:
            return 0.9

        similarities = 0
        for existing in self._entries[-20:]:
            existing_topic = str(existing.get("metadata", {}).get("topic", "")).strip().lower()
            same_source = existing.get("source") == source
            same_topic = topic and existing_topic == topic
            same_content = content[:120].lower() == existing.get("content", "")[:120].lower()
            if same_source and (same_topic or same_content):
                similarities += 1

        return self._clamp(0.9 - (similarities * 0.2))

    def _compute_attention(self, entry: Dict[str, Any]) -> float:
        state = self._homeostasis_state()
        weighted = (
            (state["task_weight"] * entry["task_relevance"])
            + (state["novelty_weight"] * entry["novelty"])
            + (state["unresolved_weight"] * entry["unresolved_status"])
            + (state["recency_weight"] * entry["recency_score"])
            + (state["severity_weight"] * entry["severity"])
        )
        return self._clamp(weighted * state["global_gain"])

    def _refresh_attention(self):
        now = time.time()
        for entry in self._entries:
            age_seconds = max(0.0, now - float(entry.get("timestamp", now)))
            age_penalty = min(age_seconds / 3600.0, 1.0)
            entry["recency_score"] = self._clamp(1.0 - (age_penalty * 0.35) - (entry.get("nights_passed", 0) * 0.1))
            entry["salience"] = self._clamp(
                (entry.get("severity", 0.5) * 0.5)
                + (entry.get("task_relevance", 0.5) * 0.3)
                + (entry.get("unresolved_status", 0.0) * 0.2)
            )
            entry["attention_score"] = self._compute_attention(entry)

    def _trim_overflow(self):
        if len(self._entries) <= self.max_entries:
            return
        self._refresh_attention()
        self._entries.sort(
            key=lambda item: (
                item.get("status") != "raw",
                item.get("attention_score", 0.0),
                item.get("timestamp", 0.0),
            )
        )
        self._entries = self._entries[-self.max_entries:]
        self._entries.sort(key=lambda item: item.get("timestamp", 0.0))

    def _row_to_entry(self, row) -> Dict[str, Any]:
        return {
            "entry_id": row["entry_id"],
            "content": row["content"],
            "source": row["source"],
            "metadata": self._store.loads_json(row["metadata_json"], {}),
            "timestamp": row["timestamp"],
            "status": row["status"],
            "nights_passed": row["nights_passed"],
            "ttl": row["ttl"],
            "episode_id": row["episode_id"],
            "task_id": row["task_id"],
            "workspace_id": row["workspace_id"],
            "project_version": row["project_version"],
            "salience": row["salience"],
            "recency_score": row["recency_score"],
            "novelty": row["novelty"],
            "severity": row["severity"],
            "task_relevance": row["task_relevance"],
            "unresolved_status": row["unresolved_status"],
            "attention_score": row["attention_score"],
        }

    def add(
        self,
        content: str,
        source: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
        ttl: int = 2,
        episode_id: Optional[str] = None,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        metadata = metadata or {}
        self.load()
        entry = MemoryEntry(
            content=content,
            source=source,
            metadata=metadata,
            ttl=ttl,
            episode_id=episode_id,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            novelty=self._infer_novelty(content, source, metadata),
            severity=self._infer_severity(content, metadata),
            task_relevance=self._infer_task_relevance(metadata),
            unresolved_status=self._infer_unresolved_status(content, metadata),
        ).model_dump()
        entry["salience"] = self._clamp(
            (entry["severity"] * 0.5)
            + (entry["task_relevance"] * 0.3)
            + (entry["unresolved_status"] * 0.2)
        )
        entry["attention_score"] = self._compute_attention(entry)

        self._entries.append(entry)
        self._trim_overflow()
        self.save()
        return entry

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        self.load()
        return self._entries[-n:]

    def get_focus_set(self, limit: int = 5, include_encoded: bool = False) -> List[Dict[str, Any]]:
        self.load()
        self._refresh_attention()
        entries = self._entries
        if not include_encoded:
            entries = [entry for entry in entries if entry.get("status") != "encoded"]
        ranked = sorted(
            entries,
            key=lambda item: (item.get("attention_score", 0.0), item.get("timestamp", 0.0)),
            reverse=True,
        )
        return ranked[:limit]

    def get_all(self) -> List[Dict[str, Any]]:
        self.load()
        self._refresh_attention()
        return list(self._entries)

    def get_raw(self) -> List[Dict[str, Any]]:
        self.load()
        self._refresh_attention()
        return [entry for entry in self._entries if entry["status"] == "raw"]

    def get_survivors(self) -> List[Dict[str, Any]]:
        self.load()
        self._refresh_attention()
        survivors = [
            entry for entry in self._entries
            if entry["status"] == "raw" and entry.get("nights_passed", 0) > entry.get("ttl", 2)
        ]
        return sorted(
            survivors,
            key=lambda item: (item.get("attention_score", 0.0), item.get("timestamp", 0.0)),
            reverse=True,
        )

    def mark_encoded(self, entry_id: str):
        # Match on the stable UUID, not the float timestamp: two events created
        # in the same millisecond share a timestamp, and float equality would
        # then mark/skip the wrong survivor (silent data loss at consolidation).
        self.load()
        for entry in self._entries:
            if str(entry.get("entry_id")) == str(entry_id):
                entry["status"] = "encoded"
                break
        self.save()

    def remove_by_ids(self, entry_ids: List[str]):
        self.load()
        id_set = {str(entry_id) for entry_id in entry_ids}
        self._entries = [entry for entry in self._entries if str(entry.get("entry_id")) not in id_set]
        self.save()

    def clear(self):
        self._entries.clear()
        self._store.execute(f"DELETE FROM {self._table}")

    @property
    def count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0

    def increment_nights(self):
        self.load()
        for entry in self._entries:
            entry["nights_passed"] = entry.get("nights_passed", 0) + 1
        self._refresh_attention()
        self.save()

    def build_context_string(self, n: int = 5) -> str:
        focus_entries = self.get_focus_set(limit=n)
        if not focus_entries:
            return "(No recent working-memory events.)"
        state = self._homeostasis_state()
        return "\n".join(
            [
                (
                    f"(attention-homeostasis: stress={state['stress_level']:.2f}, "
                    f"gain={state['global_gain']:.2f}, failure_streak={state['failure_streak']:.0f})"
                )
            ]
            + [
                f"- [{entry['source']}] {entry['content']} "
            f"(attention={entry['attention_score']:.2f}, unresolved={entry['unresolved_status']:.2f})"
            for entry in focus_entries
            ]
        )

    def get_attention_state(self) -> Dict[str, float]:
        self.load()
        return self._homeostasis_state()

    def save(self):
        rows = [
            (
                entry["entry_id"],
                entry["content"],
                entry["source"],
                self._store.dumps_json(entry.get("metadata", {})),
                float(entry["timestamp"]),
                entry.get("status", "raw"),
                int(entry.get("nights_passed", 0)),
                int(entry.get("ttl", 2)),
                entry.get("episode_id"),
                entry.get("task_id"),
                entry.get("workspace_id"),
                entry.get("project_version"),
                float(entry.get("salience", 0.5)),
                float(entry.get("recency_score", 1.0)),
                float(entry.get("novelty", 0.5)),
                float(entry.get("severity", 0.5)),
                float(entry.get("task_relevance", 0.5)),
                float(entry.get("unresolved_status", 0.0)),
                float(entry.get("attention_score", 0.5)),
            )
            for entry in self._entries
        ]
        self._store.execute(f"DELETE FROM {self._table}")
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    entry_id, content, source, metadata_json, timestamp, status,
                    nights_passed, ttl, episode_id, task_id, workspace_id, project_version,
                    salience, recency_score, novelty, severity, task_relevance,
                    unresolved_status, attention_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load(self):
        rows = self._store.fetchall(f"SELECT * FROM {self._table} ORDER BY timestamp ASC")
        self._entries = [self._row_to_entry(row) for row in rows]
        self._refresh_attention()

    def __repr__(self) -> str:
        return f"L1WorkingMemory(agent='{self.agent_name}', entries={self.count})"
