"""
OpenClaw plugin wrapper for Bio-Agent OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter
from bio_agent_os.core.runtime import BioAgentRuntime, build_runtime


@dataclass
class OpenClawMemoryPlugin:
    runtime: BioAgentRuntime
    adapter: OpenClawBioAdapter

    @staticmethod
    def config_target() -> str:
        return "bio_agent_os.plugins.openclaw:build_openclaw_plugin"

    async def observe(
        self,
        action_type: str,
        observation_output: str,
        task_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        project_version: Optional[str] = None,
        source_refs: Optional[List[str]] = None,
    ) -> bool:
        return await self.adapter.ingest_observation(
            action_type=action_type,
            observation_output=observation_output,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            source_refs=source_refs,
        )

    async def micro_sleep(self) -> Dict[str, object]:
        await self.adapter.trigger_micro_sleep()
        return {"status": "ok", "l1_count": self.runtime.l1.count}

    def build_prompt_context(
        self,
        query: str,
        retrieval_state: Optional[Dict[str, object]] = None,
    ) -> str:
        return self.adapter.inject_contextual_memory_to_openclaw(
            query=query,
            retrieval_state=retrieval_state or {},
        )

    def status(self) -> Dict[str, object]:
        snapshot = self.runtime.health_monitor.snapshot()
        return {
            "plugin": "openclaw",
            "target": self.config_target(),
            "agent_name": self.runtime.agent_name,
            "storage_dir": self.runtime.storage_dir,
            "health": snapshot,
        }


def build_openclaw_plugin(
    agent_name: str = "openclaw-brain",
    storage_dir: str = "data",
) -> OpenClawMemoryPlugin:
    runtime = build_runtime(agent_name=agent_name, storage_dir=storage_dir)
    adapter = OpenClawBioAdapter(
        hippocampus=runtime.hippo,
        garbage_collector=runtime.gc,
        persona=runtime.persona,
        retrieval_service=runtime.retrieval_service,
    )
    return OpenClawMemoryPlugin(runtime=runtime, adapter=adapter)
