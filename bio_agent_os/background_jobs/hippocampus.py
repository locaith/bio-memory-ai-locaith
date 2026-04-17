"""
Sleep consolidation and memory compilation.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.persona import Persona
from bio_agent_os.memory.episodes import EpisodeStore
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
    ):
        self.engine = engine
        self.l1 = l1
        self.persona = persona
        self.l2 = l2
        self.episodes = episodes
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

    async def label_and_store(self, raw_data: str, source: str = "unknown") -> Dict[str, Any]:
        metadata = await self.label(raw_data, source)
        entry = self.l1.add(content=raw_data, source=source, metadata=metadata)

        if self.episodes:
            episode = self.episodes.add(
                raw_payload=raw_data,
                actor=source,
                source=source,
                observation_type="event",
                inferred_intent=metadata.get("topic"),
                topic=metadata.get("topic"),
                outcome="captured",
                confidence=max(0.1, min(metadata.get("importance_score", 5) / 10.0, 0.95)),
                tags=[metadata.get("topic", "general")],
                metadata=metadata,
            )
            entry["episode_id"] = episode["episode_id"]
        return entry

    async def _compile_entry(self, content: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        prompt = (
            "You are a memory compiler for a coding agent.\n"
            "Transform one event into four outputs:\n"
            "1. episodic summary\n"
            "2. semantic memory\n"
            "3. procedural memory\n"
            "4. identity rule candidate\n"
            "Keep it compact, reusable, and avoid hype.\n"
            f"Event:\n{content}\n\n"
            f"Metadata:\n{metadata}"
        )
        return await self.engine.generate_structured(
            prompt,
            schema=CompiledMemory,
            temperature=0.2,
        )

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

                if self.l2:
                    topic = metadata.get("topic", "general")
                    self.l2.store(
                        content=compiled["semantic_memory"],
                        importance=metadata.get("importance_score", 5),
                        tags=[topic, "semantic"],
                        source_rule_id=rule_id,
                    )
                    self.l2.store(
                        content=compiled["procedural_memory"],
                        importance=metadata.get("importance_score", 5),
                        tags=[topic, "procedural"],
                        source_rule_id=rule_id,
                    )
                    self.l2.store(
                        content=compiled["episodic_summary"],
                        importance=max(1.0, metadata.get("importance_score", 5) - 1),
                        tags=[topic, "episodic"],
                        source_rule_id=rule_id,
                    )

                self.l1.mark_encoded(entry["timestamp"])
                self._log.append(f"Compiled rule: {identity_rule[:120]}")
                stats["encoded"] += 1
            except Exception as exc:
                self._log.append(f"Compile failed: {exc}")
                stats["failed"] += 1

        self._log.append("----- sleep consolidation finished -----")
        return stats

    async def dream(self) -> Dict[str, int]:
        self._log.append("----- dream cycle started -----")
        result = await self.consolidate()
        self._log.append("----- dream cycle finished -----")
        return result

    def __repr__(self) -> str:
        return f"Hippocampus(l1={self.l1.count} entries, persona={self.persona.rule_count} rules)"
