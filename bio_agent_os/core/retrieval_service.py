"""
Hybrid retrieval, safety guards, and lineage view service.
"""

import re
from typing import Any, Dict, List, Optional

from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.persona import Persona
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class RetrievalService:
    ANCHOR_QUERY_MARKERS = {
        "mật mã",
        "mat ma",
        "ký tự đặc biệt",
        "ky tu dac biet",
        "ký tự mật mã",
        "secret code",
        "code word",
        "passphrase",
        "password",
        "special character",
        "what did we choose",
        "bạn vừa chọn",
        "ban vua chon",
    }

    def __init__(
        self,
        l2: L2SemanticMemory,
        graph: KnowledgeGraph,
        episodes: EpisodeStore,
        persona: Persona,
        approval_queue: Optional[ApprovalQueue] = None,
        exact_memory: Optional[ExactMemoryStore] = None,
    ):
        self.l2 = l2
        self.graph = graph
        self.episodes = episodes
        self.exact_memory = exact_memory
        self.persona = persona
        self.approval_queue = approval_queue

    def _tokenize(self, text: str) -> set[str]:
        cleaned = re.sub(r"[^a-z0-9\u00c0-\u024f\s]", " ", text.lower())
        return {token for token in cleaned.split() if token}

    def _is_anchor_query(self, query: str) -> bool:
        lowered = query.lower()
        return any(marker in lowered for marker in self.ANCHOR_QUERY_MARKERS)

    def _scope_bonus(self, scope: str, retrieval_state: Dict[str, object]) -> float:
        normalized_scope = (scope or "").strip().lower()
        preferred_scope = str(retrieval_state.get("preferred_scope", "project")).lower()
        risk_level = str(retrieval_state.get("risk_level", "medium")).lower()
        if normalized_scope == preferred_scope:
            return 0.35
        if normalized_scope == "core":
            return 0.30
        if normalized_scope == "project" and preferred_scope in {"project", "organization"}:
            return 0.20
        if normalized_scope == "organization" and risk_level == "high":
            return 0.20
        if normalized_scope == "adaptive":
            return 0.05
        return 0.0

    def _rank_text_candidate(
        self,
        query_terms: set[str],
        text: str,
        scope: str,
        base_confidence: float,
        support_count: int,
        retrieval_state: Dict[str, object],
    ) -> float:
        text_terms = self._tokenize(text)
        overlap = len(query_terms & text_terms)
        return (
            float(overlap) * 0.45
            + float(base_confidence)
            + min(float(support_count) * 0.05, 0.25)
            + self._scope_bonus(scope, retrieval_state)
        )

    def relevant_stable_persona_rules(
        self,
        query: str,
        retrieval_state: Dict[str, object],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        query_terms = self._tokenize(query)
        candidates: List[Dict[str, Any]] = []
        for rule_id, rule in self.persona.get_rule_records().items():
            if rule.get("state") not in {"stable", "reinforced"}:
                continue
            text = str(rule.get("text", "")).strip()
            if not text:
                continue
            overlap_terms = query_terms & self._tokenize(text)
            score = self._rank_text_candidate(
                query_terms,
                text,
                str(rule.get("scope", "project")),
                float(rule.get("confidence", 0.0)),
                int(rule.get("support_count", 0)),
                retrieval_state,
            )
            if query_terms and not overlap_terms and str(rule.get("scope", "project")) != "core":
                continue
            candidates.append(
                {
                    "rule_id": rule_id,
                    "text": text,
                    "layer": rule.get("layer", "adaptive"),
                    "scope": rule.get("scope", "project"),
                    "state": rule.get("state", "stable"),
                    "confidence": float(rule.get("confidence", 0.0)),
                    "support_count": int(rule.get("support_count", 0)),
                    "score": round(score, 3),
                }
            )
        candidates.sort(key=lambda item: (-float(item["score"]), -float(item["confidence"]), -int(item["support_count"])))
        return candidates[:limit]

    def relevant_pending_approvals(
        self,
        query: str,
        retrieval_state: Dict[str, object],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        if not self.approval_queue:
            return []
        query_terms = self._tokenize(query)
        candidates: List[Dict[str, Any]] = []
        for request in self.approval_queue.list(status="pending", limit=200):
            rule_text = str(request.get("rule_text", "")).strip()
            if not rule_text:
                continue
            overlap_terms = query_terms & self._tokenize(rule_text)
            score = self._rank_text_candidate(
                query_terms,
                rule_text,
                str(request.get("scope", "project")),
                float(request.get("confidence", 0.0)),
                1,
                retrieval_state,
            )
            if query_terms and not overlap_terms:
                continue
            candidates.append(
                {
                    "request_id": request.get("request_id"),
                    "action_type": request.get("action_type"),
                    "rule_text": rule_text,
                    "scope": request.get("scope", "project"),
                    "confidence": float(request.get("confidence", 0.0)),
                    "created_at": request.get("created_at"),
                    "metadata": request.get("metadata", {}),
                    "score": round(score, 3),
                }
            )
        candidates.sort(key=lambda item: (-float(item["score"]), -float(item["confidence"]), -float(item["created_at"] or 0.0)))
        return candidates[:limit]

    def relevant_anchor_episodes(
        self,
        query: str,
        retrieval_state: Dict[str, object],
        limit: int = 4,
    ) -> List[Dict[str, Any]]:
        if not self._is_anchor_query(query):
            return []
        matches = self.episodes.search_text(
            query,
            limit=limit,
            task_id=retrieval_state.get("task_id"),
            workspace_id=retrieval_state.get("workspace_id"),
            project_version=retrieval_state.get("project_version"),
        )
        results = []
        for record in matches:
            results.append(
                {
                    "episode_id": record.get("episode_id"),
                    "timestamp": record.get("timestamp"),
                    "source": record.get("source"),
                    "actor": record.get("actor"),
                    "observation_type": record.get("observation_type"),
                    "raw_payload": record.get("raw_payload"),
                    "score": record.get("score", 0.0),
                    "source_refs": record.get("source_refs", []),
                    "metadata": record.get("metadata", {}),
                }
            )
        return results

    def relevant_exact_facts(
        self,
        query: str,
        retrieval_state: Dict[str, object],
        limit: int = 3,
    ) -> Dict[str, Any]:
        if not self.exact_memory:
            return {"kind": None, "status": "none", "facts": [], "answer_candidate": None}
        return self.exact_memory.recall(
            query=query,
            workspace_id=retrieval_state.get("workspace_id"),
            project_version=retrieval_state.get("project_version"),
            task_id=retrieval_state.get("task_id"),
            limit=limit,
        )

    def revalidation_packet(self, query: str, retrieval_state: Dict[str, object]) -> Dict[str, Any]:
        if not self.exact_memory:
            return {"status": "none", "question": None, "candidates": [], "kind": None}
        return self.exact_memory.build_revalidation_packet(
            query=query,
            workspace_id=retrieval_state.get("workspace_id"),
            project_version=retrieval_state.get("project_version"),
            task_id=retrieval_state.get("task_id"),
        )

    def should_direct_answer_exact(self, query: str, exact_facts: Dict[str, Any]) -> bool:
        if not exact_facts or not exact_facts.get("kind"):
            return False
        lowered = query.strip().lower()
        starters = ("what ", "which ", "whats ", "what's ", "ky ", "ký ", "mat ", "mật ", "pass", "secret", "code", "password")
        return lowered.startswith(starters) or lowered.endswith(("la gi?", "là gì?", "is what?", "is it?"))

    def direct_exact_response(self, query: str, exact_facts: Dict[str, Any], revalidation: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not self.should_direct_answer_exact(query, exact_facts):
            return None
        kind = str(exact_facts.get("kind") or "exact_fact").replace("_", " ")
        is_vietnamese = any(token in query.lower() for token in ["ký", "ky ", "mật", "mat ", "là gì", "la gi", "chúng ta", "chon", "chọn"])
        if exact_facts.get("status") == "resolved" and exact_facts.get("answer_candidate"):
            if is_vietnamese:
                return f"{kind.capitalize()} là {exact_facts['answer_candidate']}."
            return f"The {kind} is {exact_facts['answer_candidate']}."
        if exact_facts.get("status") == "conflicting":
            packet = revalidation or {"question": f"I have conflicting exact memories for {kind}. Please confirm the canonical value."}
            if is_vietnamese:
                candidates = [item.get("fact_value") for item in packet.get("candidates", []) if item.get("fact_value")]
                if candidates:
                    return f"Tôi đang có xung đột ở trí nhớ chính xác cho {kind}: {'; '.join(candidates)}. Bạn hãy xác nhận giá trị nào là canonical."
            return str(packet.get("question"))
        return None

    def build_retrieval_state(self, data: Dict[str, object]) -> Dict[str, object]:
        mode = str(data.get("mode", "implement"))
        stress_state = str(data.get("stress_state", "normal"))
        risk_level = str(data.get("risk_level", "medium"))
        preferred_scope = "organization" if risk_level == "high" else "project"
        return {
            "mode": mode,
            "stress_state": stress_state,
            "risk_level": risk_level,
            "query": data.get("query", ""),
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
        def _expiry_label(item: Dict[str, object]) -> str:
            windows = item.get("expires_override_at") or []
            if not windows:
                return "none"
            labels = []
            for window in windows:
                if isinstance(window, dict):
                    start = window.get("valid_from")
                    end = window.get("valid_to")
                    if end:
                        labels.append(f"{start:.0f}->{end:.0f}")
                    elif start:
                        labels.append(f"{start:.0f}->open")
                else:
                    labels.append(str(window))
            return ",".join(labels) or "none"

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
                f"expires={_expiry_label(item)})"
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
        exact_facts = self.relevant_exact_facts(query, retrieval_state, limit=min(top_k, 3))
        revalidation = self.revalidation_packet(query, retrieval_state)
        exact_directive = self.direct_exact_response(query, exact_facts, revalidation)
        l2_results = self.l2.search(query, top_k=top_k, retrieval_state=retrieval_state)
        graph_results = self.graph.retrieve_beliefs(query, top_k=top_k, retrieval_state=retrieval_state)
        stable_persona_rules = self.relevant_stable_persona_rules(query, retrieval_state, limit=min(top_k, 5))
        pending_approvals = self.relevant_pending_approvals(query, retrieval_state, limit=min(top_k, 5))
        anchor_episodes = self.relevant_anchor_episodes(query, retrieval_state, limit=min(top_k, 4))
        resolved = self._resolve_graph_l2_conflicts(l2_results, graph_results, retrieval_state)
        graph_results = resolved["graph_results"]
        safety_guard = self.build_safety_guard(query, l2_results, graph_results)
        if exact_facts.get("status") == "conflicting":
            warning = (
                "Exact memory conflict detected for this request. "
                "Do not guess. Ask for confirmation or cite the most recent supporting episode explicitly."
            )
            safety_guard = f"{safety_guard}\n\n{warning}".strip() if safety_guard else warning
        return {
            "exact_facts": exact_facts,
            "exact_directive": exact_directive,
            "revalidation": revalidation,
            "anchor_episodes": anchor_episodes,
            "l2_results": l2_results,
            "graph_results": graph_results,
            "graph_conflicts": resolved["dropped_graph"],
            "stable_persona_rules": stable_persona_rules,
            "pending_approvals": pending_approvals,
            "safety_guard": safety_guard,
        }

    def build_lineage(self, query: str, retrieval_state: Dict[str, object], top_k: int = 5) -> Dict[str, object]:
        bundle = self.hybrid_retrieve(query, retrieval_state, top_k=top_k)
        linked_episode_ids: List[str] = []
        override_chains: List[Dict[str, object]] = []
        exact_fact_episodes: List[str] = []
        exact_facts = bundle.get("exact_facts", {})
        for fact in exact_facts.get("facts", []):
            exact_fact_episodes.extend(fact.get("evidence_episode_ids", []))
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
                        "approved_by_policy": [
                            {
                                "policy_node_id": edge["target"],
                                "policy_text": self.graph.get_entity(edge["target"]).get("properties", {}).get("text", edge["target"])
                                if self.graph.get_entity(edge["target"])
                                else edge["target"],
                            }
                            for edge in belief_bundle.get("approved_by_policy", [])
                        ],
                        "requires_human_approval": [edge["target"] for edge in belief_bundle.get("requires_human_approval", [])],
                        "expires_override_at": [
                            {
                                "valid_from": edge.get("properties", {}).get("valid_from"),
                                "valid_to": edge.get("properties", {}).get("valid_to"),
                            }
                            for edge in belief_bundle.get("expires_override_at", [])
                        ],
                        "evidence_episode_ids": [edge["source"] for edge in belief_bundle.get("supports", []) if edge.get("source")],
                    }
                )
        deduped_episode_ids = sorted(set(linked_episode_ids + exact_fact_episodes))
        lineage_episodes = self.episodes.get_many(deduped_episode_ids)
        for anchor in bundle.get("anchor_episodes", []):
            if not any(item.get("episode_id") == anchor.get("episode_id") for item in lineage_episodes):
                lineage_episodes.append(anchor)
        exception_items = [item for item in bundle["l2_results"] if item.get("memory_type") == "exception"]
        return {
            **bundle,
            "episodes": lineage_episodes,
            "beliefs": bundle["graph_results"],
            "exceptions": exception_items,
            "override_chains": override_chains,
            "persona_snapshot": self.persona.get_layer_records(),
        }
