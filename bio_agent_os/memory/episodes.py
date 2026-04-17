"""
Episode storage for Bio-Agent OS V2.

Episodes are the auditable ground-truth stream behind later semantic,
procedural, and persona memories.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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
        self._filepath = os.path.join(storage_dir, f"{agent_name}_episodes.json")
        self._episodes: List[Dict[str, Any]] = []
        self.load()

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
        self._episodes.append(record)
        self.save()
        return record

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._episodes[-limit:]

    def get(self, episode_id: str) -> Optional[Dict[str, Any]]:
        for episode in self._episodes:
            if episode["episode_id"] == episode_id:
                return episode
        return None

    def query(
        self,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        results = self._episodes
        if task_id is not None:
            results = [episode for episode in results if episode.get("task_id") == task_id]
        if workspace_id is not None:
            results = [episode for episode in results if episode.get("workspace_id") == workspace_id]
        if project_version is not None:
            results = [
                episode for episode in results
                if episode.get("project_version") == project_version
            ]
        return results[-limit:]

    @property
    def count(self) -> int:
        return len(self._episodes)

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(self._episodes, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    self._episodes = json.load(handle)
            except (json.JSONDecodeError, OSError):
                self._episodes = []
