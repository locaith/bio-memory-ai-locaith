"""
Sleep consolidation and memory compilation.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bio_agent_os.core.audit_log import AuditLog
from bio_agent_os.core.compaction import MemoryCompactor
from bio_agent_os.core.dream_journal import DreamJournal
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.memory_health import MemoryHealthMonitor
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.reconciliation import ContradictionResolver
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


class MemoryLabel(BaseModel):
    topic: str = Field(description="Main topic")
    importance_score: int = Field(description="Importance from 1 to 10")
    is_junk_or_transient: bool = Field(description="Whether this should be forgotten quickly")
    user_state: str = Field(description="Observed user or agent state")


class CompiledMemory(BaseModel):
    episodic_summary: str = Field(description="Short factual memory of what happened")
    semantic_memory: str = Field(description="Generalized knowledge extracted from the event")
    procedural_memory: str = Field(description="Reusable procedure or workflow guidance")
    exception_memory: str = Field(description="Important exception, caveat, or dangerous special case")
    identity_rule: str = Field(description="Stable rule candidate for the self-model")
    confidence: float = Field(description="Confidence score from 0 to 1")
    scope: str = Field(description="core, project, agent, user, session, organization")


class Hippocampus:
    def __init__(
        self,
        engine: LLMEngine,
        l1: L1WorkingMemory,
        persona: Persona,
        l2: Optional[L2SemanticMemory] = None,
        episodes: Optional[EpisodeStore] = None,
        graph: Optional[KnowledgeGraph] = None,
        dream_journal: Optional[DreamJournal] = None,
        audit_log: Optional[AuditLog] = None,
    ):
        self.engine = engine
        self.l1 = l1
        self.persona = persona
        self.l2 = l2
        self.episodes = episodes
        self.graph = graph
        self.dream_journal = dream_journal
        self.audit_log = audit_log
        self.compactor = MemoryCompactor()
        self.reconciler = ContradictionResolver(persona=persona)
        self._log: List[str] = []

    @property
    def logs(self) -> List[str]:
        return list(self._log)

    def clear_logs(self):
        self._log.clear()

    async def label(self, raw_data: str, source: str = "unknown") -> Dict[str, Any]:
        self._log.append(f"Hippocampus labeling input from {source}.")

        prompt = (
            "You are the hippocampus for an AI agent.\n"
            "Label the following raw data with topic, importance, whether it is junk, "
            f"and observed state.\nData:\n{raw_data[:1200]}"
        )
        try:
            metadata = await self.engine.generate_structured(
                prompt,
                schema=MemoryLabel,
                temperature=0.1,
            )
            self._log.append(
                "Labeled memory "
                f"(importance={metadata['importance_score']}, junk={metadata['is_junk_or_transient']})."
            )
            return metadata
        except Exception as exc:
            self._log.append(f"Label failed: {exc}")
            return {
                "topic": "unknown",
                "importance_score": 5,
                "is_junk_or_transient": False,
                "user_state": "unknown",
            }

    async def label_and_store(
        self,
        raw_data: str,
        source: str = "unknown",
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        source_refs: Optional[List[Dict[str, Any]]] = None,
        observation_type: str = "event",
    ) -> Dict[str, Any]:
        compaction = self.compactor.compact(raw_data)
        metadata = await self.label(compaction["content"], source)
        metadata.update(
            {
                "was_compacted": compaction["was_compacted"],
                "original_length": compaction["original_length"],
                "compacted_length": compaction["compacted_length"],
                "task_id": task_id,
                "workspace_id": workspace_id,
                "project_version": project_version,
            }
        )
        episode = None

        if self.episodes:
            episode = self.episodes.add(
                raw_payload=raw_data,
                actor=source,
                source=source,
                observation_type=observation_type,
                inferred_intent=metadata.get("topic"),
                topic=metadata.get("topic"),
                outcome="captured",
                confidence=max(0.1, min(metadata.get("importance_score", 5) / 10.0, 0.95)),
                tags=[metadata.get("topic", "general")],
                metadata=metadata,
                task_id=task_id,
                workspace_id=workspace_id,
                project_version=project_version,
                source_refs=source_refs,
            )
        entry = self.l1.add(
            content=compaction["content"],
            source=source,
            metadata=metadata,
            episode_id=episode["episode_id"] if episode else None,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
        )
        if self.audit_log:
            self.audit_log.append(
                "memory_ingest",
                f"Ingested memory from {source}",
                {
                    "source": source,
                    "topic": metadata.get("topic"),
                    "was_compacted": compaction["was_compacted"],
                    "original_length": compaction["original_length"],
                    "compacted_length": compaction["compacted_length"],
                    "task_id": task_id,
                    "workspace_id": workspace_id,
                    "project_version": project_version,
                },
            )
        return entry

    async def _compile_entry(self, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        effort = self._adaptive_effort(metadata, content)
        prompt = (
            "You are a memory compiler for a coding agent.\n"
            "Transform one event into four outputs:\n"
            "1. episodic summary\n"
            "2. semantic memory\n"
            "3. procedural memory\n"
            "4. exception memory\n"
            "5. identity rule candidate\n"
            "Keep it compact, reusable, and avoid hype.\n"
            f"Event:\n{content}\n\n"
            f"Metadata:\n{metadata}"
        )
        return await self.engine.generate_structured(
            prompt,
            schema=CompiledMemory,
            temperature=0.2,
            effort=effort,
        )

    def _adaptive_effort(self, metadata: Dict[str, Any], content: str) -> str:
        if self.graph and self.episodes and self.l2:
            monitor = MemoryHealthMonitor(
                l1=self.l1,
                l2=self.l2,
                persona=self.persona,
                episodes=self.episodes,
                graph=self.graph,
            )
            effort = monitor.adaptive_effort(
                importance_score=int(metadata.get("importance_score", 5)),
                content_length=len(content),
            )
            self._log.append(f"Adaptive effort selected: {effort}")
            return effort
        return "medium"

    async def consolidate(self) -> Dict[str, int]:
        self._log.append("----- sleep consolidation started -----")
        survivors = self.l1.get_survivors()
        stats = {"encoded": 0, "failed": 0, "challenged": 0}

        if not survivors:
            self._log.append("No survivors to consolidate.")
            self._log.append("----- sleep consolidation finished -----")
            return stats

        self._log.append(f"Compiling {len(survivors)} survivor memories.")

        for entry in survivors:
            content = entry["content"]
            metadata = entry.get("metadata", {})
            episode_id = entry.get("episode_id")
            try:
                compiled = await self._compile_entry(content, metadata)
                identity_rule = compiled["identity_rule"].strip()
                confidence = float(compiled.get("confidence", 0.55))
                scope = compiled.get("scope", "project").strip().lower() or "project"

                rule_id = self.persona.add_rule(
                    identity_rule,
                    scope=scope,
                    confidence=confidence,
                    evidence_episode_ids=[episode_id] if episode_id else [],
                )
                reconcile_stats = self.reconciler.reconcile(rule_id)
                rule_record = self.persona.get_rule_records()[rule_id]

                if self.graph:
                    self.graph.add_belief_rule(rule_record)
                    if episode_id:
                        self.graph.add_episode_evidence(
                            rule_id,
                            episode_id,
                            confidence=confidence,
                        )
                    for deprecated_id in reconcile_stats["deprecated_ids"]:
                        self.graph.add_conflict(rule_id, deprecated_id)
                        self.graph.add_supersedes(rule_id, deprecated_id)
                    for challenged_id in reconcile_stats["challenged_ids"]:
                        self.graph.add_conflict(challenged_id, rule_id)

                if self.l2:
                    topic = metadata.get("topic", "general")
                    mode_hints = self._mode_hints(metadata, content)
                    shared_kwargs = {
                        "importance": metadata.get("importance_score", 5),
                        "source_rule_id": rule_id,
                        "scope": scope,
                        "mode_hints": mode_hints,
                        "risk_level": self._risk_level(metadata),
                        "stress_state": self._stress_state(metadata, content),
                        "task_id": entry.get("task_id"),
                        "workspace_id": entry.get("workspace_id"),
                        "project_version": entry.get("project_version"),
                    }
                    self.l2.store(
                        content=compiled["semantic_memory"],
                        tags=[topic, "semantic"],
                        memory_type="semantic",
                        **shared_kwargs,
                    )
                    self.l2.store(
                        content=compiled["procedural_memory"],
                        tags=[topic, "procedural"],
                        memory_type="procedural",
                        **shared_kwargs,
                    )
                    self.l2.store(
                        content=compiled["episodic_summary"],
                        importance=max(1.0, metadata.get("importance_score", 5) - 1),
                        tags=[topic, "episodic"],
                        source_rule_id=rule_id,
                        memory_type="episodic",
                        scope=scope,
                        mode_hints=mode_hints,
                        risk_level=self._risk_level(metadata),
                        stress_state=self._stress_state(metadata, content),
                        task_id=entry.get("task_id"),
                        workspace_id=entry.get("workspace_id"),
                        project_version=entry.get("project_version"),
                    )
                    exception_memory = compiled.get("exception_memory", "").strip()
                    if exception_memory:
                        self.l2.store_exception(
                            content=exception_memory,
                            exception_for=topic,
                            tags=[topic],
                            source_rule_id=rule_id,
                            scope=scope,
                            mode_hints=mode_hints,
                            risk_level=self._risk_level(metadata),
                            stress_state=self._stress_state(metadata, content),
                            task_id=entry.get("task_id"),
                            workspace_id=entry.get("workspace_id"),
                            project_version=entry.get("project_version"),
                        )

                self.l1.mark_encoded(entry["timestamp"])
                self._log.append(f"Compiled rule: {identity_rule[:120]}")
                if self.audit_log:
                    self.audit_log.append(
                        "memory_consolidate",
                        "Consolidated survivor into long-term memory",
                        {
                            "rule_id": rule_id,
                            "scope": scope,
                            "confidence": confidence,
                            "episode_id": episode_id,
                        },
                    )
                if reconcile_stats["deprecated"] or reconcile_stats["challenged"]:
                    self._log.append(
                        "Reconciled rule "
                        f"(deprecated={reconcile_stats['deprecated']}, "
                        f"challenged={reconcile_stats['challenged']})."
                    )
                stats["encoded"] += 1
                stats["challenged"] += reconcile_stats["challenged"]
            except Exception as exc:
                self._log.append(f"Compile failed: {exc}")
                stats["failed"] += 1

        self._log.append("----- sleep consolidation finished -----")
        return stats

    def _mode_hints(self, metadata: Dict[str, Any], content: str) -> List[str]:
        lowered = content.lower()
        hints = set()
        topic = str(metadata.get("topic", "")).lower()
        if any(token in lowered for token in ["error", "failed", "panic", "traceback", "exception"]):
            hints.add("debug")
        if any(token in lowered for token in ["implement", "write", "create", "build feature"]):
            hints.add("implement")
        if any(token in lowered for token in ["refactor", "cleanup", "rename", "extract"]):
            hints.add("refactor")
        if any(token in lowered for token in ["deploy", "release", "production", "rollback", "migration"]):
            hints.add("deploy")
        if topic in {"git", "dependency", "build"}:
            hints.update({"debug", "deploy"})
        return sorted(hints or {"implement"})

    def _risk_level(self, metadata: Dict[str, Any]) -> str:
        importance = int(metadata.get("importance_score", 5))
        if importance >= 8:
            return "high"
        if importance >= 5:
            return "medium"
        return "low"

    def _stress_state(self, metadata: Dict[str, Any], content: str) -> str:
        lowered = content.lower()
        if any(token in lowered for token in ["failed", "panic", "critical", "error", "exception"]):
            return "failure"
        return metadata.get("user_state", "normal") or "normal"

    async def dream(self) -> Dict[str, int]:
        self._log.append("----- dream cycle started -----")
        result = await self.consolidate()
        if self.graph and self.episodes and self.l2:
            monitor = MemoryHealthMonitor(
                l1=self.l1,
                l2=self.l2,
                persona=self.persona,
                episodes=self.episodes,
                graph=self.graph,
            )
            report = monitor.dream_report(result, self.logs)
            if self.dream_journal:
                report = self.dream_journal.append(report)
            if self.audit_log:
                self.audit_log.append(
                    "dream_cycle",
                    "Completed dream cycle",
                    {"report_id": report.get("report_id"), "summary": report.get("summary", {})},
                )
            result["report"] = report
        self._log.append("----- dream cycle finished -----")
        return result

    def __repr__(self) -> str:
        return f"Hippocampus(l1={self.l1.count} entries, persona={self.persona.rule_count} rules)"
