"""
Long-term semantic memory with real embedding support and state-dependent retrieval.
"""

import math
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.embedder import Embedder
from bio_agent_os.core.sqlite_store import SQLiteStore

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchAny,
        PointStruct,
        VectorParams,
    )

    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False


class L2SemanticMemory:
    # Retrieval-aware forgetting (Feature A, synaptic tag-and-capture).
    # durability in [1.0, DUR_MAX] only ever SLOWS decay; 1.0 == legacy behavior.
    # Set DUR_MAX=1.0 to revert Feature A to a no-op exactly.
    DUR_MAX = float(os.getenv("BIO_DURABILITY_MAX", "4.0"))
    DUR_GROWTH = 0.6          # log1p growth → Karpicke diminishing returns
    DISUSE_DAYS = 30.0        # after this, never-helped entries lose prior protection
    DISUSE_FACTOR = 0.95

    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.collection_name = f"{agent_name}_l2"
        self.decay_lambda = 0.05
        self.embedder = Embedder()

        # Transient synaptic tags: {entry_id: [returned_count, helped_count]}.
        # Deliberately NOT persisted — a crash before the next sleep means
        # "reactivation without consolidation", which is biologically correct
        # and keeps the hot retrieval path free of SQLite writes.
        self._pending_access: Dict[str, List[int]] = {}

        url = os.getenv("QDRANT_URL", None)
        api_key = os.getenv("QDRANT_API_KEY", None)
        self._remote_index = bool(url)

        # Durable store: long-term memory must survive process restarts even
        # when the vector index is in-memory (no external Qdrant configured).
        self._durable = SQLiteStore(storage_dir=storage_dir)
        self._durable_table = f"{SQLiteStore.sanitize_identifier(agent_name)}_l2_entries"
        self._durable.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._durable_table} (
                entry_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                vector_json TEXT NOT NULL
            )
            """
        )

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

        if not self._remote_index:
            self._rehydrate()

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

    def _memory_decay_lambda(self, payload: Dict[str, Any]) -> float:
        memory_type = str(payload.get("memory_type", "semantic")).lower()
        if memory_type == "anchor":
            return self.decay_lambda * 0.08
        if memory_type == "procedural":
            return self.decay_lambda * 0.45
        if memory_type == "semantic":
            return self.decay_lambda * 0.70
        if memory_type == "exception":
            return self.decay_lambda * 0.35
        return self.decay_lambda

    def _effective_lambda(self, payload: Dict[str, Any]) -> float:
        # Retrieval-aware forgetting: durability divides the per-type lambda,
        # so a reinforced memory decays *slower*. durability is clamped to
        # [1.0, DUR_MAX] — it can only ever flatten the curve, never invert
        # recency ordering (a newer entry always keeps >= an older one's decay
        # within a type). durability=1.0 → identical to _memory_decay_lambda.
        base = self._memory_decay_lambda(payload)
        durability = max(1.0, min(self.DUR_MAX, float(payload.get("durability", 1.0))))
        return base / durability

    def _effective_decay(self, payload: Dict[str, Any], days_elapsed: float) -> float:
        return math.exp(-self._effective_lambda(payload) * max(days_elapsed, 0.0))

    def _is_anchor_query(self, query: str) -> bool:
        lowered = query.lower()
        markers = [
            "mật mã",
            "mat ma",
            "ký tự đặc biệt",
            "ky tu dac biet",
            "ký tự mật mã",
            "secret code",
            "code word",
            "passphrase",
            "password",
            "what did we choose",
            "what was the chosen",
            "what is the special character",
            "ký tự",
        ]
        return any(marker in lowered for marker in markers)

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
            # Retrieval-aware forgetting state (Feature A). durability=1.0 means
            # "never reinforced" → decay identical to legacy behavior.
            "access_count": 0,
            "last_accessed": None,
            "durability": 1.0,
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

        vector = self.embedder.embed(content)
        self._persist_entry(payload, vector)

        if self._fallback:
            payload = dict(payload)
            payload["vector"] = vector
            self._entries.append(payload)
            return

        point = PointStruct(
            id=payload["entry_id"],
            vector=vector,
            payload=payload,
        )
        self.client.upsert(collection_name=self.collection_name, points=[point])

    def _persist_entry(self, payload: Dict[str, Any], vector: List[float]) -> None:
        durable_payload = {key: value for key, value in payload.items() if key != "vector"}
        self._durable.execute(
            f"INSERT OR REPLACE INTO {self._durable_table} (entry_id, payload_json, vector_json) VALUES (?, ?, ?)",
            (
                str(payload["entry_id"]),
                self._durable.dumps_json(durable_payload),
                self._durable.dumps_json(vector),
            ),
        )

    def _rehydrate(self) -> None:
        rows = self._durable.fetchall(f"SELECT * FROM {self._durable_table}")
        if not rows:
            return
        points: List[Any] = []
        for row in rows:
            payload = self._durable.loads_json(row["payload_json"], None)
            vector = self._durable.loads_json(row["vector_json"], None)
            if not isinstance(payload, dict) or not isinstance(vector, list):
                continue
            # Legacy rows predate retrieval-aware forgetting; default to the
            # neutral durability so they decay exactly as before.
            payload.setdefault("access_count", 0)
            payload.setdefault("last_accessed", None)
            payload.setdefault("durability", 1.0)
            if len(vector) != self.embedder.dimensions:
                # Embedding backend changed since this entry was written;
                # re-embed from content so no memory is silently lost.
                vector = self.embedder.embed(str(payload.get("content", "")))
                self._persist_entry(payload, vector)
            if self._fallback:
                entry = dict(payload)
                entry["vector"] = vector
                self._entries.append(entry)
            else:
                points.append(
                    PointStruct(id=payload["entry_id"], vector=vector, payload=payload)
                )
        if points:
            self.client.upsert(collection_name=self.collection_name, points=points)

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
        if self._is_anchor_query(str(retrieval_state.get("query", ""))) and payload.get("memory_type") == "anchor":
            boost += 1.0

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
        allowed_workspaces = self._allowed_workspaces(retrieval_state)
        for payload in entries:
            # Hard tenancy boundary: entries outside the caller's workspace
            # scope are invisible, not merely down-ranked.
            if allowed_workspaces is not None and payload.get("workspace_id") not in allowed_workspaces:
                continue
            importance = float(payload["importance"])
            timestamp = float(payload["timestamp"])
            days_elapsed = (now - timestamp) / 86400
            decay = self._effective_decay(payload, days_elapsed)

            semantic_score = 0.6
            if "vector" in payload:
                semantic_score = max(0.0, self._cosine_similarity(query_vector, payload["vector"]))

            lexical_bonus = 0.0
            content_terms = set(str(payload["content"]).lower().split())
            tag_terms = {str(tag).lower() for tag in payload.get("tags", [])}
            overlap = len(query_terms & (content_terms | tag_terms))
            if query_terms:
                lexical_bonus = min(0.2, overlap / max(len(query_terms), 1) * 0.2)
            if self._is_anchor_query(query) and payload.get("memory_type") == "anchor":
                normalized_query = " ".join(sorted(query_terms))
                normalized_content = " ".join(sorted(content_terms))
                if overlap >= 2:
                    lexical_bonus += 0.6
                if normalized_query and normalized_query in normalized_content:
                    lexical_bonus += 0.4

            state_boost = self._state_boost(payload, retrieval_state)
            # Relevance gates, importance modulates: a raw importance
            # multiplier (1-9) would let an important-but-irrelevant memory
            # outrank a relevant one, so importance is squashed to a gentle
            # 0.84-1.16 factor instead.
            importance_factor = 0.8 + (importance / 25.0)
            final_score = (semantic_score + lexical_bonus) * importance_factor * decay * state_boost

            results.append(
                {
                    # entry_id is the attribution key for retrieval-aware
                    # forgetting — without it a caller cannot credit the
                    # specific stored row that helped.
                    "entry_id": payload.get("entry_id"),
                    "durability": float(payload.get("durability", 1.0)),
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

    @staticmethod
    def _allowed_workspaces(retrieval_state: Optional[Dict[str, Any]]) -> Optional[set]:
        if not retrieval_state:
            return None
        allowed = retrieval_state.get("allowed_workspaces")
        if allowed is not None:
            return {str(workspace) for workspace in allowed}
        # SECURITY (per-workspace isolation): when there is no explicit tenant
        # allow-list, fall back to the caller's OWN workspace_id as a hard
        # boundary — so one workspace's consolidated L2 memories never surface
        # for another. Previously, without a tenant API key, workspace_id was
        # ignored here and L2 search spanned ALL workspaces (cross-user leak).
        # Matches the strict workspace_id filter already used for episodes.
        workspace_id = retrieval_state.get("workspace_id")
        if workspace_id:
            return {str(workspace_id)}
        return None

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

        allowed_workspaces = self._allowed_workspaces(retrieval_state)
        query_filter = None
        if allowed_workspaces is not None:
            # Filter at the index so tenant results are not starved by
            # other tenants' entries occupying the candidate window.
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="workspace_id",
                        match=MatchAny(any=sorted(allowed_workspaces)),
                    )
                ]
            )

        points_data = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=query_filter,
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

    def all_entries(self, limit: int = 2000) -> List[Dict[str, Any]]:
        if self._fallback:
            return list(self._entries)[:limit]
        entries: List[Dict[str, Any]] = []
        offset = None
        while len(entries) < limit:
            batch_limit = min(256, limit - len(entries))
            points, offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=batch_limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            entries.extend(dict(point.payload) for point in points)
            if offset is None:
                break
        return entries[:limit]

    def confidence_dashboard(self, limit: int = 2000) -> Dict[str, Any]:
        entries = self.all_entries(limit=limit)
        if not entries:
            return {"total": 0, "by_type": []}
        now = time.time()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for entry in entries:
            grouped.setdefault(str(entry.get("memory_type", "semantic")), []).append(entry)
        by_type = []
        for memory_type, items in grouped.items():
            confidence_scores = []
            stale_count = 0
            for item in items:
                days_elapsed = max((now - float(item.get("timestamp", now))) / 86400.0, 0.0)
                decay = self._effective_decay(item, days_elapsed)
                confidence_score = min(1.0, (float(item.get("importance", 5.0)) / 10.0) * decay * 1.15)
                confidence_scores.append(confidence_score)
                if confidence_score < 0.4:
                    stale_count += 1
            by_type.append(
                {
                    "memory_type": memory_type,
                    "count": len(items),
                    "average_confidence": round(sum(confidence_scores) / max(len(confidence_scores), 1), 3),
                    "stale_count": stale_count,
                }
            )
        by_type.sort(key=lambda item: (-item["average_confidence"], -item["count"]))
        return {
            "total": len(entries),
            "by_type": by_type,
        }

    def _delete_persisted(self, entry_ids: List[str]) -> None:
        if not entry_ids:
            return
        placeholders = ", ".join("?" for _ in entry_ids)
        self._durable.execute(
            f"DELETE FROM {self._durable_table} WHERE entry_id IN ({placeholders})",
            tuple(str(entry_id) for entry_id in entry_ids),
        )

    # --- Retrieval-aware forgetting: synaptic tag-and-capture ---------------

    def note_access(
        self,
        entry_ids: List[Optional[str]],
        helped_ids: Optional[List[Optional[str]]] = None,
    ) -> None:
        """
        Hot-path 'tag' (Stage 1): record that entries were reactivated and,
        optionally, that some genuinely helped. Pure O(k) dict math — no
        SQLite write, no LLM. Tags are transient and only become durable at
        the next sleep (apply_access_consolidation). 'helped' is the
        success gate: mere return does not reinforce.
        """
        helped = {str(eid) for eid in (helped_ids or []) if eid}
        for eid in entry_ids:
            if not eid:
                continue
            self._pending_access.setdefault(str(eid), [0, 0])[0] += 1
        for eid in helped:
            self._pending_access.setdefault(eid, [0, 0])[1] += 1

    def _load_payload(self, entry_id: str):
        if self._fallback:
            for entry in self._entries:
                if str(entry.get("entry_id")) == str(entry_id):
                    return entry, entry.get("vector")
            return None, None
        records = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[entry_id],
            with_payload=True,
            with_vectors=True,
        )
        if not records:
            return None, None
        record = records[0]
        return dict(record.payload), list(record.vector) if record.vector else None

    def _save_payload(self, payload: Dict[str, Any], vector: Optional[List[float]]) -> None:
        self._persist_entry(payload, vector or [])
        if not self._fallback and vector:
            self.client.upsert(
                collection_name=self.collection_name,
                points=[PointStruct(id=payload["entry_id"], vector=vector, payload=payload)],
            )

    def durable_entries(self, workspace_id: Optional[str] = None):
        """
        (payload, vector) pairs straight from the durable store — the only
        place vectors are guaranteed available (the Qdrant scroll path uses
        with_vectors=False). Used by reconsolidation replay.
        """
        rows = self._durable.fetchall(f"SELECT * FROM {self._durable_table}")
        out = []
        for row in rows:
            payload = self._durable.loads_json(row["payload_json"], None)
            vector = self._durable.loads_json(row["vector_json"], None)
            if not isinstance(payload, dict):
                continue
            payload.setdefault("access_count", 0)
            payload.setdefault("last_accessed", None)
            payload.setdefault("durability", 1.0)
            if workspace_id is not None and payload.get("workspace_id") != workspace_id:
                continue
            out.append((payload, vector if isinstance(vector, list) else None))
        return out

    def update_entry(self, payload: Dict[str, Any], vector: Optional[List[float]]) -> None:
        """Re-persist a mutated payload to the durable store and the index."""
        self._save_payload(payload, vector)

    def purge_workspace(self, workspace_id: str) -> int:
        """Xoá mọi ký ức ngữ nghĩa của một workspace, cả bản bền và cả index.

        Quét bảng bền rồi gọi `forget` để index và bản bền không bị lệch nhau —
        một bên còn vector mà bên kia đã xoá thì truy hồi sẽ trả về ký ức của
        người đã yêu cầu xoá.
        """
        if not workspace_id:
            return 0
        rows = self._durable.fetchall(
            f"SELECT entry_id, payload_json FROM {self._durable_table}"
        )
        victims: List[str] = []
        for row in rows:
            payload = self._durable.loads_json(row["payload_json"], {}) or {}
            if str(payload.get("workspace_id") or "") == str(workspace_id):
                victims.append(row["entry_id"])
        if not victims:
            return 0
        return self.forget(victims)

    def forget(self, entry_ids: List[Optional[str]]) -> int:
        """Remove entries from both the index and the durable store."""
        ids = [str(eid) for eid in entry_ids if eid]
        if not ids:
            return 0
        if self._fallback:
            drop = set(ids)
            self._entries = [e for e in self._entries if str(e.get("entry_id")) not in drop]
        else:
            self.client.delete(collection_name=self.collection_name, points_selector=ids)
        self._delete_persisted(ids)
        return len(ids)

    def apply_access_consolidation(self) -> Dict[str, int]:
        """
        Offline 'capture' (Stage 2), run at sleep: drain transient tags into
        durable per-entry state. Durability grows with cumulative *helped*
        accesses (the testing effect), with log1p diminishing returns and a
        hard DUR_MAX cap. Returned-but-not-helped never reinforces.
        """
        if not self._pending_access:
            return {"reinforced": 0, "tagged": 0}
        pending = self._pending_access
        self._pending_access = {}
        now = time.time()
        reinforced = 0
        for entry_id, (_returned, helped) in pending.items():
            if helped <= 0:
                continue
            payload, vector = self._load_payload(entry_id)
            if payload is None:
                continue
            payload["access_count"] = int(payload.get("access_count", 0)) + int(helped)
            payload["last_accessed"] = now
            payload["durability"] = min(
                self.DUR_MAX,
                1.0 + self.DUR_GROWTH * math.log1p(payload["access_count"]),
            )
            self._save_payload(payload, vector)
            reinforced += 1
        return {"reinforced": reinforced, "tagged": len(pending)}

    def prune_decayed(self, threshold: float = 1.0) -> int:
        now = time.time()
        if self._fallback:
            kept: List[Dict[str, Any]] = []
            removed_ids: List[str] = []
            for entry in self._entries:
                # Use the per-type effective decay (durability-aware). This
                # also fixes a pre-existing bug where the fallback path used
                # the flat base lambda while the Qdrant path used per-type.
                days_elapsed = (now - entry["timestamp"]) / 86400
                decay_score = entry["importance"] * self._effective_decay(entry, days_elapsed)
                if decay_score >= threshold:
                    kept.append(entry)
                else:
                    removed_ids.append(str(entry.get("entry_id")))
            self._entries = kept
            self._delete_persisted(removed_ids)
            return len(removed_ids)

        points, _ = self.client.scroll(collection_name=self.collection_name, limit=1000)
        to_delete = []

        for point in points:
            days_elapsed = (now - point.payload["timestamp"]) / 86400
            decay_score = point.payload["importance"] * self._effective_decay(
                point.payload, days_elapsed
            )
            if decay_score < threshold:
                to_delete.append(point.id)

        if to_delete:
            self.client.delete(collection_name=self.collection_name, points_selector=to_delete)
            self._delete_persisted([str(point_id) for point_id in to_delete])

        return len(to_delete)

    @property
    def count(self) -> int:
        if self._fallback:
            return len(self._entries)
        return self.client.count(collection_name=self.collection_name).count
