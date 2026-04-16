"""
memory/l2_semantic.py — Long-term Semantic Memory with Time-Decay.

Lưu trữ vector embeddings của các "Sự thật" (Facts) đã được chắt lọc.
Hỗ trợ tìm kiếm ngữ nghĩa (Semantic Search) và áp dụng hàm suy giảm 
theo thời gian Ebbinghaus: W(t) = W0 * e^(-λt)

Storage backends: In-memory (default), Qdrant, ChromaDB.
"""

import os
import json
import math
import time
from typing import List, Dict, Any, Optional


class SemanticEntry:
    """A single vector entry in L2 memory."""
    def __init__(
        self,
        content: str,
        importance: float = 5.0,
        tags: List[str] = None,
        source_rule_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ):
        self.content = content
        self.importance = importance
        self.tags = tags or []
        self.source_rule_id = source_rule_id
        self.timestamp = timestamp or time.time()
        self.access_count = 0
        self.last_accessed = self.timestamp

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "importance": self.importance,
            "tags": self.tags,
            "source_rule_id": self.source_rule_id,
            "timestamp": self.timestamp,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticEntry":
        entry = cls(
            content=d["content"],
            importance=d.get("importance", 5.0),
            tags=d.get("tags", []),
            source_rule_id=d.get("source_rule_id"),
            timestamp=d.get("timestamp", time.time()),
        )
        entry.access_count = d.get("access_count", 0)
        entry.last_accessed = d.get("last_accessed", entry.timestamp)
        return entry


class L2SemanticMemory:
    """
    Long-term semantic memory with time-decay scoring.
    
    In production, this should be backed by a Vector DB (Qdrant, ChromaDB).
    The default implementation uses keyword-based matching for simplicity.
    
    Usage:
        l2 = L2SemanticMemory(agent_name="my-agent")
        l2.store("API cần async/await khi gọi DB", importance=8, tags=["coding"])
        results = l2.search("database async", top_k=3)
    """

    def __init__(
        self,
        agent_name: str = "Bio-AI",
        storage_dir: str = "data",
        decay_lambda: float = 0.05,
    ):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self.decay_lambda = decay_lambda
        self._entries: List[SemanticEntry] = []
        self._filepath = os.path.join(storage_dir, f"{agent_name}_l2_semantic.json")
        self.load()

    # ─── Store & Retrieve ─────────────────────────────────────

    def store(
        self,
        content: str,
        importance: float = 5.0,
        tags: List[str] = None,
        source_rule_id: Optional[str] = None,
    ) -> SemanticEntry:
        """Store a semantic fact in L2."""
        entry = SemanticEntry(
            content=content,
            importance=importance,
            tags=tags,
            source_rule_id=source_rule_id,
        )
        self._entries.append(entry)
        self.save()
        return entry

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search L2 using keyword similarity + time-decay scoring.
        
        Score = keyword_overlap * importance * decay_factor
        In production, replace keyword matching with cosine similarity on embeddings.
        """
        query_tokens = set(query.lower().split())
        now = time.time()
        scored = []

        for entry in self._entries:
            content_tokens = set(entry.content.lower().split())
            tag_tokens = set(t.lower() for t in entry.tags)
            all_tokens = content_tokens | tag_tokens

            # Keyword overlap score
            overlap = len(query_tokens & all_tokens)
            if overlap == 0:
                continue

            # Time-decay: Ebbinghaus forgetting curve
            days_elapsed = (now - entry.timestamp) / 86400
            decay = math.exp(-self.decay_lambda * days_elapsed)

            # Final score
            score = overlap * entry.importance * decay

            entry.access_count += 1
            entry.last_accessed = now

            scored.append({
                "content": entry.content,
                "score": round(score, 3),
                "importance": entry.importance,
                "decay": round(decay, 3),
                "tags": entry.tags,
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        self.save()
        return scored[:top_k]

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all L2 entries as dicts."""
        return [e.to_dict() for e in self._entries]

    @property
    def count(self) -> int:
        return len(self._entries)

    # ─── Pruning ──────────────────────────────────────────────

    def prune_decayed(self, threshold: float = 1.0) -> int:
        """Remove entries whose time-decayed score falls below threshold."""
        now = time.time()
        before = len(self._entries)
        survivors = []
        for entry in self._entries:
            days = (now - entry.timestamp) / 86400
            decayed_score = entry.importance * math.exp(-self.decay_lambda * days)
            if decayed_score >= threshold:
                survivors.append(entry)
        self._entries = survivors
        self.save()
        return before - len(self._entries)

    # ─── Persistence ──────────────────────────────────────────

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        data = [e.to_dict() for e in self._entries]
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._entries = [SemanticEntry.from_dict(d) for d in data]
            except (json.JSONDecodeError, IOError):
                self._entries = []

    def __repr__(self) -> str:
        return f"L2SemanticMemory(agent='{self.agent_name}', entries={self.count})"
