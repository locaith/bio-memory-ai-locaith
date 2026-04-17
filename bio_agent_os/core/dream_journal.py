"""
Persistent dream reports.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List


class DreamJournal:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._filepath = os.path.join(storage_dir, f"{agent_name}_dream_reports.json")
        self._reports: List[Dict[str, Any]] = []
        self.load()

    def append(self, report: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "report_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            **report,
        }
        self._reports.append(entry)
        self.save()
        return entry

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._reports[-limit:]

    @property
    def count(self) -> int:
        return len(self._reports)

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump(self._reports, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    self._reports = json.load(handle)
            except (json.JSONDecodeError, OSError):
                self._reports = []
