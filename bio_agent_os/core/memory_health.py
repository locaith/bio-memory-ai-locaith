"""
Memory health and dream reports.
"""

from typing import Any, Dict, List

from bio_agent_os.core.persona import Persona
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class MemoryHealthMonitor:
    def __init__(
        self,
        l1: L1WorkingMemory,
        l2: L2SemanticMemory,
        persona: Persona,
        episodes: EpisodeStore,
        graph: KnowledgeGraph,
        exact_memory: ExactMemoryStore | None = None,
    ):
        self.l1 = l1
        self.l2 = l2
        self.persona = persona
        self.episodes = episodes
        self.graph = graph
        self.exact_memory = exact_memory

    def snapshot(self) -> Dict[str, Any]:
        rules = list(self.persona.get_rule_records().values())
        active_rules = [rule for rule in rules if rule["state"] not in {"deprecated", "archived"}]
        stable_rules = [rule for rule in rules if rule["state"] in {"reinforced", "stable"}]
        challenged_rules = [rule for rule in rules if rule["state"] == "challenged"]
        low_confidence_rules = [rule for rule in active_rules if rule["confidence"] < 0.6]
        avg_confidence = (
            sum(rule["confidence"] for rule in active_rules) / len(active_rules)
            if active_rules
            else 0.0
        )
        encoded_l1 = len([entry for entry in self.l1.get_all() if entry.get("status") == "encoded"])
        raw_l1 = len([entry for entry in self.l1.get_all() if entry.get("status") == "raw"])

        return {
            "l1_total": self.l1.count,
            "l1_raw": raw_l1,
            "l1_encoded": encoded_l1,
            "l2_total": self.l2.count,
            "exact_memory_total": self.exact_memory.count if self.exact_memory else 0,
            "episodes_total": self.episodes.count,
            "rules_total": len(rules),
            "rules_active": len(active_rules),
            "rules_stable": len(stable_rules),
            "rules_challenged": len(challenged_rules),
            "rules_low_confidence": len(low_confidence_rules),
            "average_rule_confidence": round(avg_confidence, 3),
            "belief_graph": self.graph.belief_summary(),
            "confidence_dashboard": self.confidence_dashboard(),
            "revalidation": self.revalidation_summary(),
        }

    def confidence_dashboard(self) -> Dict[str, Any]:
        exact_dashboard = self.exact_memory.confidence_dashboard() if self.exact_memory else {
            "total": 0,
            "active": 0,
            "conflicting": 0,
            "average_confidence": 0.0,
            "by_kind": [],
        }
        l2_dashboard = self.l2.confidence_dashboard()
        procedural = next((item for item in l2_dashboard.get("by_type", []) if item.get("memory_type") == "procedural"), None)
        semantic = next((item for item in l2_dashboard.get("by_type", []) if item.get("memory_type") == "semantic"), None)
        return {
            "exact": exact_dashboard,
            "semantic": semantic or {"memory_type": "semantic", "count": 0, "average_confidence": 0.0, "stale_count": 0},
            "procedural": procedural or {"memory_type": "procedural", "count": 0, "average_confidence": 0.0, "stale_count": 0},
            "l2": l2_dashboard,
            "persona": {
                "stable_rules": len([rule for rule in self.persona.get_rule_records().values() if rule["state"] in {"stable", "reinforced"}]),
                "challenged_rules": len([rule for rule in self.persona.get_rule_records().values() if rule["state"] == "challenged"]),
                "average_confidence": round(
                    (
                        sum(rule["confidence"] for rule in self.persona.get_rule_records().values())
                        / max(len(self.persona.get_rule_records()), 1)
                    ) if self.persona.get_rule_records() else 0.0,
                    3,
                ),
            },
        }

    def revalidation_summary(self) -> Dict[str, Any]:
        clusters = self.exact_memory.conflict_clusters(limit=12) if self.exact_memory else []
        return {
            "count": len(clusters),
            "clusters": clusters,
        }

    def status(self) -> Dict[str, Any]:
        snapshot = self.snapshot()
        issues: List[str] = []
        if snapshot["l1_raw"] > 50:
            issues.append("Working memory is accumulating too many raw entries.")
        if snapshot["rules_challenged"] > snapshot["rules_stable"]:
            issues.append("Too many challenged rules relative to stable rules.")
        if snapshot["average_rule_confidence"] < 0.55 and snapshot["rules_total"] > 0:
            issues.append("Rule confidence is trending low.")
        if snapshot["belief_graph"]["support_edges"] < snapshot["rules_active"]:
            issues.append("Some active beliefs do not have enough explicit evidence links.")
        if snapshot.get("revalidation", {}).get("count", 0) > 0:
            issues.append("Some exact facts need revalidation before they should be trusted across channels.")

        health = "healthy"
        if issues:
            health = "warning"
        if len(issues) >= 3:
            health = "critical"

        return {"health": health, "issues": issues, "snapshot": snapshot}

    def dream_report(self, encode_result: Dict[str, Any], logs: List[str]) -> Dict[str, Any]:
        status = self.status()
        return {
            "summary": {
                "encoded": encode_result.get("encoded", 0),
                "failed": encode_result.get("failed", 0),
                "challenged": encode_result.get("challenged", 0),
            },
            "health": status["health"],
            "issues": status["issues"],
            "belief_graph": status["snapshot"]["belief_graph"],
            "recent_logs": logs[-12:],
        }

    def reflect(self) -> Dict[str, Any]:
        snapshot = self.snapshot()
        top_risks: List[str] = []
        if snapshot["rules_low_confidence"] > 0:
            top_risks.append("Some active rules still have low confidence.")
        if snapshot["rules_challenged"] > 0:
            top_risks.append("There are challenged beliefs that may need reconsolidation.")
        if snapshot["l1_raw"] > snapshot["l1_encoded"]:
            top_risks.append("Working memory is still heavier on raw events than encoded memory.")
        if not top_risks:
            top_risks.append("Memory state is currently balanced.")

        return {
            "agent_reflection": (
                "Bio-Agent OS is tracking episodes, semantic memory, procedural memory, "
                "and self-model rules. Stable beliefs should dominate over challenged ones."
            ),
            "snapshot": snapshot,
            "top_risks": top_risks,
        }

    def reflection_prompt(self) -> str:
        snapshot = self.snapshot()
        return (
            "You are the metacognitive reflection layer for Bio-Agent OS.\n"
            "Summarize the current memory state for an engineering user.\n"
            "Be concrete and concise.\n\n"
            f"Snapshot: {snapshot}\n"
        )

    def adaptive_effort(self, importance_score: int = 5, content_length: int = 0) -> str:
        snapshot = self.snapshot()
        pressure_score = 0
        if snapshot["l1_raw"] > 20:
            pressure_score += 1
        if snapshot["rules_challenged"] > 0:
            pressure_score += 1
        if content_length > 4000:
            pressure_score += 1
        if importance_score >= 8:
            pressure_score += 1

        if pressure_score >= 4:
            return "xhigh"
        if pressure_score >= 3:
            return "high"
        if pressure_score >= 2:
            return "medium"
        return "low"
