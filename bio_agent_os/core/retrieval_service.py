"""
Hybrid retrieval, safety guards, and lineage view service.
"""

import re
from typing import Dict, List, Optional

from bio_agent_os.core.persona import Persona
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class RetrievalService:
    def __init__(
        self,
        l2: L2SemanticMemory,
        graph: KnowledgeGraph,
        episodes: EpisodeStore,
        persona: Persona,
    ):
        self.l2 = l2
        self.graph = graph
        self.episodes = episodes
        self.persona = persona

    def _tokenize(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^a-z0-9\u00c0-\u024f\s]", " ", text.lower())
        return {token for token in cleaned.split() if token}

    def build_retrieval_state(self, data: Dict[str, object]) -> Dict[str, object]:
        mode = str(data.get("mode", "implement"))
        stress_state = str(data.get("stress_state", "normal"))
        risk_level = str(data.get("risk_level", "medium"))
        preferred_scope = "organization" if risk_level == "high" else "project"
        return {
            "mode": mode,
            "stress_state": stress_state,
            "risk_level": risk_level,
            "task_id": data.get("task_id"),
            "workspace_id": data.get("workspace_id"),
            "project_version": data.get("project_version"),
            "prefer_exception": mode in {"debug", "deploy"} or stress_state == "failure" or risk_level == "high",
            "preferred_scope": preferred_scope,
        }

    def build_safety_guard(
        self,
        query: str,
        l2_results: List[Dict[str, object]],
        graph_results: List[Dict[str, object]],
    ) -> str:
        exception_lines = [
            f"- {item['content']}"
            for item in l2_results
            if item.get("memory_type") == "exception"
        ][:3]
        default_rule_lines = [
            f"- [{item['scope']}] {item['text']} (confidence={item['confidence']:.2f})"
            for item in graph_results
            if item.get("state") in {"stable", "reinforced"} and not item.get("governed_exception_for")
        ][:3]
        approved_override_lines = [
            (
                f"- [{item['scope']}] {item['text']} "
                f"(approved override, confidence={item['confidence']:.2f}, "
                f"requires_approval={'yes' if item.get('requires_human_approval') else 'no'}, "
                f"expires={','.join(item.get('expires_override_at') or ['none'])})"
            )
            for item in graph_results
            if item.get("state") in {"stable", "reinforced"} and item.get("governed_exception_for")
        ][:3]
        challenged_lines = [
            f"- [{item['scope']}] {item['text']} (state=challenged, confidence={item['confidence']:.2f})"
            for item in graph_results
            if item.get("state") == "challenged"
        ][:3]
        if not challenged_lines:
            query_terms = self._tokenize(query)
            fallback_candidates = []
            for rule in self.persona.get_rule_records().values():
                if rule.get("state") != "challenged":
                    continue
                text_terms = self._tokenize(str(rule.get("text", "")))
                if len(query_terms & text_terms) >= 1:
                    fallback_candidates.append(rule)
            challenged_lines = [
                f"- [{item['scope']}] {item['text']} (state=challenged, confidence={item['confidence']:.2f})"
                for item in fallback_candidates[:3]
            ]

        if not exception_lines and not default_rule_lines and not approved_override_lines and not challenged_lines:
            return ""

        lines = ["Safety guardrails for this request:"]
        if exception_lines:
            lines.append("Critical exceptions:")
            lines.extend(exception_lines)
        if default_rule_lines:
            lines.append("Default rules:")
            lines.extend(default_rule_lines)
        if approved_override_lines:
            lines.append("Approved overrides:")
            lines.extend(approved_override_lines)
            lines.append("- Apply these only when the stated approval and audit conditions are satisfied.")
        if challenged_lines:
            lines.append("Challenged beliefs to treat as uncertain:")
            lines.extend(challenged_lines)
            lines.append("Fallback action:")
            lines.append("- Do not enforce challenged beliefs as hard constraints.")
            lines.append("- Prefer procedural memory and explicit exception memory.")
            lines.append("- Require explicit approval before destructive, irreversible, or production-facing actions.")
        return "\n".join(lines)

    def _graph_provenance_score(self, rule_id: str, retrieval_state: Dict[str, object]) -> float:
        belief_bundle = self.graph.belief_query(rule_id=rule_id)
        support_edges = belief_bundle.get("supports", [])
        episode_ids = [edge["source"] for edge in support_edges if edge.get("source")]
        if not episode_ids:
            return 0.0
        score = float(len(episode_ids)) * 0.2
        for episode in self.episodes.get_many(episode_ids):
            if retrieval_state.get("workspace_id") and episode.get("workspace_id") == retrieval_state.get("workspace_id"):
                score += 0.2
            if retrieval_state.get("project_version") and episode.get("project_version") == retrieval_state.get("project_version"):
                score += 0.15
            if retrieval_state.get("task_id") and episode.get("task_id") == retrieval_state.get("task_id"):
                score += 0.1
        return score

    def _l2_provenance_score(self, item: Dict[str, object], retrieval_state: Dict[str, object]) -> float:
        score = 0.0
        if retrieval_state.get("workspace_id") and item.get("workspace_id") == retrieval_state.get("workspace_id"):
            score += 0.2
        if retrieval_state.get("project_version") and item.get("project_version") == retrieval_state.get("project_version"):
            score += 0.15
        if retrieval_state.get("task_id") and item.get("task_id") == retrieval_state.get("task_id"):
            score += 0.1
        return score

    def _is_text_conflict(self, left: str, right: str) -> bool:
        left_lower = left.lower()
        right_lower = right.lower()
        negative_markers = ["never", "do not", "don't", "must not", "avoid", "forbid"]
        positive_markers = ["allow", "always", "must", "should", "prefer"]
        left_negative = any(marker in left_lower for marker in negative_markers)
        right_negative = any(marker in right_lower for marker in negative_markers)
        left_positive = any(marker in left_lower for marker in positive_markers)
        right_positive = any(marker in right_lower for marker in positive_markers)
        shared_tokens = set(left_lower.split()) & set(right_lower.split())
        if len(shared_tokens) < 3:
            return False
        return (left_negative and right_positive) or (left_positive and right_negative)

    def _resolve_graph_l2_conflicts(
        self,
        l2_results: List[Dict[str, object]],
        graph_results: List[Dict[str, object]],
        retrieval_state: Dict[str, object],
    ) -> Dict[str, object]:
        resolved_graph = []
        dropped_graph = []
        for graph_item in graph_results:
            graph_score = float(graph_item["score"]) + self._graph_provenance_score(str(graph_item["rule_id"]), retrieval_state)
            keep_graph = True
            for l2_item in l2_results:
                if not self._is_text_conflict(str(graph_item["text"]), str(l2_item["content"])):
                    continue
                l2_score = float(l2_item["score"]) + self._l2_provenance_score(l2_item, retrieval_state)
                if l2_score > graph_score:
                    keep_graph = False
                    dropped_graph.append(
                        {
                            "rule_id": graph_item["rule_id"],
                            "reason": "l2_provenance_stronger",
                            "graph_score": graph_score,
                            "l2_score": l2_score,
                        }
                    )
                    break
            if keep_graph:
                graph_copy = dict(graph_item)
                graph_copy["provenance_score"] = round(
                    self._graph_provenance_score(str(graph_item["rule_id"]), retrieval_state),
                    3,
                )
                resolved_graph.append(graph_copy)
        return {"graph_results": resolved_graph, "dropped_graph": dropped_graph}

    def hybrid_retrieve(self, query: str, retrieval_state: Dict[str, object], top_k: int = 5) -> Dict[str, object]:
        l2_results = self.l2.search(query, top_k=top_k, retrieval_state=retrieval_state)
        graph_results = self.graph.retrieve_beliefs(query, top_k=top_k, retrieval_state=retrieval_state)
        resolved = self._resolve_graph_l2_conflicts(l2_results, graph_results, retrieval_state)
        graph_results = resolved["graph_results"]
        safety_guard = self.build_safety_guard(query, l2_results, graph_results)
        return {
            "l2_results": l2_results,
            "graph_results": graph_results,
            "graph_conflicts": resolved["dropped_graph"],
            "safety_guard": safety_guard,
        }

    def build_lineage(self, query: str, retrieval_state: Dict[str, object], top_k: int = 5) -> Dict[str, object]:
        bundle = self.hybrid_retrieve(query, retrieval_state, top_k=top_k)
        linked_episode_ids: List[str] = []
        override_chains: List[Dict[str, object]] = []
        for graph_item in bundle["graph_results"]:
            belief_bundle = self.graph.belief_query(rule_id=str(graph_item["rule_id"]))
            linked_episode_ids.extend(
                [edge["source"] for edge in belief_bundle.get("supports", []) if edge.get("source")]
            )
            if belief_bundle.get("governed_exception_for"):
                default_rule_ids = [edge["target"] for edge in belief_bundle.get("governed_exception_for", [])]
                default_rules = []
                for default_rule_id in default_rule_ids:
                    default_bundle = self.graph.belief_query(rule_id=default_rule_id)
                    if default_bundle.get("rule"):
                        default_rules.append(default_bundle["rule"])
                override_chains.append(
                    {
                        "override_rule_id": graph_item["rule_id"],
                        "override_text": graph_item["text"],
                        "default_rules": default_rules,
                        "approved_by_policy": [edge["target"] for edge in belief_bundle.get("approved_by_policy", [])],
                        "requires_human_approval": [edge["target"] for edge in belief_bundle.get("requires_human_approval", [])],
                        "expires_override_at": [edge["target"] for edge in belief_bundle.get("expires_override_at", [])],
                        "evidence_episode_ids": [edge["source"] for edge in belief_bundle.get("supports", []) if edge.get("source")],
                    }
                )
        deduped_episode_ids = sorted(set(linked_episode_ids))
        lineage_episodes = self.episodes.get_many(deduped_episode_ids)
        exception_items = [item for item in bundle["l2_results"] if item.get("memory_type") == "exception"]
        return {
            **bundle,
            "episodes": lineage_episodes,
            "beliefs": bundle["graph_results"],
            "exceptions": exception_items,
            "override_chains": override_chains,
            "persona_snapshot": self.persona.get_layer_records(),
        }
