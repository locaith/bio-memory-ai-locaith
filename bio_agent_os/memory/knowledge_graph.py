"""
Knowledge graph and belief graph storage.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional


class KnowledgeGraph:
    """
    In-memory graph with two practical uses:
    - world/code graph for entities and relations
    - belief graph for rules, evidence episodes, contradictions, and supersession
    """

    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.agent_name = agent_name
        self.storage_dir = storage_dir
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, Any]] = []
        self._filepath = os.path.join(storage_dir, f"{agent_name}_knowledge_graph.json")
        self.load()

    def _node_key(self, name: str) -> str:
        return name.lower().strip()

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        key = self._node_key(name)
        if key in self._nodes:
            if properties:
                self._nodes[key]["properties"].update(properties)
            self._nodes[key]["updated_at"] = time.time()
            self.save()
            return False

        self._nodes[key] = {
            "name": name,
            "type": entity_type,
            "properties": properties or {},
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.save()
        return True

    def get_entity(self, name: str) -> Optional[Dict[str, Any]]:
        return self._nodes.get(self._node_key(name))

    def remove_entity(self, name: str) -> bool:
        key = self._node_key(name)
        if key not in self._nodes:
            return False
        del self._nodes[key]
        self._edges = [
            edge
            for edge in self._edges
            if self._node_key(edge["source"]) != key and self._node_key(edge["target"]) != key
        ]
        self.save()
        return True

    def add_relation(
        self,
        source: str,
        relation: str,
        target: str,
        weight: float = 1.0,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        src_key = self._node_key(source)
        tgt_key = self._node_key(target)

        if src_key not in self._nodes:
            self.add_entity(source)
        if tgt_key not in self._nodes:
            self.add_entity(target)

        for edge in self._edges:
            if (
                self._node_key(edge["source"]) == src_key
                and self._node_key(edge["target"]) == tgt_key
                and edge["relation"] == relation
            ):
                edge["weight"] = weight
                if properties:
                    edge["properties"].update(properties)
                edge["updated_at"] = time.time()
                self.save()
                return False

        self._edges.append(
            {
                "source": source,
                "relation": relation,
                "target": target,
                "weight": weight,
                "properties": properties or {},
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        )
        self.save()
        return True

    def add_belief_rule(self, rule: Dict[str, Any]) -> bool:
        return self.add_entity(
            rule["id"],
            entity_type="belief_rule",
            properties={
                "text": rule["text"],
                "scope": rule["scope"],
                "confidence": rule["confidence"],
                "state": rule["state"],
                "support_count": rule["support_count"],
                "contradiction_count": rule["contradiction_count"],
                "valid_from": rule["valid_from"],
                "valid_to": rule["valid_to"],
                "superseded_by": rule["superseded_by"],
            },
        )

    def add_episode_evidence(self, rule_id: str, episode_id: str, confidence: float = 0.5) -> bool:
        self.add_entity(episode_id, entity_type="episode")
        return self.add_relation(
            episode_id,
            "supports",
            rule_id,
            weight=confidence,
            properties={"valid_from": time.time(), "valid_to": None},
        )

    def add_conflict(self, challenger_rule_id: str, target_rule_id: str) -> bool:
        return self.add_relation(
            challenger_rule_id,
            "conflicts_with",
            target_rule_id,
            weight=1.0,
            properties={"valid_from": time.time(), "valid_to": None},
        )

    def add_supersedes(self, newer_rule_id: str, older_rule_id: str) -> bool:
        return self.add_relation(
            newer_rule_id,
            "supersedes",
            older_rule_id,
            weight=1.0,
            properties={"valid_from": time.time(), "valid_to": None},
        )

    def query_relations(self, entity_name: str) -> List[Dict[str, Any]]:
        key = self._node_key(entity_name)
        return [
            edge
            for edge in self._edges
            if self._node_key(edge["source"]) == key or self._node_key(edge["target"]) == key
        ]

    def query_by_type(self, entity_type: str) -> List[Dict[str, Any]]:
        return [node for node in self._nodes.values() if node["type"] == entity_type]

    def belief_summary(self) -> Dict[str, Any]:
        rules = self.query_by_type("belief_rule")
        active_rules = [
            node for node in rules if node["properties"].get("state") not in {"deprecated", "archived"}
        ]
        challenged_rules = [
            node for node in rules if node["properties"].get("state") == "challenged"
        ]
        supersedes_edges = [edge for edge in self._edges if edge["relation"] == "supersedes"]
        conflict_edges = [edge for edge in self._edges if edge["relation"] == "conflicts_with"]
        support_edges = [edge for edge in self._edges if edge["relation"] == "supports"]
        return {
            "belief_rules": len(rules),
            "active_beliefs": len(active_rules),
            "challenged_beliefs": len(challenged_rules),
            "support_edges": len(support_edges),
            "conflict_edges": len(conflict_edges),
            "supersedes_edges": len(supersedes_edges),
        }

    def belief_query(self, rule_id: Optional[str] = None, active_only: bool = False) -> Dict[str, Any]:
        rules = self.query_by_type("belief_rule")
        if active_only:
            rules = [
                rule
                for rule in rules
                if rule["properties"].get("state") not in {"deprecated", "archived"}
            ]

        if rule_id is None:
            return {"rules": rules}

        selected = next((rule for rule in rules if rule["name"] == rule_id), None)
        if not selected:
            return {"rule": None, "supports": [], "conflicts_with": [], "supersedes": [], "superseded_by": []}

        supports = [edge for edge in self._edges if edge["relation"] == "supports" and edge["target"] == rule_id]
        conflicts_with = [
            edge for edge in self._edges
            if edge["relation"] == "conflicts_with" and (edge["source"] == rule_id or edge["target"] == rule_id)
        ]
        supersedes = [edge for edge in self._edges if edge["relation"] == "supersedes" and edge["source"] == rule_id]
        superseded_by = [edge for edge in self._edges if edge["relation"] == "supersedes" and edge["target"] == rule_id]

        return {
            "rule": selected,
            "supports": supports,
            "conflicts_with": conflicts_with,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
        }

    def find_path(self, source: str, target: str, max_depth: int = 4) -> List[str]:
        src = self._node_key(source)
        tgt = self._node_key(target)
        if src not in self._nodes or tgt not in self._nodes:
            return []

        adjacency: Dict[str, List[str]] = {}
        for edge in self._edges:
            src_key = self._node_key(edge["source"])
            tgt_key = self._node_key(edge["target"])
            adjacency.setdefault(src_key, []).append(tgt_key)
            adjacency.setdefault(tgt_key, []).append(src_key)

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
        relations = self.query_relations(entity_name)
        if not relations:
            return f"(No graph data found for '{entity_name}')"
        lines = [f"  {item['source']} --[{item['relation']}]--> {item['target']}" for item in relations]
        return f"Knowledge graph related to '{entity_name}':\n" + "\n".join(lines)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def save(self):
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self._filepath, "w", encoding="utf-8") as handle:
            json.dump({"nodes": self._nodes, "edges": self._edges}, handle, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                self._nodes = data.get("nodes", {})
                self._edges = data.get("edges", [])
            except (json.JSONDecodeError, OSError):
                self._nodes = {}
                self._edges = []

    def __repr__(self) -> str:
        return f"KnowledgeGraph(nodes={self.node_count}, edges={self.edge_count})"
