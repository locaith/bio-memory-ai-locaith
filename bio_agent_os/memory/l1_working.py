"""
memory/l1_working.py — Short-term Working Memory (Bộ đệm ngắn hạn).

Mô phỏng vùng Prefrontal Cortex — lưu trữ hội thoại gần đây,
dữ liệu thô chưa qua xử lý. Có cơ chế TTL (Time-To-Live).

Storage backends: In-memory (default), Redis, DuckDB.
"""

import os
import json
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class MemoryEntry(BaseModel):
    """Schema cho một mục ký ức trong L1."""
    content: str
    source: str = "unknown"
    metadata: Dict[str, Any] = {}
    timestamp: float = 0.0
    status: str = "raw"         # raw | processed | encoded
    nights_passed: int = 0      # TTL counter (số đêm đã qua)
    ttl: int = 2                # Tự hủy sau N đêm nếu là rác


class L1WorkingMemory:
    """
    Short-term episodic buffer — CRUD operations on recent events.
    
    Data flows:
      User input → Hippocampus labels it → L1 stores it
      Background job reads L1 → Prunes/Encodes → Moves to L2 or deletes
    
    Usage:
        l1 = L1WorkingMemory(agent_name="my-agent")
        l1.add("User said something important", source="user", metadata={...})
        recent = l1.get_recent(n=5)
        l1.increment_nights()  # Called during "sleep" cycle
    """

    def __init__(
        self,
        agent_name: str = "Bio-AI",
        storage_dir: str = "data",
        max_entries: int = 100,
    ):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self.max_entries = max_entries
        self._entries: List[Dict[str, Any]] = []
        self._filepath = os.path.join(storage_dir, f"{agent_name}_l1_memory.json")
        self.load()

    # ─── CRUD Operations ──────────────────────────────────────

    def add(
        self,
        content: str,
        source: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
        ttl: int = 2,
    ) -> Dict[str, Any]:
        """Add a new memory entry to the working buffer."""
        entry = MemoryEntry(
            content=content,
            source=source,
            metadata=metadata or {},
            timestamp=time.time(),
            status="raw",
            nights_passed=0,
            ttl=ttl,
        ).model_dump()

        self._entries.append(entry)

        # Overflow protection
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]

        self.save()
        return entry

    def get_recent(self, n: int = 5) -> List[Dict[str, Any]]:
        """Get the N most recent entries."""
        return self._entries[-n:]

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all entries in L1."""
        return list(self._entries)

    def get_raw(self) -> List[Dict[str, Any]]:
        """Get entries that haven't been processed yet."""
        return [e for e in self._entries if e["status"] == "raw"]

    def get_survivors(self) -> List[Dict[str, Any]]:
        """Get entries that survived past their TTL (ready for encoding)."""
        return [
            e for e in self._entries
            if e["status"] == "raw" and e.get("nights_passed", 0) > e.get("ttl", 2)
        ]

    def mark_encoded(self, timestamp: float):
        """Mark an entry as encoded (already compressed into Core Logic)."""
        for e in self._entries:
            if e["timestamp"] == timestamp:
                e["status"] = "encoded"
        self.save()

    def remove_by_timestamps(self, timestamps: List[float]):
        """Remove specific entries by their timestamps."""
        ts_set = set(timestamps)
        self._entries = [e for e in self._entries if e["timestamp"] not in ts_set]
        self.save()

    def clear(self):
        """Wipe all L1 memory (hard reset)."""
        self._entries.clear()
        self.save()

    @property
    def count(self) -> int:
        return len(self._entries)

    # ─── Night Cycle (TTL) ────────────────────────────────────

    def increment_nights(self):
        """
        Called during each 'sleep' cycle. 
        Increments the nights_passed counter for all entries.
        """
        for entry in self._entries:
            entry["nights_passed"] = entry.get("nights_passed", 0) + 1
        self.save()

    # ─── Context Builder ──────────────────────────────────────

    def build_context_string(self, n: int = 5) -> str:
        """Build a context string from recent entries for LLM injection."""
        recent = self.get_recent(n)
        if not recent:
            return "(Không có sự kiện gần đây nào.)"
        return "\n".join(
            f"- [{e['source']}] {e['content']}" for e in recent
        )

    # ─── Persistence ──────────────────────────────────────────

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    self._entries = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._entries = []

    def __repr__(self) -> str:
        return f"L1WorkingMemory(agent='{self.agent_name}', entries={self.count})"
