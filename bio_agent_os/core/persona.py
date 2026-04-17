"""
Self-model and persona memory for Bio-Agent OS V2.
"""

import base64
import json
import os
import time
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


RULE_STATES = {
    "proposed",
    "reinforced",
    "stable",
    "challenged",
    "deprecated",
    "archived",
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
        self._filepath = os.path.join(storage_dir, f"{name}_core_identity.json")
        self._crypto_key = os.getenv(
            "BIO_AGENT_SECRET_KEY",
            "default-insecure-key-change-in-prod-erp",
        )
        self._fernet = self._setup_cipher()
        self.load()

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

    def add_rule(
        self,
        rule_text: str,
        scope: str = "project",
        confidence: float = 0.55,
        evidence_episode_ids: Optional[List[str]] = None,
        source: str = "hippocampus",
        promotion_threshold: int = 3,
    ) -> str:
        existing_rule_id = self._find_rule_id(rule_text, scope)
        now = time.time()

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
            self.save()
            return existing_rule_id

        rule_id = f"rule_{int(now * 1000)}"
        self._rules[rule_id] = {
            "id": rule_id,
            "text": rule_text,
            "scope": scope,
            "source": source,
            "confidence": max(0.0, min(confidence, 0.99)),
            "support_count": 1,
            "contradiction_count": 0,
            "state": "proposed",
            "created_at": now,
            "updated_at": now,
            "last_validated_at": now,
            "valid_from": now,
            "valid_to": None,
            "evidence_episode_ids": evidence_episode_ids or [],
            "superseded_by": None,
            "expires_at": None,
        }
        self.save()
        return rule_id

    def challenge_rule(self, rule_id: str, reason: str = "", decay: float = 0.15) -> bool:
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

    def deprecate_rule(self, rule_id: str, superseded_by: Optional[str] = None) -> bool:
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
        return {rule_id: rule["text"] for rule_id, rule in self._rules.items()}

    def get_rule_records(self) -> Dict[str, Dict[str, Any]]:
        return {rule_id: dict(rule) for rule_id, rule in self._rules.items()}

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def get_identity_prompt(self, include_scopes: Optional[List[str]] = None) -> str:
        include_scopes = include_scopes or ["core", "project", "agent", "organization"]
        selected: List[Dict[str, Any]] = []
        for rule in self._rules.values():
            if rule["scope"] not in include_scopes:
                continue
            if rule["state"] not in {"reinforced", "stable"}:
                continue
            if rule["confidence"] < 0.6:
                continue
            selected.append(rule)

        selected.sort(
            key=lambda item: (item["scope"] != "core", -item["confidence"], -item["support_count"])
        )

        if not selected:
            return (
                f"You are {self.name}. "
                "No stable self-model rules have been consolidated yet."
            )

        lines = []
        for idx, rule in enumerate(selected[:12], start=1):
            lines.append(
                f"{idx}. [{rule['scope']}] {rule['text']} "
                f"(confidence={rule['confidence']:.2f}, support={rule['support_count']})"
            )

        return (
            f"You are {self.name}. Apply the following stable self-model rules "
            "unless current evidence clearly contradicts them:\n\n"
            + "\n".join(lines)
        )

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        encrypted_rules: Dict[str, Dict[str, Any]] = {}
        for rule_id, rule in self._rules.items():
            encrypted_rule = dict(rule)
            encrypted_rule["text"] = self._encrypt(rule["text"])
            encrypted_rules[rule_id] = encrypted_rule

        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "name": self.name,
                    "rules": encrypted_rules,
                    "updated_at": time.time(),
                    "encrypted": True,
                    "schema_version": 2,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    def load(self):
        if not os.path.exists(self._filepath):
            return

        with open(self._filepath, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        is_encrypted = data.get("encrypted", False)
        raw_rules = data.get("rules", {})

        loaded_rules: Dict[str, Dict[str, Any]] = {}
        for rule_id, raw_rule in raw_rules.items():
            if isinstance(raw_rule, str):
                loaded_rules[rule_id] = {
                    "id": rule_id,
                    "text": self._decrypt(raw_rule) if is_encrypted else raw_rule,
                    "scope": "project",
                    "source": "legacy",
                    "confidence": 0.7,
                    "support_count": 1,
                    "contradiction_count": 0,
                    "state": "stable",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "last_validated_at": time.time(),
                    "valid_from": time.time(),
                    "valid_to": None,
                    "evidence_episode_ids": [],
                    "superseded_by": None,
                    "expires_at": None,
                }
                continue

            rule = dict(raw_rule)
            if is_encrypted:
                rule["text"] = self._decrypt(rule["text"])
            rule.setdefault("id", rule_id)
            rule.setdefault("scope", "project")
            rule.setdefault("source", "legacy")
            rule.setdefault("confidence", 0.7)
            rule.setdefault("support_count", 1)
            rule.setdefault("contradiction_count", 0)
            rule.setdefault("state", "stable")
            rule.setdefault("created_at", time.time())
            rule.setdefault("updated_at", time.time())
            rule.setdefault("last_validated_at", time.time())
            rule.setdefault("valid_from", time.time())
            rule.setdefault("valid_to", None)
            rule.setdefault("evidence_episode_ids", [])
            rule.setdefault("superseded_by", None)
            rule.setdefault("expires_at", None)
            if rule["state"] not in RULE_STATES:
                rule["state"] = "proposed"
            loaded_rules[rule_id] = rule

        self._rules = loaded_rules

    def __repr__(self) -> str:
        return f"Persona(name='{self.name}', rules={self.rule_count}, encrypted=True)"
