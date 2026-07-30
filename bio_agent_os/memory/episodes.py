"""
Episode storage for Bio-Agent OS V2.
"""

import os
import re
import time
import unicodedata
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
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data", embedder=None):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        # Optional shared embedder enables dense hippocampal recall; without
        # it the store falls back to keyword-overlap search.
        self._embedder = embedder
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_episodes.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_episodes"
        self._ensure_table()
        self._migrate_legacy_json()
        self._backfill_norm_text()

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
                metadata_json TEXT NOT NULL,
                vector_json TEXT
            )
            """
        )
        try:
            # Migration for tables created before dense recall existed.
            self._store.execute(f"ALTER TABLE {self._table} ADD COLUMN vector_json TEXT")
        except Exception:
            pass
        try:
            # Lowercase/punctuation-stripped mirror of raw_payload. SQLite's
            # LOWER() is ASCII-only, so Vietnamese text can only be matched
            # reliably against a pre-normalized column computed in Python.
            self._store.execute(f"ALTER TABLE {self._table} ADD COLUMN norm_text TEXT")
        except Exception:
            pass
        try:
            self._store.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._table}_ws_ts "
                f"ON {self._table}(workspace_id, timestamp)"
            )
        except Exception:
            pass

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

    def _tokenize(self, text: str) -> set[str]:
        return {token for token in self._normalize_text(text).split() if token}

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Lowercased, punctuation-stripped mirror used for tokenizing and for
        SQL LIKE sweeps.

        Uses the Unicode word class rather than a fixed `a-z0-9\u00c0-\u024f` range:
        Vietnamese diacritics live in Latin Extended Additional (U+1E00\u2013U+1EFF),
        which that range silently DELETES \u2014 turning "h\u1ee3p \u0111\u1ed3ng" into "h p \u0111 ng"
        and shattering recall for exactly the language this product serves.
        NFC normalization keeps precomposed and decomposed input comparable.
        """
        folded = unicodedata.normalize("NFC", str(text)).lower()
        cleaned = re.sub(r"[^\w\s]", " ", folded, flags=re.UNICODE)
        return " ".join(cleaned.split())

    def _backfill_norm_text(self, batch: int = 2000):
        """Populate norm_text for rows written before the column existed, so a
        lexical sweep can reach the FULL history rather than only new rows."""
        try:
            rows = self._store.fetchall(
                f"SELECT episode_id, raw_payload FROM {self._table} "
                f"WHERE norm_text IS NULL LIMIT ?",
                [batch],
            )
        except Exception:
            return
        if not rows:
            return
        payload = [
            (self._normalize_text(row["raw_payload"]), row["episode_id"])
            for row in rows
        ]
        try:
            self._store.executemany(
                f"UPDATE {self._table} SET norm_text = ? WHERE episode_id = ?",
                payload,
            )
        except Exception:
            pass

    def _infer_anchor_kind(self, text: str) -> Optional[str]:
        lowered = str(text).lower()
        if any(marker in lowered for marker in ["ký tự đặc biệt", "ky tu dac biet", "special character"]):
            return "special_character"
        if "ký tự" in lowered and "mật mã" in lowered:
            return "special_character"
        if any(marker in lowered for marker in ["mật mã", "mat ma", "secret code", "code word", "passphrase", "password"]):
            return "verification_code"
        return None

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
        defer_vector: Optional[bool] = None,
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
        # Embedding is a network round trip to the embedding backend. Doing it
        # inline makes every write wait on it (~0.3-0.6s), which is the rest of
        # the write-path cost once LLM labeling is deferred. Store the episode
        # now and let `backfill_vectors()` fill vectors during sleep; until then
        # recall still works through the lexical/norm_text sweep.
        if defer_vector is None:
            defer_vector = os.getenv("BIO_AGENT_DEFER_VECTOR", "1").strip().lower() in (
                "1", "true", "yes", "on",
            )
        vector: Optional[List[float]] = None
        if self._embedder is not None and not defer_vector:
            try:
                vector = self._embedder.embed(str(raw_payload)[:1500])
            except Exception:
                vector = None
        self._store.execute(
            f"""
            INSERT OR REPLACE INTO {self._table} (
                episode_id, timestamp, task_id, workspace_id, project_version,
                actor, source, observation_type, raw_payload, inferred_intent,
                topic, outcome, confidence, tags_json, source_refs_json, metadata_json,
                vector_json, norm_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                self._store.dumps_json(vector) if vector else None,
                self._normalize_text(raw_payload),
            ],
        )
        return record

    def backfill_vectors(self, limit: int = 500) -> int:
        """Embed episodes stored by the deferred-vector write path.

        Runs during sleep consolidation, off the user's request path.
        """
        if self._embedder is None:
            return 0
        rows = self._store.fetchall(
            f"SELECT episode_id, raw_payload FROM {self._table} "
            f"WHERE vector_json IS NULL LIMIT ?",
            [limit],
        )
        filled = 0
        for row in rows:
            try:
                vector = self._embedder.embed(str(row["raw_payload"])[:1500])
            except Exception:
                continue
            if not vector:
                continue
            self._store.execute(
                f"UPDATE {self._table} SET vector_json = ? WHERE episode_id = ?",
                [self._store.dumps_json(vector), row["episode_id"]],
            )
            filled += 1
        return filled

    def update_labels(
        self,
        episode_id: str,
        topic: Optional[str] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Fill in labels for an episode written by the deferred-label path."""
        sets: List[str] = []
        parameters: List[Any] = []
        if topic is not None:
            sets.append("topic = ?")
            parameters.append(topic)
            sets.append("inferred_intent = ?")
            parameters.append(topic)
        if confidence is not None:
            sets.append("confidence = ?")
            parameters.append(float(confidence))
        if tags is not None:
            sets.append("tags_json = ?")
            parameters.append(self._store.dumps_json(tags))
        if metadata is not None:
            sets.append("metadata_json = ?")
            parameters.append(self._store.dumps_json(metadata))
        if not sets:
            return
        parameters.append(episode_id)
        self._store.execute(
            f"UPDATE {self._table} SET {', '.join(sets)} WHERE episode_id = ?",
            parameters,
        )

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

    @staticmethod
    def _cosine(left: List[float], right: List[float]) -> float:
        if len(left) != len(right):
            return 0.0
        return sum(a * b for a, b in zip(left, right))

    def _scope_conditions(
        self,
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
    ) -> tuple[List[str], List[Any]]:
        conditions: List[str] = []
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
        return conditions, parameters

    def _rows_to_candidates(self, rows) -> List[Dict[str, Any]]:
        candidates = []
        for row in rows:
            record = self._row_to_record(row)
            record["_vector"] = self._store.loads_json(row["vector_json"], None)
            candidates.append(record)
        return candidates

    def _search_candidates(
        self,
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Recency window — the freshest slice of this scope's history."""
        conditions, parameters = self._scope_conditions(task_id, workspace_id, project_version)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(limit)
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} {where} ORDER BY timestamp DESC LIMIT ?",
            parameters,
        )
        return self._rows_to_candidates(rows)

    def _lexical_candidates(
        self,
        query_terms: set[str],
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Sweep the ENTIRE history for episodes containing any significant query
        term.

        Without this, the candidate pool is a pure recency window, so once a
        workspace grows past that window its older memories become unreachable
        even though they are still stored — recall silently contradicting the
        immortal-ledger guarantee. Matching runs against `norm_text` because
        SQLite's LOWER() is ASCII-only and cannot fold Vietnamese.
        """
        terms = sorted({t for t in query_terms if len(t) >= 2}, key=len, reverse=True)[:8]
        if not terms:
            return []
        conditions, parameters = self._scope_conditions(task_id, workspace_id, project_version)
        like_clause = " OR ".join(["norm_text LIKE ?"] * len(terms))
        conditions.append(f"({like_clause})")
        parameters.extend(f"%{term}%" for term in terms)
        where = f"WHERE {' AND '.join(conditions)}"
        parameters.append(limit)
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} {where} ORDER BY timestamp DESC LIMIT ?",
            parameters,
        )
        return self._rows_to_candidates(rows)

    def _anchor_candidates(
        self,
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Pinned/anchor memories stay reachable no matter how old they are."""
        conditions, parameters = self._scope_conditions(task_id, workspace_id, project_version)
        conditions.append("metadata_json LIKE ?")
        parameters.append("%is_anchor_memory%")
        where = f"WHERE {' AND '.join(conditions)}"
        parameters.append(limit)
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} {where} ORDER BY timestamp DESC LIMIT ?",
            parameters,
        )
        return self._rows_to_candidates(rows)

    def _candidate_pool(
        self,
        query_terms: set[str],
        task_id: Optional[str],
        workspace_id: Optional[str],
        project_version: Optional[str],
        recent_limit: int,
        lexical_limit: int = 400,
        anchor_limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Recency window ∪ full-history lexical sweep ∪ anchors, deduped."""
        pool: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for group in (
            self._search_candidates(task_id, workspace_id, project_version, recent_limit),
            self._lexical_candidates(
                query_terms, task_id, workspace_id, project_version, lexical_limit
            ),
            self._anchor_candidates(task_id, workspace_id, project_version, anchor_limit),
        ):
            for record in group:
                episode_id = record.get("episode_id")
                if episode_id in seen:
                    continue
                seen.add(episode_id)
                pool.append(record)
        return pool

    def search_text(
        self,
        query: str,
        limit: int = 10,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not query.strip():
            return []
        query_terms = self._tokenize(query)
        # A recency-capped pool alone would make old episodes unfindable no
        # matter how relevant, so the pool also sweeps the FULL history for the
        # query's terms and always includes anchors.
        candidates = self._candidate_pool(
            query_terms=query_terms,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            recent_limit=max(limit * 12, 500),
        )
        normalized_query = " ".join(query_terms)
        desired_anchor_kind = self._infer_anchor_kind(query)
        query_vector: Optional[List[float]] = None
        if self._embedder is not None:
            try:
                query_vector = self._embedder.embed(query)
            except Exception:
                query_vector = None
        now = time.time()
        results: List[Dict[str, Any]] = []
        for record in candidates:
            text = str(record.get("raw_payload", "")).strip()
            vector = record.pop("_vector", None)
            if not text:
                continue
            metadata = record.get("metadata", {})
            anchor_kind = metadata.get("anchor_kind")
            anchor_value = metadata.get("anchor_value")
            text_terms = self._tokenize(text)
            overlap = len(query_terms & text_terms)
            is_matching_anchor = bool(
                metadata.get("is_anchor_memory")
                and desired_anchor_kind
                and anchor_kind == desired_anchor_kind
            )
            dense = 0.0
            if query_vector is not None and isinstance(vector, list):
                dense = max(0.0, self._cosine(query_vector, vector))
            # Dense similarity rescues paraphrased queries that share no
            # keywords with the episode text.
            if (
                query_terms
                and overlap == 0
                and normalized_query not in text.lower()
                and not is_matching_anchor
                and dense < 0.4
            ):
                continue
            recency_hours = max((now - float(record.get("timestamp", now))) / 3600.0, 0.0)
            recency_bonus = max(0.0, 1.5 - min(recency_hours / 24.0, 1.5))
            anchor_bonus = 0.8 if metadata.get("is_anchor_memory") else 0.0
            if anchor_value:
                anchor_bonus += 0.4
            if desired_anchor_kind and anchor_kind == desired_anchor_kind:
                anchor_bonus += 1.4
            elif desired_anchor_kind and anchor_kind and anchor_kind != desired_anchor_kind:
                anchor_bonus -= 0.6
            exact_bonus = 0.6 if normalized_query and normalized_query in " ".join(text_terms) else 0.0
            score = dense * 3.0 + overlap * 0.7 + recency_bonus + anchor_bonus + exact_bonus
            results.append({**record, "score": round(score, 3)})
        results.sort(key=lambda item: (-float(item["score"]), -float(item["timestamp"])))
        return results[:limit]

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
