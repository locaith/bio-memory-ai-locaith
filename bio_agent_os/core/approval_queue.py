"""
Human approval queue for sensitive rule promotion and deprecation.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


SENSITIVE_MARKERS = {
    "force push",
    "git push -f",
    "auth",
    "authentication",
    "security",
    "credential",
    "secret",
    "migration",
    "payment",
    "production",
    "prod",
    "delete",
}


class ApprovalQueue:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._filepath = os.path.join(storage_dir, f"{agent_name}_approval_queue.json")
        self._requests: List[Dict[str, Any]] = []
        self.load()

    def requires_approval(self, rule_text: str, scope: str = "project", confidence: float = 0.0) -> bool:
        normalized = rule_text.lower()
        if scope == "core":
            return True
        if confidence >= 0.9:
            return True
        return any(marker in normalized for marker in SENSITIVE_MARKERS)

    def submit(
        self,
        action_type: str,
        rule_text: str,
        scope: str = "project",
        confidence: float = 0.0,
        target_rule_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        existing = next(
            (
                request for request in self._requests
                if request["status"] == "pending"
                and request["action_type"] == action_type
                and request["rule_text"] == rule_text
                and request.get("target_rule_id") == target_rule_id
            ),
            None,
        )
        if existing:
            return existing

        request = {
            "request_id": str(uuid.uuid4()),
            "created_at": time.time(),
            "updated_at": time.time(),
            "status": "pending",
            "action_type": action_type,
            "rule_text": rule_text,
            "scope": scope,
            "confidence": confidence,
            "target_rule_id": target_rule_id,
            "metadata": metadata or {},
        }
        self._requests.append(request)
        self.save()
        return request

    def resolve(self, request_id: str, decision: str, reviewer: str = "human") -> Optional[Dict[str, Any]]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected.")
        for request in self._requests:
            if request["request_id"] == request_id:
                request["status"] = decision
                request["reviewed_by"] = reviewer
                request["updated_at"] = time.time()
                self.save()
                return request
        return None

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        for request in self._requests:
            if request["request_id"] == request_id:
                return dict(request)
        return None

    def list(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        requests = self._requests
        if status:
            requests = [request for request in requests if request["status"] == status]
        return [dict(request) for request in requests[-limit:]]

    @property
    def pending_count(self) -> int:
        return len([request for request in self._requests if request["status"] == "pending"])

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(self._requests, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    self._requests = json.load(handle)
            except (json.JSONDecodeError, OSError):
                self._requests = []
