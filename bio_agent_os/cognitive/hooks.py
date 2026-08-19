from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .facade import MemoryOS
from .projection_intent import MemoryProjectionIntent
from .shadow import ProjectionMode
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

    # SINGLE WRITER BY MODE — hợp đồng sau sự cố 18/08 (1 prompt thật thành
    # 2 memories dưới outbox):
    #
    #     LEGACY   observe -> event;            remember -> memory
    #     SHADOW   observe -> event + shadow;   remember -> memory legacy
    #     OUTBOX   observe -> event + debt;     worker LÀ writer DUY NHẤT
    #
    # Hai quyết định tách thành method để mutant tháo được đúng từng cái.

    def _direct_write_allowed(self) -> bool:
        """OUTBOX có đúng MỘT materialization authority: worker."""
        return self.memory_os.projection_mode is not ProjectionMode.OUTBOX

    def _projection_debt_allowed(self, substantive: bool) -> bool:
        """Sự kiện không đáng thành memory thì không được nợ projection —
        thiếu chốt này, sửa duplicate sẽ phá filtering contract cũ và
        `hook=SessionStart` bắt đầu được materialize."""
        return substantive or (
            self.memory_os.projection_mode is not ProjectionMode.OUTBOX)

    def ingest(self, hook: str, payload: dict[str, Any]) -> HookIngestResult:
        if hook not in SUPPORTED_CLAUDE_HOOKS:
            return HookIngestResult(False, None, hook, "unsupported_hook", None)
        content = self._content(hook, payload)
        substantive = self._is_substantive(hook, payload)
        # MỘT intent — nguồn sự thật duy nhất cho cả hai đường ghi. Các giá
        # trị này là call-site contract của hook (SP-0 đo được builder cũ đánh
        # rơi chúng: 0.72→0.5, metadata.state biến mất, retrieval lệch thật).
        intent = MemoryProjectionIntent(
            confidence=0.72,
            importance=0.55 if hook in {"UserPromptSubmit", "PostToolUse",
                                        "PostToolUseFailure", "TaskCompleted",
                                        "StopFailure"} else 0.35,
            salience=0.75 if hook in {"PostToolUseFailure", "StopFailure",
                                      "PermissionDenied"} else 0.50,
            utility=0.65,
            semantic_metadata={
                "hook": hook,
                "session_id": payload.get("session_id"),
                "tool": payload.get("tool_name"),
                "state": {
                    "mode": "debug" if hook in {"PostToolUseFailure",
                                                "StopFailure"} else "implement",
                    "stress_state": "failure" if hook in {
                        "PostToolUseFailure", "StopFailure",
                        "PermissionDenied"} else "normal",
                },
            },
        ) if substantive else None
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
            enqueue_projection=self._projection_debt_allowed(substantive),
            projection_intent=intent,
        )
        # An event always happened, so it is always recorded — events are the
        # audit trail and dropping one would be lying about what occurred.
        #
        # A *memory* is a different promise. When the payload carries none of
        # the keys `_content` looks for, the content it produces is literally
        # "hook=SessionStart": the entire body is the name of the mechanism that
        # created it. Storing that competes for retrieval slots with everything
        # that means something, and it wins, because it is the newest thing in
        # the store every single session.
        #
        # Measured 2026-08-07: three of the five slots a recall returns were
        # rows of exactly that shape, on a store holding the company bank
        # account and a customer's real pricing. The store was not short of
        # signal; the signal was being outvoted by its own logging.
        if not substantive:
            return HookIngestResult(True, event.event_id, hook,
                                    "no_substantive_content", None)

        if not self._direct_write_allowed():
            # OUTBOX: event đã mang nợ projection; worker sẽ materialize.
            # KHÔNG remember() — bản ghi thẳng chính là nhánh đã nhân đôi
            # một prompt thật của chủ ngày 18/08.
            return HookIngestResult(True, event.event_id, hook, None, None)

        projected = self.memory_os.remember(
            event=event,
            memory_type=MemoryType.EPISODIC,
            content=content,
            confidence=intent.confidence,
            importance=intent.importance,
            salience=intent.salience,
            utility=intent.utility,
            metadata=intent.semantic_metadata,
        )
        memory_id = getattr(projected, "memory_id", None)
        return HookIngestResult(True, event.event_id, hook, None, memory_id)

    #: The payload keys that carry something a person could later want back.
    #: `_content` renders them; `_is_substantive` decides whether any were there.
    CONTENT_KEYS = (
        "prompt", "command_name", "tool_name", "tool_input", "tool_output",
        "error", "error_details", "summary", "compact_summary", "task_prompt",
        "last_assistant_message", "reason", "file_path", "delta",
    )

    #: Below this, a rendered value is a marker rather than a memory — an empty
    #: string, a lone punctuation mark, an "ok". Deliberately small: the job is
    #: to drop the empty, not to judge the brief.
    MIN_CONTENT_CHARS = 3

    @classmethod
    def _is_substantive(cls, hook: str, payload: dict[str, Any]) -> bool:
        """Did the payload bring anything beyond the fact that a hook fired?"""
        for key in cls.CONTENT_KEYS:
            value = payload.get(key)
            if value in (None, "", {}, []):
                continue
            if isinstance(value, str) and len(value.strip()) < cls.MIN_CONTENT_CHARS:
                continue
            return True
        return False

    @classmethod
    def _content(cls, hook: str, payload: dict[str, Any]) -> str:
        parts = [f"hook={hook}"]
        for key in cls.CONTENT_KEYS:
            value = payload.get(key)
            if value not in (None, "", {}, []):
                parts.append(f"{key}={value}")
        return " | ".join(parts)
