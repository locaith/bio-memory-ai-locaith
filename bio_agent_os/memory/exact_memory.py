"""
Exact fact memory for high-precision anchor recall.

This layer stores short, verbatim-like facts that should not be reconstructed
through semantic guessing, such as special characters, code words, passphrases,
or compact operational identifiers.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


class ExactMemoryStore:
    SUBJECT_CUES = {
        "character",
        "symbol",
        "icon",
        "emoji",
        "code",
        "password",
        "passphrase",
        "token",
        "id",
        "identifier",
        "name",
        "alias",
        "model",
        "branch",
        "workspace",
        "project",
        "tenant",
        "rule",
        "procedure",
        "version",
        "key",
        "secret",
        "mật",
        "mã",
        "ký",
        "tự",
        "định",
        "danh",
        "tên",
        "quy",
        "tắc",
        "phiên",
        "bản",
    }

    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(agent_name)}_exact_memory"
        self._ensure_table()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                fact_id TEXT PRIMARY KEY,
                fact_kind TEXT NOT NULL,
                fact_value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                state TEXT NOT NULL,
                confidence REAL NOT NULL,
                authority_score REAL NOT NULL,
                reinforcement_count INTEGER NOT NULL,
                conflict_count INTEGER NOT NULL,
                first_seen_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                valid_from REAL NOT NULL,
                valid_to REAL,
                source TEXT,
                task_id TEXT,
                workspace_id TEXT,
                project_version TEXT,
                evidence_episode_ids_json TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        self._store.execute(
            f"CREATE INDEX IF NOT EXISTS {self._table}_kind_scope_idx "
            f"ON {self._table} (fact_kind, workspace_id, project_version, last_seen_at)"
        )

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKD", text)
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        normalized = re.sub(r"[^a-zA-Z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized

    def _normalize_value(self, value: Any) -> str:
        normalized = self._normalize_text(value)
        return normalized[:220]

    def _normalize_kind(self, value: Any) -> str:
        normalized = self._normalize_text(value).replace(" ", "_")
        return normalized or "unknown"

    def _subject_key(self, subject: str) -> str:
        key = self._normalize_text(subject)
        key = re.sub(r"\b(?:the|we|chosen|choose|selected|select|current|last|cuoi|cuối|la|là|is|was)\b", " ", key)
        key = re.sub(r"\s+", " ", key).strip()
        return key.replace(" ", "_") or "exact_fact"

    def _looks_like_exact_subject(self, subject: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        normalized = self._normalize_text(subject)
        tokens = set(normalized.split())
        if metadata and (metadata.get("retain_verbatim") or metadata.get("is_anchor_memory")):
            return True
        if len(tokens) == 0 or len(tokens) > 8:
            return False
        return bool(tokens & {self._normalize_text(token) for token in self.SUBJECT_CUES})

    def _is_plausible_value(self, fact_kind: str, fact_value: str) -> bool:
        value = " ".join(str(fact_value or "").split()).strip()
        if not value:
            return False
        normalized = self._normalize_text(value)
        tokens = normalized.split()
        if len(tokens) == 0:
            return False
        if len(value) > 96 or len(tokens) > 12:
            return False
        if any(marker in normalized for marker in [
            "avoid",
            "because",
            "therefore",
            "duy tri",
            "tinh dong nhat",
            "giu a cac kenh",
            "tranh tinh trang",
            "quy trinh",
            "safety guard",
            "guardrail",
        ]):
            return False
        if fact_kind in {"special_character", "verification_code"} and (len(tokens) > 6 or "," in value or ":" in value):
            return False
        return True

    def _dedupe_list(self, values: List[Any]) -> List[Any]:
        seen = set()
        result = []
        for value in values:
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _source_role(self, source: str, source_refs: List[Dict[str, Any]]) -> str:
        for ref in source_refs:
            role = self._normalize_text(ref.get("role", ""))
            if role in {"user", "assistant", "system"}:
                return role
        normalized_source = self._normalize_text(source)
        if "user" in normalized_source:
            return "user"
        if "assistant" in normalized_source or "jarvis" in normalized_source or "bio" in normalized_source:
            return "assistant"
        return "system"

    def _authority_score(
        self,
        source: str,
        source_refs: List[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> float:
        role = self._source_role(source, source_refs)
        score = 0.65
        if role == "user":
            score = 1.0
        elif role == "assistant":
            score = 0.82
        if metadata.get("retain_verbatim"):
            score += 0.08
        if metadata.get("is_anchor_memory"):
            score += 0.06
        if metadata.get("anchor_value"):
            score += 0.05
        return min(score, 1.15)

    def extract_candidate(self, raw_payload: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        if metadata and metadata.get("anchor_kind") and metadata.get("anchor_value"):
            fact_kind = self._normalize_kind(metadata.get("anchor_kind"))
            fact_value = " ".join(str(metadata.get("anchor_value", "")).split()).strip()
            if self._is_plausible_value(fact_kind, fact_value):
                return {"fact_kind": fact_kind, "fact_value": fact_value}
            return {}
        text = " ".join(str(raw_payload or "").split())
        original_patterns = [
            (
                "special_character",
                r"(?:ký tự đặc biệt|ky tu dac biet|special character)[^:]{0,80}?(?:là|la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "verification_code",
                r"(?:ký tự mật mã|ky tu mat ma|mật mã|mat ma|secret code|code word|passphrase|password)[^:]{0,80}?(?:là|la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "special_character",
                r"(?:remember exactly|ghi nhớ chính xác|ghi nho chinh xac)\s*[:\-]\s*(.+?)(?:[.!?\n]|$)",
            ),
        ]
        for kind, pattern in original_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = re.sub(r"[*`_]+", "", match.group(1)).strip(" .,:;!?-")
            value = re.sub(r"\s+", " ", value)
            if value and self._is_plausible_value(kind, value):
                return {"fact_kind": kind, "fact_value": value}

        generic_patterns = [
            r"(?:the\s+)?(?P<subject>[A-Za-zÀ-ỹ0-9 _/\-]{2,60}?)\s+(?:is|was|là|la|=)\s+(?P<value>[^.!?\n]{1,120})",
            r"(?:remember|ghi nhớ)\s+(?:that\s+)?(?P<subject>[A-Za-zÀ-ỹ0-9 _/\-]{2,60}?)\s+(?:is|was|là|la|=)\s+(?P<value>[^.!?\n]{1,120})",
        ]
        for pattern in generic_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            subject = re.sub(r"\s+", " ", match.group("subject")).strip(" .,:;!?-")
            value = re.sub(r"[*`_]+", "", match.group("value")).strip(" .,:;!?-")
            value = re.sub(r"\s+", " ", value)
            if not subject or not value or len(value.split()) > 12:
                continue
            if not self._looks_like_exact_subject(subject, metadata):
                continue
            fact_kind = self._subject_key(subject)
            if self._is_plausible_value(fact_kind, value):
                return {"fact_kind": fact_kind, "fact_value": value}

        normalized = self._normalize_text(text)
        patterns = [
            (
                "special_character",
                r"(?:ky tu dac biet|special character)[^:]{0,80}?(?:la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "verification_code",
                r"(?:ky tu mat ma|mat ma|secret code|code word|passphrase|password)[^:]{0,80}?(?:la|is)\s+(.+?)(?:[.!?\n]|$)",
            ),
            (
                "special_character",
                r"(?:remember exactly|ghi nho chinh xac)\s*[:\-]\s*(.+?)(?:[.!?\n]|$)",
            ),
        ]
        for kind, pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            normalized_value = match.group(1).strip(" .,:;!?-")
            if not normalized_value:
                continue
            if self._is_plausible_value(kind, normalized_value):
                return {"fact_kind": kind, "fact_value": normalized_value}
        return {}

    def _scope_where(
        self,
        workspace_id: Optional[str],
        project_version: Optional[str],
    ) -> tuple[str, List[Any]]:
        return (
            "COALESCE(workspace_id, '') = COALESCE(?, '') AND COALESCE(project_version, '') = COALESCE(?, '')",
            [workspace_id, project_version],
        )

    def _fetch_kind_candidates(
        self,
        fact_kind: str,
        workspace_id: Optional[str],
        project_version: Optional[str],
    ) -> List[Dict[str, Any]]:
        scope_sql, parameters = self._scope_where(workspace_id, project_version)
        rows = self._store.fetchall(
            f"""
            SELECT * FROM {self._table}
            WHERE fact_kind = ? AND {scope_sql}
            ORDER BY last_seen_at DESC
            """,
            [fact_kind, *parameters],
        )
        return [self._row_to_record(row) for row in rows]

    def _persist(self, record: Dict[str, Any]):
        self._store.execute(
            f"""
            INSERT OR REPLACE INTO {self._table} (
                fact_id, fact_kind, fact_value, normalized_value, state,
                confidence, authority_score, reinforcement_count, conflict_count,
                first_seen_at, last_seen_at, valid_from, valid_to, source,
                task_id, workspace_id, project_version,
                evidence_episode_ids_json, source_refs_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["fact_id"],
                record["fact_kind"],
                record["fact_value"],
                record["normalized_value"],
                record["state"],
                record["confidence"],
                record["authority_score"],
                record["reinforcement_count"],
                record["conflict_count"],
                record["first_seen_at"],
                record["last_seen_at"],
                record["valid_from"],
                record.get("valid_to"),
                record.get("source"),
                record.get("task_id"),
                record.get("workspace_id"),
                record.get("project_version"),
                self._store.dumps_json(record.get("evidence_episode_ids", [])),
                self._store.dumps_json(record.get("source_refs", [])),
                self._store.dumps_json(record.get("metadata", {})),
            ],
        )

    def _strength(self, record: Dict[str, Any], now: Optional[float] = None) -> float:
        current_time = now or time.time()
        age_days = max((current_time - float(record.get("last_seen_at", current_time))) / 86400.0, 0.0)
        recency = math.exp(-0.06 * age_days)
        reinforcement = math.log2(int(record.get("reinforcement_count", 1)) + 1.0)
        conflict_penalty = float(record.get("conflict_count", 0)) * 0.18
        state_penalty = 0.0
        if record.get("state") == "conflicting":
            state_penalty += 0.35
        if record.get("state") == "superseded":
            state_penalty += 1.2
        return (
            float(record.get("authority_score", 0.65)) * 1.1
            + float(record.get("confidence", 0.5))
            + reinforcement * 0.22
            + recency * 0.35
            - conflict_penalty
            - state_penalty
        )

    def remember(
        self,
        fact_kind: str,
        fact_value: str,
        confidence: float = 0.85,
        source: str = "unknown",
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        episode_id: Optional[str] = None,
        source_refs: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        kind = self._normalize_kind(fact_kind)
        value = " ".join(str(fact_value or "").split()).strip()
        normalized_value = self._normalize_value(value)
        if not kind or not normalized_value or not self._is_plausible_value(kind, value):
            return {}

        meta = dict(metadata or {})
        refs = [ref for ref in (source_refs or []) if isinstance(ref, dict)]
        now = time.time()
        authority = self._authority_score(source, refs, meta)
        existing_records = self._fetch_kind_candidates(kind, workspace_id, project_version)
        same_value = next((record for record in existing_records if record["normalized_value"] == normalized_value), None)

        if same_value:
            evidence_ids = list(same_value.get("evidence_episode_ids", []))
            already_indexed = episode_id and episode_id in evidence_ids
            if episode_id and not already_indexed:
                evidence_ids.append(episode_id)
            same_value["evidence_episode_ids"] = self._dedupe_list(evidence_ids)
            if not already_indexed:
                same_value["reinforcement_count"] = int(same_value.get("reinforcement_count", 1)) + 1
            same_value["last_seen_at"] = now
            same_value["authority_score"] = max(float(same_value.get("authority_score", 0.65)), authority)
            same_value["confidence"] = min(
                0.99,
                max(float(same_value.get("confidence", 0.5)), confidence) + (0.04 if not already_indexed else 0.0),
            )
            same_value["source_refs"] = self._dedupe_list(list(same_value.get("source_refs", [])) + refs)
            same_value["metadata"] = {**same_value.get("metadata", {}), **meta}
            if same_value.get("state") == "superseded":
                same_value["state"] = "active"
                same_value["valid_to"] = None
            self._persist(same_value)
            if already_indexed:
                return same_value
            current = same_value
        else:
            current = {
                "fact_id": str(uuid.uuid4()),
                "fact_kind": kind,
                "fact_value": value,
                "normalized_value": normalized_value,
                "state": "active",
                "confidence": min(max(float(confidence), 0.35), 0.99),
                "authority_score": authority,
                "reinforcement_count": 1,
                "conflict_count": 0,
                "first_seen_at": now,
                "last_seen_at": now,
                "valid_from": now,
                "valid_to": None,
                "source": source,
                "task_id": task_id,
                "workspace_id": workspace_id,
                "project_version": project_version,
                "evidence_episode_ids": [episode_id] if episode_id else [],
                "source_refs": refs,
                "metadata": meta,
            }
            self._persist(current)

        current_strength = self._strength(current, now=now)
        for other in existing_records:
            if other["fact_id"] == current["fact_id"]:
                continue
            if other["normalized_value"] == current["normalized_value"]:
                continue
            other["conflict_count"] = int(other.get("conflict_count", 0)) + 1
            current["conflict_count"] = int(current.get("conflict_count", 0)) + 1
            current_role = self._source_role(str(current.get("source", "")), list(current.get("source_refs", [])))
            other_role = self._source_role(str(other.get("source", "")), list(other.get("source_refs", [])))
            if current_role == "assistant" and other_role == "user":
                current["state"] = "conflicting"
                self._persist(other)
                continue
            if current_role == "user" and other_role == "assistant":
                other["state"] = "superseded"
                other["valid_to"] = now
                self._persist(other)
                continue
            other_strength = self._strength(other, now=now)
            if current_strength >= other_strength + 0.35:
                other["state"] = "superseded"
                other["valid_to"] = now
            elif other_strength >= current_strength + 0.35:
                current["state"] = "conflicting"
            else:
                other["state"] = "conflicting"
                current["state"] = "conflicting"
            self._persist(other)

        self._persist(current)
        return current

    def infer_query_kind(self, query: str) -> Optional[str]:
        normalized = self._normalize_text(query)
        if any(marker in normalized for marker in ["special character", "ky tu dac biet"]):
            return "special_character"
        if "ky tu" in normalized and "mat ma" in normalized:
            return "special_character"
        if any(marker in normalized for marker in ["mat ma", "secret code", "code word", "passphrase", "password"]):
            return "verification_code"
        generic_patterns = [
            r"what(?:\s+was|\s+is)?\s+(?:the\s+)?(?P<subject>[^?]+)",
            r"(?P<subject>.+?)\s+(?:la gi|là gì)\b",
        ]
        for pattern in generic_patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            subject = re.sub(r"\b(?:we chose|we selected|current|last|cuoi cung|cuối cùng)\b", " ", match.group("subject"))
            subject = re.sub(r"\s+", " ", subject).strip()
            if self._looks_like_exact_subject(subject):
                return self._subject_key(subject)
        return None

    def recall(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        task_id: Optional[str] = None,
        limit: int = 3,
    ) -> Dict[str, Any]:
        fact_kind = self.infer_query_kind(query)
        if not fact_kind:
            return {"kind": None, "status": "none", "facts": [], "answer_candidate": None}

        candidates = self._fetch_kind_candidates(fact_kind, workspace_id, project_version)
        now = time.time()
        scored: List[Dict[str, Any]] = []
        for record in candidates:
            score = self._strength(record, now=now)
            if task_id and record.get("task_id") == task_id:
                score += 0.08
            if workspace_id and record.get("workspace_id") == workspace_id:
                score += 0.12
            if project_version and record.get("project_version") == project_version:
                score += 0.08
            item = {**record, "score": round(score, 3)}
            scored.append(item)
        scored.sort(key=lambda item: (-float(item["score"]), -float(item["last_seen_at"])))
        top = scored[: max(limit, 2)]
        if not top:
            return {"kind": fact_kind, "status": "none", "facts": [], "answer_candidate": None}

        answer_candidate = top[0]["fact_value"]
        status = "resolved"
        if len(top) >= 2 and top[0]["normalized_value"] != top[1]["normalized_value"]:
            if abs(float(top[0]["score"]) - float(top[1]["score"])) < 0.35:
                status = "conflicting"
                answer_candidate = None
        if top[0].get("state") == "conflicting" and len(top) > 1:
            status = "conflicting"
            answer_candidate = None

        return {
            "kind": fact_kind,
            "status": status,
            "answer_candidate": answer_candidate,
            "facts": top[:limit],
        }

    def backfill_from_episodes(self, episodes, limit: int = 800) -> int:
        imported = 0
        for episode in episodes.query(limit=limit):
            metadata = episode.get("metadata", {}) or {}
            fact_kind = metadata.get("anchor_kind")
            fact_value = metadata.get("anchor_value")
            if not fact_kind or not fact_value:
                extracted = self.extract_candidate(episode.get("raw_payload", ""), metadata=metadata)
                fact_kind = extracted.get("fact_kind")
                fact_value = extracted.get("fact_value")
            if not fact_kind or not fact_value:
                continue
            record = self.remember(
                fact_kind=fact_kind,
                fact_value=fact_value,
                confidence=max(0.45, float(episode.get("confidence", 0.7))),
                source=str(episode.get("source", "unknown")),
                task_id=episode.get("task_id"),
                workspace_id=episode.get("workspace_id"),
                project_version=episode.get("project_version"),
                episode_id=episode.get("episode_id"),
                source_refs=episode.get("source_refs", []),
                metadata=metadata,
            )
            if record:
                imported += 1
        return imported

    def clear(self):
        self._store.execute(f"DELETE FROM {self._table}")

    def reindex_from_episodes(self, episodes, limit: int = 800) -> int:
        self.clear()
        return self.backfill_from_episodes(episodes, limit=limit)

    @property
    def count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} ORDER BY last_seen_at DESC LIMIT ?",
            [limit],
        )
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row) -> Dict[str, Any]:
        return {
            "fact_id": row["fact_id"],
            "fact_kind": row["fact_kind"],
            "fact_value": row["fact_value"],
            "normalized_value": row["normalized_value"],
            "state": row["state"],
            "confidence": row["confidence"],
            "authority_score": row["authority_score"],
            "reinforcement_count": row["reinforcement_count"],
            "conflict_count": row["conflict_count"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "source": row["source"],
            "task_id": row["task_id"],
            "workspace_id": row["workspace_id"],
            "project_version": row["project_version"],
            "evidence_episode_ids": self._store.loads_json(row["evidence_episode_ids_json"], []),
            "source_refs": self._store.loads_json(row["source_refs_json"], []),
            "metadata": self._store.loads_json(row["metadata_json"], {}),
        }
