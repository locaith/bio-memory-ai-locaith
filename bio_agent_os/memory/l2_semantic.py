"""
Long-term semantic memory with time decay.
"""

import os
import math
import time
import uuid
from typing import List, Dict, Any, Optional

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False


class SemanticEntry:
    def __init__(self, content: str, importance: float = 5.0, tags: List[str] = None):
        self.content = content
        self.importance = importance
        self.tags = tags or []
        self.timestamp = time.time()


class L2SemanticMemory:
    """
    Enterprise-scale L2 Semantic memory via Qdrant Client.
    Will fall back to in-memory Qdrant instance if no URL is provided.
    """

    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.collection_name = f"{agent_name}_l2"
        self.decay_lambda = 0.05
        
        url = os.getenv("QDRANT_URL", None)
        api_key = os.getenv("QDRANT_API_KEY", None)
        
        if QDRANT_AVAILABLE:
            if url:
                self.client = QdrantClient(url=url, api_key=api_key)
            else:
                self.client = QdrantClient(":memory:")
            self._ensure_collection()
            self._fallback = False
        else:
            self._fallback = True
            self._entries = []
            
    def _ensure_collection(self):
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )

    # Note: In a production ERP, you would embed the text via OpenAI or BGE models.
    # We will use mock randomly generated vectors for this pipeline demo.
    def _mock_embed(self, text: str) -> List[float]:
        import random
        # deterministic mock based on length for simple testing without heavy ML packages
        random.seed(len(text)) 
        return [random.uniform(-1, 1) for _ in range(384)]

    def store(self, content: str, importance: float = 5.0, tags: List[str] = None, source_rule_id: Optional[str] = None):
        if self._fallback:
            self._entries.append(SemanticEntry(content, importance, tags))
            return

        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=self._mock_embed(content),
            payload={
                "content": content,
                "importance": importance,
                "tags": tags or [],
                "source_rule_id": source_rule_id,
                "timestamp": time.time()
            }
        )
        self.client.upsert(
            collection_name=self.collection_name,
            points=[point]
        )

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if self._fallback:
            return [{"content": e.content, "score": 1.0, "importance": e.importance} for e in self._entries[:top_k]]
            
        points_data = self.client.query_points(
            collection_name=self.collection_name,
            query=self._mock_embed(query),
            limit=top_k * 2 # get more to decay
        )
        points = points_data.points

        
        results = []
        now = time.time()
        for hit in points:
            payload = hit.payload
            importance = payload["importance"]
            timestamp = payload["timestamp"]
            
            # Apply Time Decay W(t)
            days_elapsed = (now - timestamp) / 86400
            decay = math.exp(-self.decay_lambda * days_elapsed)
            final_score = hit.score * importance * decay
            
            results.append({
                "content": payload["content"],
                "score": final_score,
                "importance": importance,
                "tags": payload["tags"]
            })
            
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def prune_decayed(self, threshold: float = 1.0) -> int:
        if self._fallback:
            return 0
            
        # In a real Qdrant integration, scroll through records, compute decay, and delete. 
        # Simulated here for framework structural integrity.
        points, _ = self.client.scroll(collection_name=self.collection_name, limit=1000)
        to_delete = []
        now = time.time()
        
        for p in points:
            days_elapsed = (now - p.payload["timestamp"]) / 86400
            decay_score = p.payload["importance"] * math.exp(-self.decay_lambda * days_elapsed)
            if decay_score < threshold:
                to_delete.append(p.id)
                
        if to_delete:
            self.client.delete(collection_name=self.collection_name, points_selector=to_delete)
            
        return len(to_delete)

    @property
    def count(self) -> int:
        if self._fallback:
            return len(self._entries)
        return self.client.count(collection_name=self.collection_name).count
