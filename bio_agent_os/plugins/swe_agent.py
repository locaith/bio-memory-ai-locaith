"""
SWE-Agent plugin wrapper for Bio-Agent OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter
from bio_agent_os.core.runtime import BioAgentRuntime, build_runtime


@dataclass
class SWEAgentMemoryPlugin:
    runtime: BioAgentRuntime
    adapter: OpenClawBioAdapter

    @staticmethod
    def config_target() -> str:
        return "bio_agent_os.plugins.swe_agent:build_swe_agent_plugin"

    async def observe_step(
        self,
        step_type: str,
        observation_output: str,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        source_refs: Optional[List[str]] = None,
    ) -> bool:
        return await self.adapter.ingest_observation(
            action_type=step_type,
            observation_output=observation_output,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            source_refs=source_refs,
        )

    def build_prompt_context(
        self,
        query: str,
        retrieval_state: Optional[Dict[str, object]] = None,
    ) -> str:
        return self.adapter.inject_contextual_memory_to_openclaw(
            query=query,
            retrieval_state=retrieval_state or {},
        )

    async def dream(self) -> Dict[str, object]:
        return await self.runtime.hippo.dream()

    def status(self) -> Dict[str, object]:
        snapshot = self.runtime.health_monitor.snapshot()
        return {
            "plugin": "swe-agent",
            "target": self.config_target(),
            "agent_name": self.runtime.agent_name,
            "storage_dir": self.runtime.storage_dir,
            "health": snapshot,
        }


def build_swe_agent_plugin(
    agent_name: str = "swe-agent-brain",
    storage_dir: str = "data",
) -> SWEAgentMemoryPlugin:
    runtime = build_runtime(agent_name=agent_name, storage_dir=storage_dir)
    adapter = OpenClawBioAdapter(
        hippocampus=runtime.hippo,
        garbage_collector=runtime.gc,
        persona=runtime.persona,
        retrieval_service=runtime.retrieval_service,
    )
    return SWEAgentMemoryPlugin(runtime=runtime, adapter=adapter)
