"""
Knowledge graph and belief graph storage.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from bio_agent_os.core.sqlite_store import SQLiteStore


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
        self._legacy_filepath = os.path.join(storage_dir, f"{agent_name}_knowledge_graph.json")
        self._store = SQLiteStore(storage_dir=storage_dir)
        base = self._store.sanitize_identifier(agent_name)
        self._nodes_table = f"{base}_kg_nodes"
        self._edges_table = f"{base}_kg_edges"
        self._ensure_tables()
        self._migrate_legacy_json()
        self.load()

    def _ensure_tables(self):
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._nodes_table} (
                node_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._store.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._edges_table} (
                edge_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                relation TEXT NOT NULL,
                target TEXT NOT NULL,
                weight REAL NOT NULL,
                properties_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    def _migrate_legacy_json(self):
        if not os.path.exists(self._legacy_filepath):
            return
        existing = self._store.fetchone(f"SELECT node_key FROM {self._nodes_table} LIMIT 1")
        if existing:
            return
        try:
            with open(self._legacy_filepath, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return
        self._nodes = data.get("nodes", {})
        self._edges = data.get("edges", [])
        self.save()

    def _node_key(self, name: str) -> str:
        return name.lower().strip()

    def _edge_key(self, source: str, relation: str, target: str) -> str:
        return f"{self._node_key(source)}::{relation}::{self._node_key(target)}"

    def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        self.load()
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
        self.load()
        return self._nodes.get(self._node_key(name))

    def remove_entity(self, name: str) -> bool:
        self.load()
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
        self.load()
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

    def add_governed_exception(self, exception_rule_id: str, default_rule_id: str) -> bool:
        return self.add_relation(
            exception_rule_id,
            "governed_exception_for",
            default_rule_id,
            weight=1.0,
            properties={"valid_from": time.time(), "valid_to": None},
        )

    def query_relations(self, entity_name: str) -> List[Dict[str, Any]]:
        self.load()
        key = self._node_key(entity_name)
        return [
            edge
            for edge in self._edges
            if self._node_key(edge["source"]) == key or self._node_key(edge["target"]) == key
        ]

    def query_by_type(self, entity_type: str) -> List[Dict[str, Any]]:
        self.load()
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
        governed_exception_edges = [edge for edge in self._edges if edge["relation"] == "governed_exception_for"]
        conflict_edges = [edge for edge in self._edges if edge["relation"] == "conflicts_with"]
        support_edges = [edge for edge in self._edges if edge["relation"] == "supports"]
        return {
            "belief_rules": len(rules),
            "active_beliefs": len(active_rules),
            "challenged_beliefs": len(challenged_rules),
            "support_edges": len(support_edges),
            "conflict_edges": len(conflict_edges),
            "supersedes_edges": len(supersedes_edges),
            "governed_exception_edges": len(governed_exception_edges),
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
            return {
                "rule": None,
                "supports": [],
                "conflicts_with": [],
                "supersedes": [],
                "superseded_by": [],
                "governed_exception_for": [],
                "governed_by_exceptions": [],
            }

        supports = [edge for edge in self._edges if edge["relation"] == "supports" and edge["target"] == rule_id]
        conflicts_with = [
            edge for edge in self._edges
            if edge["relation"] == "conflicts_with" and (edge["source"] == rule_id or edge["target"] == rule_id)
        ]
        supersedes = [edge for edge in self._edges if edge["relation"] == "supersedes" and edge["source"] == rule_id]
        superseded_by = [edge for edge in self._edges if edge["relation"] == "supersedes" and edge["target"] == rule_id]
        governed_exception_for = [
            edge for edge in self._edges if edge["relation"] == "governed_exception_for" and edge["source"] == rule_id
        ]
        governed_by_exceptions = [
            edge for edge in self._edges if edge["relation"] == "governed_exception_for" and edge["target"] == rule_id
        ]

        return {
            "rule": selected,
            "supports": supports,
            "conflicts_with": conflicts_with,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "governed_exception_for": governed_exception_for,
            "governed_by_exceptions": governed_by_exceptions,
        }

    def retrieve_beliefs(
        self,
        query: str,
        top_k: int = 5,
        retrieval_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        query_terms = {term for term in query.lower().split() if term}
        preferred_scope = str((retrieval_state or {}).get("preferred_scope", "")).strip().lower()
        results: List[Dict[str, Any]] = []

        for node in self.query_by_type("belief_rule"):
            props = node.get("properties", {})
            if props.get("state") in {"deprecated", "archived"}:
                continue

            text = str(props.get("text", ""))
            text_terms = set(text.lower().split())
            overlap = len(query_terms & text_terms)
            lexical = 0.6 + min(0.4, overlap / max(len(query_terms), 1)) if query_terms else 0.6

            confidence = float(props.get("confidence", 0.5))
            support_count = int(props.get("support_count", 1))
            contradiction_count = int(props.get("contradiction_count", 0))
            scope = str(props.get("scope", "project")).lower()
            state = str(props.get("state", "proposed")).lower()

            score = lexical * (0.5 + confidence) * (1.0 + min(0.5, support_count * 0.08))
            score *= max(0.45, 1.0 - (contradiction_count * 0.1))

            if state in {"stable", "reinforced"}:
                score += 0.2
            elif state == "challenged":
                score *= 0.55
            if preferred_scope and scope == preferred_scope:
                score += 0.15

            evidence_edges = [
                edge for edge in self._edges
                if edge["relation"] == "supports" and edge["target"] == node["name"]
            ]
            conflict_edges = [
                edge for edge in self._edges
                if edge["relation"] == "conflicts_with" and (edge["source"] == node["name"] or edge["target"] == node["name"])
            ]
            governed_exception_for = [
                edge for edge in self._edges
                if edge["relation"] == "governed_exception_for" and edge["source"] == node["name"]
            ]
            governed_by_exceptions = [
                edge for edge in self._edges
                if edge["relation"] == "governed_exception_for" and edge["target"] == node["name"]
            ]

            results.append(
                {
                    "rule_id": node["name"],
                    "text": text,
                    "scope": scope,
                    "state": state,
                    "confidence": confidence,
                    "support_count": support_count,
                    "contradiction_count": contradiction_count,
                    "score": score,
                    "evidence_count": len(evidence_edges),
                    "conflict_count": len(conflict_edges),
                    "governed_exception_for_count": len(governed_exception_for),
                    "governed_by_exception_count": len(governed_by_exceptions),
                    "governed_exception_for": [edge["target"] for edge in governed_exception_for],
                    "governed_by_exceptions": [edge["source"] for edge in governed_by_exceptions],
                    "fallback_action": (
                        "Treat as non-authoritative. Prefer procedural/exception memory and require explicit approval before destructive actions."
                        if state == "challenged"
                        else ""
                    ),
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]

    def find_path(self, source: str, target: str, max_depth: int = 4) -> List[str]:
        self.load()
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
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._nodes_table}")
        return int(row["total"]) if row else 0

    @property
    def edge_count(self) -> int:
        row = self._store.fetchone(f"SELECT COUNT(*) AS total FROM {self._edges_table}")
        return int(row["total"]) if row else 0

    def save(self):
        node_rows = [
            (
                self._node_key(key),
                node["name"],
                node["type"],
                self._store.dumps_json(node.get("properties", {})),
                float(node.get("created_at", time.time())),
                float(node.get("updated_at", time.time())),
            )
            for key, node in self._nodes.items()
        ]
        edge_rows = [
            (
                self._edge_key(edge["source"], edge["relation"], edge["target"]),
                edge["source"],
                edge["relation"],
                edge["target"],
                float(edge.get("weight", 1.0)),
                self._store.dumps_json(edge.get("properties", {})),
                float(edge.get("created_at", time.time())),
                float(edge.get("updated_at", time.time())),
            )
            for edge in self._edges
        ]
        self._store.execute(f"DELETE FROM {self._nodes_table}")
        self._store.execute(f"DELETE FROM {self._edges_table}")
        if node_rows:
            self._store.executemany(
                f"INSERT OR REPLACE INTO {self._nodes_table} (node_key, name, type, properties_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                node_rows,
            )
        if edge_rows:
            self._store.executemany(
                f"INSERT OR REPLACE INTO {self._edges_table} (edge_key, source, relation, target, weight, properties_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                edge_rows,
            )

    def load(self):
        node_rows = self._store.fetchall(f"SELECT * FROM {self._nodes_table}")
        edge_rows = self._store.fetchall(f"SELECT * FROM {self._edges_table}")
        self._nodes = {
            row["node_key"]: {
                "name": row["name"],
                "type": row["type"],
                "properties": self._store.loads_json(row["properties_json"], {}),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in node_rows
        }
        self._edges = [
            {
                "source": row["source"],
                "relation": row["relation"],
                "target": row["target"],
                "weight": row["weight"],
                "properties": self._store.loads_json(row["properties_json"], {}),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in edge_rows
        ]

    def __repr__(self) -> str:
        return f"KnowledgeGraph(nodes={self.node_count}, edges={self.edge_count})"
