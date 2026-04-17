"""
OpenClaw integration adapter.
"""

from typing import Dict, List, Optional

from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.retrieval_service import RetrievalService


class OpenClawBioAdapter:
    """
    Turns Bio-Agent OS into a long-term memory backend for OpenClaw.

    Main responsibilities:
    - ingest tool observations into memory
    - trigger micro-sleep cycles after repeated actions
    - inject stable self-model rules back into the agent prompt
    """

    def __init__(
        self,
        hippocampus: Hippocampus,
        garbage_collector: GarbageCollector,
        persona: Persona,
        retrieval_service: Optional[RetrievalService] = None,
    ):
        self.hippo = hippocampus
        self.gc = garbage_collector
        self.persona = persona
        self.retrieval_service = retrieval_service
        self.action_counter = 0

    async def ingest_observation(self, action_type: str, observation_output: str) -> bool:
        if len(observation_output) > 2000:
            observation_output = (
                observation_output[:1000]
                + "\n...[TRUNCATED TO PREVENT BLOAT]...\n"
                + observation_output[-1000:]
            )

        raw_data = (
            f"OpenClaw Action [{action_type}]:\n"
            f"Observation:\n{observation_output}"
        )

        await self.hippo.label_and_store(raw_data, source="OpenClaw-Worker")
        self.action_counter += 1

        if self.action_counter >= 10:
            await self.trigger_micro_sleep()

        return True

    async def trigger_micro_sleep(self):
        print("[OpenClaw Adapter] Triggering micro-sleep cycle...")
        self.gc.run()
        await self.hippo.consolidate()
        self.action_counter = 0

    def build_safety_guard(
        self,
        exceptions: Optional[List[Dict[str, object]]] = None,
        beliefs: Optional[List[Dict[str, object]]] = None,
    ) -> str:
        exception_lines = [
            f"- {item['content']}"
            for item in (exceptions or [])
            if item.get("memory_type") == "exception"
        ][:3]
        belief_lines = [
            f"- [{item['scope']}] {item['text']} (confidence={item['confidence']:.2f})"
            for item in (beliefs or [])
        ][:3]

        if not exception_lines and not belief_lines:
            return ""

        lines = ["OpenClaw Safety Guard:"]
        if exception_lines:
            lines.append("Exceptions:")
            lines.extend(exception_lines)
        if belief_lines:
            lines.append("Belief constraints:")
            lines.extend(belief_lines)
        return "\n".join(lines)

    def inject_persona_to_openclaw(
        self,
        exceptions: Optional[List[Dict[str, object]]] = None,
        beliefs: Optional[List[Dict[str, object]]] = None,
    ) -> str:
        persona_prompt = self.persona.get_identity_prompt()
        safety_guard = self.build_safety_guard(exceptions=exceptions, beliefs=beliefs)
        if not safety_guard:
            return persona_prompt
        return f"{persona_prompt}\n\n{safety_guard}\n\nIf the safety guard conflicts with a plan, follow the safety guard."

    def inject_contextual_memory_to_openclaw(
        self,
        query: str,
        retrieval_state: Optional[Dict[str, object]] = None,
    ) -> str:
        if not self.retrieval_service:
            return self.inject_persona_to_openclaw()
        retrieval_state = retrieval_state or {}
        bundle = self.retrieval_service.hybrid_retrieve(query, retrieval_state, top_k=5)
        return self.inject_persona_to_openclaw(
            exceptions=bundle["l2_results"],
            beliefs=bundle["graph_results"],
        )
