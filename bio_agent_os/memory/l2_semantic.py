"""
Long-term semantic memory with real embedding support and state-dependent retrieval.
"""

import math
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.embedder import Embedder

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False


class L2SemanticMemory:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.collection_name = f"{agent_name}_l2"
        self.decay_lambda = 0.05
        self.embedder = Embedder()

        url = os.getenv("QDRANT_URL", None)
        api_key = os.getenv("QDRANT_API_KEY", None)

        if QDRANT_AVAILABLE:
            if url:
                self.client = QdrantClient(url=url, api_key=api_key)
            else:
                self.client = QdrantClient(":memory:")
            self._ensure_collection()
            self._fallback = False
        else:
            self._fallback = True
            self._entries: List[Dict[str, Any]] = []

    def _ensure_collection(self):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.embedder.dimensions, distance=Distance.COSINE),
            )
            return
        config = self.client.get_collection(self.collection_name).config.params.vectors
        existing_size = getattr(config, "size", None)
        if existing_size and int(existing_size) != int(self.embedder.dimensions):
            self.client.delete_collection(self.collection_name)
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.embedder.dimensions, distance=Distance.COSINE),
            )

    def _normalize_mode_hints(self, mode_hints: Optional[List[str]]) -> List[str]:
        return sorted({str(mode).strip().lower() for mode in (mode_hints or []) if str(mode).strip()})

    def _build_payload(
        self,
        content: str,
        importance: float,
        tags: Optional[List[str]],
        source_rule_id: Optional[str],
        memory_type: str,
        scope: str,
        mode_hints: Optional[List[str]],
        risk_level: str,
        stress_state: str,
        exception_for: Optional[str],
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "entry_id": str(uuid.uuid4()),
            "content": content,
            "importance": importance,
            "tags": tags or [],
            "source_rule_id": source_rule_id,
            "timestamp": time.time(),
            "memory_type": memory_type,
            "scope": scope,
            "mode_hints": self._normalize_mode_hints(mode_hints),
            "risk_level": risk_level,
            "stress_state": stress_state,
            "exception_for": exception_for,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "project_version": project_version,
            "embedding_backend": self.embedder.effective_backend,
            "embedding_model": self.embedder.model_id,
        }

    def store(
        self,
        content: str,
        importance: float = 5.0,
        tags: Optional[List[str]] = None,
        source_rule_id: Optional[str] = None,
        memory_type: str = "semantic",
        scope: str = "project",
        mode_hints: Optional[List[str]] = None,
        risk_level: str = "medium",
        stress_state: str = "normal",
        exception_for: Optional[str] = None,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
    ):
        payload = self._build_payload(
            content=content,
            importance=importance,
            tags=tags,
            source_rule_id=source_rule_id,
            memory_type=memory_type,
            scope=scope,
            mode_hints=mode_hints,
            risk_level=risk_level,
            stress_state=stress_state,
            exception_for=exception_for,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
        )

        if self._fallback:
            payload["vector"] = self.embedder.embed(content)
            self._entries.append(payload)
            return

        point = PointStruct(
            id=payload["entry_id"],
            vector=self.embedder.embed(content),
            payload=payload,
        )
        self.client.upsert(collection_name=self.collection_name, points=[point])

    def store_exception(
        self,
        content: str,
        exception_for: str,
        importance: float = 8.0,
        tags: Optional[List[str]] = None,
        source_rule_id: Optional[str] = None,
        scope: str = "project",
        mode_hints: Optional[List[str]] = None,
        risk_level: str = "high",
        stress_state: str = "failure",
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
    ):
        self.store(
            content=content,
            importance=importance,
            tags=(tags or []) + ["exception"],
            source_rule_id=source_rule_id,
            memory_type="exception",
            scope=scope,
            mode_hints=mode_hints,
            risk_level=risk_level,
            stress_state=stress_state,
            exception_for=exception_for,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
        )

    def _state_boost(self, payload: Dict[str, Any], retrieval_state: Optional[Dict[str, Any]]) -> float:
        if not retrieval_state:
            return 1.0

        boost = 1.0
        mode = str(retrieval_state.get("mode", "")).strip().lower()
        risk_level = str(retrieval_state.get("risk_level", "")).strip().lower()
        stress_state = str(retrieval_state.get("stress_state", "")).strip().lower()
        task_id = retrieval_state.get("task_id")
        workspace_id = retrieval_state.get("workspace_id")
        project_version = retrieval_state.get("project_version")
        prefer_exception = bool(retrieval_state.get("prefer_exception", False))

        if mode and mode in payload.get("mode_hints", []):
            boost += 0.45
        if task_id and payload.get("task_id") == task_id:
            boost += 0.25
        if workspace_id and payload.get("workspace_id") == workspace_id:
            boost += 0.2
        if project_version and payload.get("project_version") == project_version:
            boost += 0.15
        if risk_level and payload.get("risk_level") == risk_level:
            boost += 0.2
        if stress_state and payload.get("stress_state") == stress_state:
            boost += 0.15
        if prefer_exception and payload.get("memory_type") == "exception":
            boost += 0.6

        if mode == "debug":
            if payload.get("memory_type") in {"exception", "procedural"}:
                boost += 0.25
        elif mode == "implement":
            if payload.get("memory_type") == "procedural":
                boost += 0.35
            elif payload.get("memory_type") == "semantic":
                boost += 0.18
        elif mode == "refactor":
            if payload.get("memory_type") == "semantic":
                boost += 0.32
            elif payload.get("memory_type") == "procedural":
                boost += 0.12
            elif payload.get("memory_type") == "exception" and prefer_exception:
                boost += 0.12
        elif mode == "deploy":
            if payload.get("memory_type") in {"exception", "procedural"}:
                boost += 0.3

        return boost

    def _cosine_similarity(self, left: List[float], right: List[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    def _rank_entries(
        self,
        entries: List[Dict[str, Any]],
        query: str,
        top_k: int,
        retrieval_state: Optional[Dict[str, Any]] = None,
        query_vector: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        now = time.time()
        query_terms = {term for term in query.lower().split() if term}
        query_vector = query_vector or self.embedder.embed(query)
        for payload in entries:
            importance = float(payload["importance"])
            timestamp = float(payload["timestamp"])
            days_elapsed = (now - timestamp) / 86400
            decay = math.exp(-self.decay_lambda * days_elapsed)

            semantic_score = 0.6
            if "vector" in payload:
                semantic_score = max(0.0, self._cosine_similarity(query_vector, payload["vector"]))

            lexical_bonus = 0.0
            content_terms = set(str(payload["content"]).lower().split())
            tag_terms = {str(tag).lower() for tag in payload.get("tags", [])}
            overlap = len(query_terms & (content_terms | tag_terms))
            if query_terms:
                lexical_bonus = min(0.2, overlap / max(len(query_terms), 1) * 0.2)

            state_boost = self._state_boost(payload, retrieval_state)
            final_score = (semantic_score + lexical_bonus) * importance * decay * state_boost

            results.append(
                {
                    "content": payload["content"],
                    "score": final_score,
                    "importance": importance,
                    "tags": payload.get("tags", []),
                    "memory_type": payload.get("memory_type", "semantic"),
                    "scope": payload.get("scope", "project"),
                    "mode_hints": payload.get("mode_hints", []),
                    "exception_for": payload.get("exception_for"),
                    "risk_level": payload.get("risk_level", "medium"),
                    "stress_state": payload.get("stress_state", "normal"),
                    "task_id": payload.get("task_id"),
                    "workspace_id": payload.get("workspace_id"),
                    "project_version": payload.get("project_version"),
                    "embedding_backend": payload.get("embedding_backend", self.embedder.effective_backend),
                    "embedding_model": payload.get("embedding_model", self.embedder.model_id),
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    def search(
        self,
        query: str,
        top_k: int = 5,
        retrieval_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        query_vector = self.embedder.embed(query)
        if self._fallback:
            return self._rank_entries(
                self._entries,
                query=query,
                top_k=top_k,
                retrieval_state=retrieval_state,
                query_vector=query_vector,
            )

        points_data = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k * 3,
        )
        entries = [dict(hit.payload) for hit in points_data.points]
        return self._rank_entries(
            entries,
            query=query,
            top_k=top_k,
            retrieval_state=retrieval_state,
            query_vector=query_vector,
        )

    def prune_decayed(self, threshold: float = 1.0) -> int:
        if self._fallback:
            before = len(self._entries)
            now = time.time()
            self._entries = [
                entry for entry in self._entries
                if entry["importance"] * math.exp(-self.decay_lambda * ((now - entry["timestamp"]) / 86400)) >= threshold
            ]
            return before - len(self._entries)

        points, _ = self.client.scroll(collection_name=self.collection_name, limit=1000)
        to_delete = []
        now = time.time()

        for point in points:
            days_elapsed = (now - point.payload["timestamp"]) / 86400
            decay_score = point.payload["importance"] * math.exp(-self.decay_lambda * days_elapsed)
            if decay_score < threshold:
                to_delete.append(point.id)

        if to_delete:
            self.client.delete(collection_name=self.collection_name, points_selector=to_delete)

        return len(to_delete)

    @property
    def count(self) -> int:
        if self._fallback:
            return len(self._entries)
        return self.client.count(collection_name=self.collection_name).count
