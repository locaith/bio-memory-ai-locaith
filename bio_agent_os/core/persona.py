"""
Self-model and persona memory for Bio-Agent OS V2.
"""

import base64
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from bio_agent_os.core.sqlite_store import SQLiteStore


RULE_STATES = {
    "proposed",
    "reinforced",
    "stable",
    "challenged",
    "deprecated",
    "archived",
}

PERSONA_LAYERS = {
    "core",
    "project",
    "adaptive",
}


class Persona:
    """
    Scope-aware self-model.

    Rules are not plain strings anymore. They carry lifecycle, confidence,
    scope, and provenance to support reconsolidation.
    """

    def __init__(self, name: str = "Bio-AI", storage_dir: str = "data"):
        self.name = name
        self.storage_dir = storage_dir
        self._rules: Dict[str, Dict[str, Any]] = {}
        self._legacy_filepath = os.path.join(storage_dir, f"{name}_core_identity.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        self._table = f"{self._store.sanitize_identifier(name)}_persona_rules"
        self._crypto_key = os.getenv(
            "BIO_AGENT_SECRET_KEY",
            "default-insecure-key-change-in-prod-erp",
        )
        self._fernet = self._setup_cipher()
        self._ensure_table()
        self._migrate_legacy_json()
        self.load()

    def _ensure_table(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                rule_id TEXT PRIMARY KEY,
                encrypted_text TEXT NOT NULL,
                layer TEXT NOT NULL,
                scope TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                support_count INTEGER NOT NULL,
                contradiction_count INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_validated_at REAL NOT NULL,
                valid_from REAL NOT NULL,
                valid_to REAL,
                evidence_episode_ids_json TEXT NOT NULL,
                superseded_by TEXT,
                expires_at REAL,
                challenge_reasons_json TEXT NOT NULL
            )
            """
        )

    def _setup_cipher(self) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"locaith-bio-memory",
            iterations=480000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self._crypto_key.encode("utf-8")))
        return Fernet(key)

    def _encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("utf-8")

    def _decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except Exception:
            return token

    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _find_rule_id(self, rule_text: str, scope: str) -> Optional[str]:
        normalized = self._normalize(rule_text)
        for rule_id, rule in self._rules.items():
            if self._normalize(rule["text"]) == normalized and rule["scope"] == scope:
                return rule_id
        return None

    def _infer_layer(self, scope: str, confidence: float, source: str) -> str:
        normalized_scope = (scope or "project").strip().lower()
        if normalized_scope == "core":
            return "core"
        if normalized_scope in {"project", "organization"}:
            return "project"
        if source == "human-approved" and confidence >= 0.9:
            return "core"
        return "adaptive"

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT rule_id FROM {self._table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        is_encrypted = data.get("encrypted", False)
        raw_rules = data.get("rules", {})
        rows = []
        now = time.time()
        for rule_id, raw_rule in raw_rules.items():
            if isinstance(raw_rule, str):
                text = self._decrypt(raw_rule) if is_encrypted else raw_rule
                rule = {
                    "id": rule_id,
                    "text": text,
                    "layer": "project",
                    "scope": "project",
                    "source": "legacy",
                    "confidence": 0.7,
                    "support_count": 1,
                    "contradiction_count": 0,
                    "state": "stable",
                    "created_at": now,
                    "updated_at": now,
                    "last_validated_at": now,
                    "valid_from": now,
                    "valid_to": None,
                    "evidence_episode_ids": [],
                    "superseded_by": None,
                    "expires_at": None,
                    "challenge_reasons": [],
                }
            else:
                rule = dict(raw_rule)
                text_value = rule.get("text", "")
                if is_encrypted:
                    text_value = self._decrypt(text_value)
                confidence = float(rule.get("confidence", 0.7))
                rule = {
                    "id": rule.get("id", rule_id),
                    "text": text_value,
                    "layer": rule.get(
                        "layer",
                        self._infer_layer(
                            rule.get("scope", "project"),
                            confidence,
                            rule.get("source", "legacy"),
                        ),
                    ),
                    "scope": rule.get("scope", "project"),
                    "source": rule.get("source", "legacy"),
                    "confidence": confidence,
                    "support_count": int(rule.get("support_count", 1)),
                    "contradiction_count": int(rule.get("contradiction_count", 0)),
                    "state": rule.get("state", "stable"),
                    "created_at": float(rule.get("created_at", now)),
                    "updated_at": float(rule.get("updated_at", now)),
                    "last_validated_at": float(rule.get("last_validated_at", now)),
                    "valid_from": float(rule.get("valid_from", now)),
                    "valid_to": rule.get("valid_to"),
                    "evidence_episode_ids": list(rule.get("evidence_episode_ids", [])),
                    "superseded_by": rule.get("superseded_by"),
                    "expires_at": rule.get("expires_at"),
                    "challenge_reasons": list(rule.get("challenge_reasons", [])),
                }
            rows.append(
                (
                    rule["id"],
                    self._encrypt(rule["text"]),
                    rule["layer"] if rule["layer"] in PERSONA_LAYERS else "adaptive",
                    rule["scope"],
                    rule["source"],
                    float(rule["confidence"]),
                    int(rule["support_count"]),
                    int(rule["contradiction_count"]),
                    rule["state"] if rule["state"] in RULE_STATES else "proposed",
                    float(rule["created_at"]),
                    float(rule["updated_at"]),
                    float(rule["last_validated_at"]),
                    float(rule["valid_from"]),
                    rule["valid_to"],
                    self._store.dumps_json(rule["evidence_episode_ids"]),
                    rule["superseded_by"],
                    rule["expires_at"],
                    self._store.dumps_json(rule["challenge_reasons"]),
                )
            )
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    rule_id, encrypted_text, layer, scope, source, confidence,
                    support_count, contradiction_count, state, created_at, updated_at,
                    last_validated_at, valid_from, valid_to, evidence_episode_ids_json,
                    superseded_by, expires_at, challenge_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _row_to_rule(self, row) -> Dict[str, Any]:
        rule = {
            "id": row["rule_id"],
            "text": self._decrypt(row["encrypted_text"]),
            "layer": row["layer"],
            "scope": row["scope"],
            "source": row["source"],
            "confidence": row["confidence"],
            "support_count": row["support_count"],
            "contradiction_count": row["contradiction_count"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_validated_at": row["last_validated_at"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "evidence_episode_ids": self._store.loads_json(row["evidence_episode_ids_json"], []),
            "superseded_by": row["superseded_by"],
            "expires_at": row["expires_at"],
        }
        challenge_reasons = self._store.loads_json(row["challenge_reasons_json"], [])
        if challenge_reasons:
            rule["challenge_reasons"] = challenge_reasons
        return rule

    def add_rule(
        self,
        rule_text: str,
        scope: str = "project",
        confidence: float = 0.55,
        evidence_episode_ids: Optional[List[str]] = None,
        source: str = "hippocampus",
        promotion_threshold: int = 3,
        layer: Optional[str] = None,
    ) -> str:
        self.load()
        existing_rule_id = self._find_rule_id(rule_text, scope)
        now = time.time()
        resolved_layer = layer or self._infer_layer(scope, confidence, source)

        if existing_rule_id:
            rule = self._rules[existing_rule_id]
            rule["support_count"] += 1
            rule["confidence"] = min(0.99, rule["confidence"] + 0.08)
            rule["last_validated_at"] = now
            rule["updated_at"] = now
            rule["evidence_episode_ids"] = sorted(
                set(rule["evidence_episode_ids"] + (evidence_episode_ids or []))
            )
            if rule["support_count"] >= promotion_threshold:
                rule["state"] = "stable"
            elif rule["support_count"] >= 2:
                rule["state"] = "reinforced"
            if rule["layer"] != "core" and resolved_layer == "core" and rule["confidence"] >= 0.9:
                rule["layer"] = "core"
            elif rule["layer"] == "adaptive" and rule["state"] in {"reinforced", "stable"}:
                rule["layer"] = "project" if rule["scope"] in {"project", "organization"} else "adaptive"
            self.save()
            return existing_rule_id

        rule_id = f"rule_{uuid.uuid4().hex}"
        self._rules[rule_id] = {
            "id": rule_id,
            "text": rule_text,
            "layer": resolved_layer if resolved_layer in PERSONA_LAYERS else "adaptive",
            "scope": scope,
            "source": source,
            "confidence": max(0.0, min(confidence, 0.99)),
            "support_count": 1,
            "contradiction_count": 0,
            "state": "stable" if resolved_layer == "core" and source == "human-approved" else "proposed",
            "created_at": now,
            "updated_at": now,
            "last_validated_at": now,
            "valid_from": now,
            "valid_to": None,
            "evidence_episode_ids": evidence_episode_ids or [],
            "superseded_by": None,
            "expires_at": None,
            "challenge_reasons": [],
        }
        self.save()
        return rule_id

    def challenge_rule(self, rule_id: str, reason: str = "", decay: float = 0.15) -> bool:
        self.load()
        rule = self._rules.get(rule_id)
        if not rule:
            return False
        rule["contradiction_count"] += 1
        rule["confidence"] = max(0.05, rule["confidence"] - decay)
        rule["state"] = "challenged"
        rule["updated_at"] = time.time()
        if reason:
            rule.setdefault("challenge_reasons", []).append(reason)
        self.save()
        return True

    def govern_exception_rule(self, rule_id: str, confidence_bonus: float = 0.03) -> bool:
        self.load()
        rule = self._rules.get(rule_id)
        if not rule:
            return False
        rule["confidence"] = min(0.99, max(rule["confidence"], 0.68) + confidence_bonus)
        if rule["support_count"] >= 2:
            rule["state"] = "reinforced"
        elif rule["state"] == "challenged":
            rule["state"] = "proposed"
        rule["updated_at"] = time.time()
        rule["last_validated_at"] = time.time()
        self.save()
        return True

    def deprecate_rule(self, rule_id: str, superseded_by: Optional[str] = None) -> bool:
        self.load()
        rule = self._rules.get(rule_id)
        if not rule:
            return False
        rule["state"] = "deprecated"
        rule["valid_to"] = time.time()
        rule["superseded_by"] = superseded_by
        rule["updated_at"] = time.time()
        self.save()
        return True

    def get_rules(self) -> Dict[str, str]:
        self.load()
        return {rule_id: rule["text"] for rule_id, rule in self._rules.items()}

    def get_rule_records(self) -> Dict[str, Dict[str, Any]]:
        self.load()
        return {rule_id: dict(rule) for rule_id, rule in self._rules.items()}

    def get_layer_records(self) -> Dict[str, List[Dict[str, Any]]]:
        self.load()
        grouped = {"core": [], "project": [], "adaptive": []}
        for rule in self._rules.values():
            layer = rule.get("layer", "adaptive")
            grouped.setdefault(layer, []).append(dict(rule))
        for layer in grouped:
            grouped[layer].sort(
                key=lambda item: (-item.get("confidence", 0.0), -item.get("support_count", 0))
            )
        return grouped

    @property
    def rule_count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._table}")
        return int(row["total"]) if row else 0

    def get_identity_prompt(self, include_scopes: Optional[List[str]] = None) -> str:
        self.load()
        include_scopes = include_scopes or ["core", "project", "agent", "organization"]
        selected_by_layer = {"core": [], "project": [], "adaptive": []}
        for rule in self._rules.values():
            if rule["scope"] not in include_scopes:
                continue
            if rule["state"] not in {"reinforced", "stable"}:
                continue
            threshold = 0.8 if rule.get("layer") == "core" else 0.6
            if rule["confidence"] < threshold:
                continue
            selected_by_layer.setdefault(rule.get("layer", "adaptive"), []).append(rule)

        if not any(selected_by_layer.values()):
            return (
                f"You are {self.name}. "
                "No stable self-model rules have been consolidated yet."
            )

        lines = []
        index = 1
        for layer_name in ("core", "project", "adaptive"):
            layer_rules = sorted(
                selected_by_layer[layer_name],
                key=lambda item: (-item["confidence"], -item["support_count"]),
            )
            if not layer_rules:
                continue
            lines.append(f"{layer_name.upper()} RULES:")
            for rule in layer_rules[:4]:
                lines.append(
                    f"{index}. [{rule['scope']}/{layer_name}] {rule['text']} "
                    f"(confidence={rule['confidence']:.2f}, support={rule['support_count']})"
                )
                index += 1

        return (
            f"You are {self.name}. Apply the following stable self-model rules "
            "unless current evidence clearly contradicts them:\n\n"
            + "\n".join(lines)
        )

    def save(self):
        rows = []
        for rule in self._rules.values():
            rows.append(
                (
                    rule["id"],
                    self._encrypt(rule["text"]),
                    rule.get("layer", "adaptive"),
                    rule.get("scope", "project"),
                    rule.get("source", "legacy"),
                    float(rule.get("confidence", 0.7)),
                    int(rule.get("support_count", 1)),
                    int(rule.get("contradiction_count", 0)),
                    rule.get("state", "proposed"),
                    float(rule.get("created_at", time.time())),
                    float(rule.get("updated_at", time.time())),
                    float(rule.get("last_validated_at", time.time())),
                    float(rule.get("valid_from", time.time())),
                    rule.get("valid_to"),
                    self._store.dumps_json(rule.get("evidence_episode_ids", [])),
                    rule.get("superseded_by"),
                    rule.get("expires_at"),
                    self._store.dumps_json(rule.get("challenge_reasons", [])),
                )
            )
        self._store.execute(f"DELETE FROM {self._table}")
        if rows:
            self._store.executemany(
                f"""
                INSERT OR REPLACE INTO {self._table} (
                    rule_id, encrypted_text, layer, scope, source, confidence,
                    support_count, contradiction_count, state, created_at, updated_at,
                    last_validated_at, valid_from, valid_to, evidence_episode_ids_json,
                    superseded_by, expires_at, challenge_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load(self):
        rows = self._store.fetchall(f"SELECT * FROM {self._table}")
        loaded_rules: Dict[str, Dict[str, Any]] = {}
        now = time.time()
        for row in rows:
            rule = self._row_to_rule(row)
            rule.setdefault("layer", "adaptive")
            rule.setdefault("scope", "project")
            rule.setdefault("source", "legacy")
            rule.setdefault("confidence", 0.7)
            rule.setdefault("support_count", 1)
            rule.setdefault("contradiction_count", 0)
            rule.setdefault("state", "stable")
            rule.setdefault("created_at", now)
            rule.setdefault("updated_at", now)
            rule.setdefault("last_validated_at", now)
            rule.setdefault("valid_from", now)
            rule.setdefault("valid_to", None)
            rule.setdefault("evidence_episode_ids", [])
            rule.setdefault("superseded_by", None)
            rule.setdefault("expires_at", None)
            if rule["layer"] not in PERSONA_LAYERS:
                rule["layer"] = "adaptive"
            if rule["state"] not in RULE_STATES:
                rule["state"] = "proposed"
            loaded_rules[rule["id"]] = rule
        self._rules = loaded_rules

    def __repr__(self) -> str:
        return f"Persona(name='{self.name}', rules={self.rule_count}, encrypted=True)"
