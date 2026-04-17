"""
OpenClaw integration adapter.
"""

from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.core.persona import Persona


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
    ):
        self.hippo = hippocampus
        self.gc = garbage_collector
        self.persona = persona
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

    def inject_persona_to_openclaw(self) -> str:
        return self.persona.get_identity_prompt()
