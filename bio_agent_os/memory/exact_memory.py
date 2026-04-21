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
    ANCHOR_BASE_KINDS = {"special_character", "verification_code"}
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

    def _base_fact_kind(self, fact_kind: str) -> str:
        normalized = self._normalize_kind(fact_kind)
        for base_kind in self.ANCHOR_BASE_KINDS:
            if normalized == base_kind or normalized.startswith(f"{base_kind}_for_"):
                return base_kind
        return normalized

    def _subject_key(self, subject: str) -> str:
        key = self._normalize_text(subject)
        key = re.sub(r"\b(?:the|we|chosen|choose|selected|select|current|last|cuoi|cuối|la|là|is|was)\b", " ", key)
        key = re.sub(r"\s+", " ", key).strip()
        return key.replace(" ", "_") or "exact_fact"

    def _qualify_fact_kind(self, fact_kind: str, subject: str = "") -> str:
        normalized_kind = self._normalize_kind(fact_kind)
        base_kind = self._base_fact_kind(normalized_kind)
        if base_kind not in self.ANCHOR_BASE_KINDS:
            return normalized_kind
        subject_key = self._subject_key(subject)
        if not subject_key or subject_key == "exact_fact":
            return base_kind
        return f"{base_kind}_for_{subject_key}"

    def _extract_anchor_subject(self, text: str, fact_kind: str = "") -> str:
        normalized_text = " ".join(str(text or "").split())
        if not normalized_text:
            return ""
        base_kind = self._base_fact_kind(fact_kind or "unknown")
        subject_patterns: List[str] = []
        if base_kind == "special_character":
            subject_patterns.extend(
                [
                    r"(?:special character|ky tu dac biet|ký tự đặc biệt)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)(?:\s+(?:is|was|la|là|=)\s+|\??$|$)",
                    r"(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:special character|ky tu dac biet|ký tự đặc biệt)\s+(?:is|was|la|là|=)\s+",
                ]
            )
        if base_kind == "verification_code":
            subject_patterns.extend(
                [
                    r"(?:secret code|code word|passphrase|password|verification code|mat ma|mật mã)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)(?:\s+(?:is|was|la|là|=)\s+|\??$|$)",
                    r"(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:secret code|code word|passphrase|password|verification code|mat ma|mật mã)\s+(?:is|was|la|là|=)\s+",
                ]
            )
        subject_patterns.append(r"(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)(?:\s+(?:is|was|la|là|=)\s+|\??$|$)")
        for pattern in subject_patterns:
            match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
            if not match:
                continue
            subject = re.sub(r"\s+", " ", match.group("subject")).strip(" .,:;!?-")
            if subject:
                return subject
        return ""

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
        if self._base_fact_kind(fact_kind) in {"special_character", "verification_code"} and (len(tokens) > 6 or "," in value or ":" in value):
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

    def _channels(self, source_refs: List[Dict[str, Any]]) -> List[str]:
        channels = []
        for ref in source_refs:
            channel = self._normalize_text(ref.get("channel", ""))
            if channel and channel not in channels:
                channels.append(channel)
        return channels or ["default"]

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
            anchor_subject = str(metadata.get("anchor_subject") or "").strip()
            if not anchor_subject:
                anchor_subject = self._extract_anchor_subject(raw_payload, fact_kind)
            fact_kind = self._qualify_fact_kind(fact_kind, anchor_subject)
            fact_value = " ".join(str(metadata.get("anchor_value", "")).split()).strip()
            if self._is_plausible_value(fact_kind, fact_value):
                return {"fact_kind": fact_kind, "fact_value": fact_value}
            return {}
        text = " ".join(str(raw_payload or "").split())
        subject_anchor_patterns = [
            (
                "special_character",
                r"(?:special character|ky tu dac biet|ký tự đặc biệt)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:is|was|la|là|=)\s+(?P<value>[^.!?\n]{1,120})",
            ),
            (
                "verification_code",
                r"(?:secret code|code word|passphrase|password|verification code|mat ma|mật mã)\s+(?:for|of)\s+(?P<subject>[^.!?\n:=]{2,90}?)\s+(?:is|was|la|là|=)\s+(?P<value>[^.!?\n]{1,120})",
            ),
        ]
        for kind, pattern in subject_anchor_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            subject = re.sub(r"\s+", " ", match.group("subject")).strip(" .,:;!?-")
            value = re.sub(r"[*`_]+", "", match.group("value")).strip(" .,:;!?-")
            value = re.sub(r"\s+", " ", value)
            fact_kind = self._qualify_fact_kind(kind, subject)
            if subject and value and self._is_plausible_value(fact_kind, value):
                return {"fact_kind": fact_kind, "fact_value": value}
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

    def _witness_preference(self, record: Dict[str, Any]) -> float:
        role = self._source_role(str(record.get("source", "")), list(record.get("source_refs", [])))
        if role == "user":
            return 0.22
        if role == "assistant":
            return 0.04
        return 0.0

    def _should_resolve_by_authority(self, top: Dict[str, Any], second: Dict[str, Any]) -> bool:
        top_role = self._source_role(str(top.get("source", "")), list(top.get("source_refs", [])))
        second_role = self._source_role(str(second.get("source", "")), list(second.get("source_refs", [])))
        top_authority = float(top.get("authority_score", 0.0))
        second_authority = float(second.get("authority_score", 0.0))
        top_score = float(top.get("score", 0.0))
        second_score = float(second.get("score", 0.0))
        if top_role == "user" and second_role == "assistant" and (top_score - second_score) >= 0.08:
            return True
        if (top_authority - second_authority) >= 0.18 and (top_score - second_score) >= 0.08:
            return True
        return False

    def _all_records(self, limit: int = 2000) -> List[Dict[str, Any]]:
        rows = self._store.fetchall(
            f"SELECT * FROM {self._table} ORDER BY last_seen_at DESC LIMIT ?",
            [limit],
        )
        return [self._row_to_record(row) for row in rows]

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
        meta = dict(metadata or {})
        anchor_subject = str(meta.get("anchor_subject") or "").strip()
        kind = self._qualify_fact_kind(fact_kind, anchor_subject)
        value = " ".join(str(fact_value or "").split()).strip()
        normalized_value = self._normalize_value(value)
        if not kind or not normalized_value or not self._is_plausible_value(kind, value):
            return {}

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
            subject = self._extract_anchor_subject(query, "special_character")
            return self._qualify_fact_kind("special_character", subject)
        if "ky tu" in normalized and "mat ma" in normalized:
            subject = self._extract_anchor_subject(query, "special_character")
            return self._qualify_fact_kind("special_character", subject)
        if any(marker in normalized for marker in ["mat ma", "secret code", "code word", "passphrase", "password"]):
            subject = self._extract_anchor_subject(query, "verification_code")
            return self._qualify_fact_kind("verification_code", subject)
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
            score += self._witness_preference(record)
            item = {**record, "score": round(score, 3)}
            scored.append(item)
        scored.sort(key=lambda item: (-float(item["score"]), -float(item["last_seen_at"])))
        top = scored[: max(limit, 2)]
        if not top:
            return {"kind": fact_kind, "status": "none", "facts": [], "answer_candidate": None}

        answer_candidate = top[0]["fact_value"]
        status = "resolved"
        if len(top) >= 2 and top[0]["normalized_value"] != top[1]["normalized_value"]:
            if abs(float(top[0]["score"]) - float(top[1]["score"])) < 0.35 and not self._should_resolve_by_authority(top[0], top[1]):
                status = "conflicting"
                answer_candidate = None
        if top[0].get("state") == "conflicting" and len(top) > 1:
            if self._should_resolve_by_authority(top[0], top[1]):
                status = "resolved"
            else:
                status = "conflicting"
                answer_candidate = None

        return {
            "kind": fact_kind,
            "status": status,
            "answer_candidate": answer_candidate,
            "facts": top[:limit],
        }

    def confidence_dashboard(self, limit: int = 2000) -> Dict[str, Any]:
        records = self._all_records(limit=limit)
        if not records:
            return {"total": 0, "average_confidence": 0.0, "active": 0, "conflicting": 0, "by_kind": []}
        active = [record for record in records if record.get("state") == "active"]
        conflicting = [record for record in records if record.get("state") == "conflicting"]
        weighted_confidence = []
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            weighted = min(
                1.0,
                float(record.get("confidence", 0.5)) * 0.7
                + float(record.get("authority_score", 0.65)) * 0.25
                + min(int(record.get("reinforcement_count", 1)), 6) * 0.03,
            )
            weighted_confidence.append(weighted)
            grouped.setdefault(str(record.get("fact_kind", "unknown")), []).append({**record, "weighted_confidence": weighted})
        by_kind = []
        for kind, items in grouped.items():
            by_kind.append(
                {
                    "kind": kind,
                    "count": len(items),
                    "active": sum(1 for item in items if item.get("state") == "active"),
                    "conflicting": sum(1 for item in items if item.get("state") == "conflicting"),
                    "average_confidence": round(
                        sum(float(item["weighted_confidence"]) for item in items) / max(len(items), 1),
                        3,
                    ),
                }
            )
        by_kind.sort(key=lambda item: (-item["conflicting"], -item["average_confidence"], -item["count"]))
        return {
            "total": len(records),
            "active": len(active),
            "conflicting": len(conflicting),
            "average_confidence": round(sum(weighted_confidence) / max(len(weighted_confidence), 1), 3),
            "by_kind": by_kind,
        }

    def conflict_clusters(self, limit: int = 20) -> List[Dict[str, Any]]:
        records = self._all_records(limit=5000)
        grouped: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        for record in records:
            key = (
                str(record.get("fact_kind", "unknown")),
                str(record.get("workspace_id") or ""),
                str(record.get("project_version") or ""),
            )
            grouped.setdefault(key, []).append(record)
        clusters: List[Dict[str, Any]] = []
        now = time.time()
        for (fact_kind, workspace_id, project_version), items in grouped.items():
            distinct_values = {}
            for item in items:
                distinct_values[item.get("normalized_value")] = item
            if len(distinct_values) < 2:
                continue
            scored = []
            for item in items:
                score = round(self._strength(item, now=now) + self._witness_preference(item), 3)
                scored.append({**item, "score": score, "channels": self._channels(item.get("source_refs", []))})
            scored.sort(key=lambda item: (-float(item["score"]), -float(item["last_seen_at"])))
            leader = scored[0]
            status = "conflicting"
            if len(scored) >= 2 and self._should_resolve_by_authority(scored[0], scored[1]):
                status = "needs_confirmation"
            clusters.append(
                {
                    "fact_kind": fact_kind,
                    "workspace_id": workspace_id or None,
                    "project_version": project_version or None,
                    "status": status,
                    "leader": {
                        "fact_value": leader.get("fact_value"),
                        "score": leader.get("score"),
                        "authority_score": leader.get("authority_score"),
                    },
                    "facts": scored[:4],
                }
            )
        clusters.sort(
            key=lambda item: (
                0 if item["status"] == "conflicting" else 1,
                -max(float(fact.get("score", 0.0)) for fact in item.get("facts", []) or [0.0]),
            )
        )
        return clusters[:limit]

    def build_revalidation_packet(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        recall = self.recall(
            query=query,
            workspace_id=workspace_id,
            project_version=project_version,
            task_id=task_id,
            limit=3,
        )
        if recall.get("status") != "conflicting":
            return {"status": "none", "question": None, "candidates": [], "kind": recall.get("kind")}
        candidates = [
            {
                "fact_value": fact.get("fact_value"),
                "channels": self._channels(fact.get("source_refs", [])),
                "authority_score": fact.get("authority_score"),
                "score": fact.get("score"),
            }
            for fact in recall.get("facts", [])
        ]
        kind = str(recall.get("kind") or "exact_fact")
        human_kind = kind.replace("_", " ")
        values = "; ".join(item["fact_value"] for item in candidates[:3])
        question = (
            f"I have conflicting exact memories for {human_kind}: {values}. "
            f"Please confirm which value should remain canonical."
        )
        return {
            "status": "conflicting",
            "kind": kind,
            "question": question,
            "candidates": candidates,
        }

    def resolve_conflict(
        self,
        fact_kind: str,
        fact_value: str,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        reviewer: str = "human",
    ) -> Dict[str, Any]:
        kind = self._normalize_kind(fact_kind)
        normalized_value = self._normalize_value(fact_value)
        candidates = self._fetch_kind_candidates(kind, workspace_id, project_version)
        chosen = None
        now = time.time()
        for record in candidates:
            if record.get("normalized_value") == normalized_value:
                chosen = record
                record["state"] = "active"
                record["valid_to"] = None
                metadata = dict(record.get("metadata", {}))
                metadata["resolved_by"] = reviewer
                metadata["resolved_at"] = now
                record["metadata"] = metadata
            else:
                record["state"] = "superseded"
                record["valid_to"] = now
            self._persist(record)
        return {"resolved": bool(chosen), "fact_kind": kind, "fact_value": fact_value}

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
