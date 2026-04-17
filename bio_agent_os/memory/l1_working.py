"""
Attention-based working memory for Bio-Agent OS.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
        self._filepath = os.path.join(storage_dir, f"{agent_name}_l1_memory.json")
        self.load()

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
        return self._clamp(
            (0.30 * entry["task_relevance"])
            + (0.20 * entry["novelty"])
            + (0.20 * entry["unresolved_status"])
            + (0.15 * entry["recency_score"])
            + (0.15 * entry["severity"])
        )

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
        return self._entries[-n:]

    def get_focus_set(self, limit: int = 5, include_encoded: bool = False) -> List[Dict[str, Any]]:
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
        self._refresh_attention()
        return list(self._entries)

    def get_raw(self) -> List[Dict[str, Any]]:
        self._refresh_attention()
        return [entry for entry in self._entries if entry["status"] == "raw"]

    def get_survivors(self) -> List[Dict[str, Any]]:
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

    def mark_encoded(self, timestamp: float):
        for entry in self._entries:
            if entry["timestamp"] == timestamp:
                entry["status"] = "encoded"
        self.save()

    def remove_by_timestamps(self, timestamps: List[float]):
        ts_set = set(timestamps)
        self._entries = [entry for entry in self._entries if entry["timestamp"] not in ts_set]
        self.save()

    def clear(self):
        self._entries.clear()
        self.save()

    @property
    def count(self) -> int:
        return len(self._entries)

    def increment_nights(self):
        for entry in self._entries:
            entry["nights_passed"] = entry.get("nights_passed", 0) + 1
        self._refresh_attention()
        self.save()

    def build_context_string(self, n: int = 5) -> str:
        focus_entries = self.get_focus_set(limit=n)
        if not focus_entries:
            return "(No recent working-memory events.)"
        return "\n".join(
            f"- [{entry['source']}] {entry['content']} "
            f"(attention={entry['attention_score']:.2f}, unresolved={entry['unresolved_status']:.2f})"
            for entry in focus_entries
        )

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(self._entries, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    self._entries = json.load(handle)
            except (json.JSONDecodeError, OSError):
                self._entries = []
        self._refresh_attention()

    def __repr__(self) -> str:
        return f"L1WorkingMemory(agent='{self.agent_name}', entries={self.count})"
