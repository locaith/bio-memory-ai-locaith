"""
Persistent memory audit trail and replay.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


class AuditLog:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._filepath = os.path.join(storage_dir, f"{agent_name}_audit_log.json")
        self._events: List[Dict[str, Any]] = []
        self.load()

    def append(
        self,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "event_type": event_type,
            "message": message,
            "payload": payload or {},
        }
        self._events.append(event)
        self.save()
        return event

    def recent(self, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        events = self._events
        if event_type:
            events = [event for event in events if event["event_type"] == event_type]
        return events[-limit:]

    def replay(self, since: float = 0.0, until: Optional[float] = None) -> List[Dict[str, Any]]:
        until = until or time.time()
        return [
            event
            for event in self._events
            if since <= event["timestamp"] <= until
        ]

    @property
    def count(self) -> int:
        return len(self._events)

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(self._events, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    self._events = json.load(handle)
            except (json.JSONDecodeError, OSError):
                self._events = []
