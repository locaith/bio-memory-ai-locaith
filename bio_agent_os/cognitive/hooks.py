from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .facade import MemoryOS
from .models import EpistemicStatus, MemoryType, Modality, SecurityLabel, TrustTier


SUPPORTED_CLAUDE_HOOKS = {
    "Setup",
    "SessionStart",
    "InstructionsLoaded",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "TaskCreated",
    "TaskCompleted",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    "TeammateIdle",
    "PreCompact",
    "PostCompact",
    "SessionEnd",
    "ConfigChange",
    "InstructionsLoaded",
    "CwdChanged",
    "FileChanged",
    "WorktreeCreate",
    "WorktreeRemove",
    "Notification",
    "PermissionDenied",
    "Elicitation",
    "ElicitationResult",
}


@dataclass(frozen=True)
class HookIngestResult:
    accepted: bool
    event_id: str | None
    hook: str
    reason: str | None = None
    memory_id: str | None = None


class ClaudeCodeHookAdapter:
    """Native-shaped adapter for Claude Code lifecycle capture.

    This module is transport-neutral. A thin plugin can POST the hook payload
    here, while tests can exercise it without Claude or network access.
    """

    def __init__(self, memory_os: MemoryOS, tenant_id: str, workspace_id: str, agent_id: str = "claude-code"):
        self.memory_os = memory_os
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.agent_id = agent_id

    def ingest(self, hook: str, payload: dict[str, Any]) -> HookIngestResult:
        if hook not in SUPPORTED_CLAUDE_HOOKS:
            return HookIngestResult(False, None, hook, "unsupported_hook", None)
        content = self._content(hook, payload)
        event = self.memory_os.observe(
            tenant_id=self.tenant_id,
            actor=self.agent_id,
            source=f"claude-code:{hook}",
            content=content,
            workspace_id=self.workspace_id,
            trust_tier=TrustTier.TRUSTED_SYSTEM,
            security_label=SecurityLabel.INTERNAL,
            metadata={"hook": hook, "session_id": payload.get("session_id"), "tool": payload.get("tool_name")},
            modality=Modality.CODE if "ToolUse" in hook or "Task" in hook else Modality.TEXT,
            epistemic_status=EpistemicStatus.OBSERVED,
        )
        projected = self.memory_os.remember(
            event=event,
            memory_type=MemoryType.EPISODIC,
            content=content,
            confidence=0.72,
            importance=0.55 if hook in {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "TaskCompleted", "StopFailure"} else 0.35,
            salience=0.75 if hook in {"PostToolUseFailure", "StopFailure", "PermissionDenied"} else 0.50,
            utility=0.65,
            metadata={
                "hook": hook,
                "session_id": payload.get("session_id"),
                "tool": payload.get("tool_name"),
                "state": {
                    "mode": "debug" if hook in {"PostToolUseFailure", "StopFailure"} else "implement",
                    "stress_state": "failure" if hook in {"PostToolUseFailure", "StopFailure", "PermissionDenied"} else "normal",
                },
            },
        )
        memory_id = getattr(projected, "memory_id", None)
        return HookIngestResult(True, event.event_id, hook, None, memory_id)

    @staticmethod
    def _content(hook: str, payload: dict[str, Any]) -> str:
        keys = ["prompt", "command_name", "tool_name", "tool_input", "tool_output", "error", "error_details", "summary", "compact_summary", "task_prompt", "last_assistant_message", "reason", "file_path", "delta"]
        parts = [f"hook={hook}"]
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", {}, []):
                parts.append(f"{key}={value}")
        return " | ".join(parts)
