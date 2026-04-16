"""
memory/knowledge_graph.py — Knowledge Graph (Đồ thị tri thức).

Mapping các thực thể (Entities) và mối quan hệ (Relationships).
Biến AI từ "máy nén text" thành "Cơ sở dữ liệu tư duy" (Reasoning Database).

Storage backends: NetworkX (default, in-memory), Neo4j (production).
"""

import os
import json
import time
from typing import List, Dict, Any, Optional, Tuple


class KnowledgeGraph:
    """
    In-memory Knowledge Graph using adjacency list.
    
    Nodes = Entities (người, công nghệ, dự án, khái niệm)
    Edges = Relationships (sử_dụng, quản_lý, liên_quan, tạo_ra)
    
    Usage:
        kg = KnowledgeGraph(agent_name="my-agent")
        kg.add_entity("FastAPI", type="technology")
        kg.add_entity("Tuấn Anh", type="person")
        kg.add_relation("Tuấn Anh", "sử_dụng", "FastAPI")
        results = kg.query_relations("Tuấn Anh")
    """

    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, Any]] = []
        self._filepath = os.path.join(storage_dir, f"{agent_name}_knowledge_graph.json")
        self.load()

    # ─── Entity Management ────────────────────────────────────

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add or update an entity node."""
        key = name.lower().strip()
        if key in self._nodes:
            # Update properties
            if properties:
                self._nodes[key]["properties"].update(properties)
            self._nodes[key]["updated_at"] = time.time()
            self.save()
            return False  # Already existed
        
        self._nodes[key] = {
            "name": name,
            "type": entity_type,
            "properties": properties or {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.save()
        return True  # Newly created

    def get_entity(self, name: str) -> Optional[Dict[str, Any]]:
        """Get an entity by name."""
        return self._nodes.get(name.lower().strip())

    def remove_entity(self, name: str) -> bool:
        """Remove an entity and all its edges."""
        key = name.lower().strip()
        if key not in self._nodes:
            return False
        del self._nodes[key]
        self._edges = [
            e for e in self._edges
            if e["source"].lower() != key and e["target"].lower() != key
        ]
        self.save()
        return True

    # ─── Relationship Management ──────────────────────────────

    def add_relation(
        self,
        source: str,
        relation: str,
        target: str,
        weight: float = 1.0,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add a directed relationship between two entities."""
        src_key = source.lower().strip()
        tgt_key = target.lower().strip()

        # Auto-create entities if missing
        if src_key not in self._nodes:
            self.add_entity(source)
        if tgt_key not in self._nodes:
            self.add_entity(target)

        # Check for duplicate
        for e in self._edges:
            if (e["source"].lower() == src_key and 
                e["target"].lower() == tgt_key and
                e["relation"] == relation):
                e["weight"] = weight  # Update weight
                self.save()
                return False

        self._edges.append({
            "source": source,
            "relation": relation,
            "target": target,
            "weight": weight,
            "properties": properties or {},
            "created_at": time.time(),
        })
        self.save()
        return True

    # ─── Queries ──────────────────────────────────────────────

    def query_relations(self, entity_name: str) -> List[Dict[str, Any]]:
        """Get all relationships linked to an entity (both directions)."""
        key = entity_name.lower().strip()
        results = []
        for e in self._edges:
            if e["source"].lower() == key or e["target"].lower() == key:
                results.append(e)
        return results

    def query_by_type(self, entity_type: str) -> List[Dict[str, Any]]:
        """Get all entities of a specific type."""
        return [
            node for node in self._nodes.values()
            if node["type"] == entity_type
        ]

    def find_path(self, source: str, target: str, max_depth: int = 4) -> List[str]:
        """BFS shortest path between two entities."""
        src = source.lower().strip()
        tgt = target.lower().strip()
        if src not in self._nodes or tgt not in self._nodes:
            return []

        adjacency: Dict[str, List[str]] = {}
        for e in self._edges:
            s, t = e["source"].lower(), e["target"].lower()
            adjacency.setdefault(s, []).append(t)
            adjacency.setdefault(t, []).append(s)

        visited = {src}
        queue = [(src, [src])]
        while queue:
            current, path = queue.pop(0)
            if current == tgt:
                return path
            if len(path) >= max_depth:
                continue
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return []

    def build_context_string(self, entity_name: str) -> str:
        """Build a context string for LLM injection from an entity's relations."""
        relations = self.query_relations(entity_name)
        if not relations:
            return f"(Không tìm thấy thông tin về '{entity_name}' trong Đồ thị Tri thức.)"
        lines = []
        for r in relations:
            lines.append(f"  {r['source']} --[{r['relation']}]--> {r['target']}")
        return f"Đồ thị tri thức liên quan đến '{entity_name}':\n" + "\n".join(lines)

    # ─── Stats ────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    # ─── Persistence ──────────────────────────────────────────

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as f:
            json.dump({
                "nodes": self._nodes,
                "edges": self._edges,
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._nodes = data.get("nodes", {})
                    self._edges = data.get("edges", [])
            except (json.JSONDecodeError, IOError):
                self._nodes = {}
                self._edges = []

    def __repr__(self) -> str:
        return f"KnowledgeGraph(nodes={self.node_count}, edges={self.edge_count})"
