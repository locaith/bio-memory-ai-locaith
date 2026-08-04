from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .facade import MemoryOS
from .hooks import ClaudeCodeHookAdapter
from .models import AccessContext, EpistemicStatus, VerificationStatus


def _memory_context(memory_os: MemoryOS, query: str, tenant: str, workspace: str, limit: int = 5) -> str:
    results = memory_os.recall(
        query or "current project rules procedures decisions",
        context=AccessContext(tenant_id=tenant, workspace_id=workspace, agent_id="claude-code"),
        state={"mode": "implement", "risk_level": "medium"},
        limit=limit,
    )
    lines: list[str] = []
    for item in results:
        memory = item.memory
        if memory.epistemic_status == EpistemicStatus.SIMULATED:
            continue
        if memory.verification_status == VerificationStatus.REJECTED:
            continue
        lines.append(
            f"- [{memory.memory_type.value}; confidence={memory.confidence:.2f}; "
            f"status={memory.epistemic_status.value}; id={memory.memory_id}] {memory.content}"
        )
    if not lines:
        return ""
    return "Relevant Bio-AGI Memory OS context (claims retain confidence and provenance labels):\n" + "\n".join(lines)


def main() -> None:
    payload: dict[str, Any] = json.load(sys.stdin)
    hook = sys.argv[1] if len(sys.argv) > 1 else str(payload.get("hook_event_name", ""))
    cwd = str(payload.get("cwd") or os.getcwd())
    tenant = os.environ.get("BIO_MEMORY_TENANT", "local")
    workspace = os.environ.get("BIO_MEMORY_WORKSPACE", cwd)
    db_path = Path(os.environ.get("BIO_MEMORY_DB", str(Path(cwd) / ".bio-agent-os" / "memory.db")))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    memory_os = MemoryOS(db_path)
    adapter = ClaudeCodeHookAdapter(memory_os, tenant, workspace)
    context_events = {"SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PostCompact", "SubagentStart"}
    query = str(payload.get("prompt") or payload.get("compact_summary") or payload.get("task_prompt") or "")
    # Recall before capture so a prompt is not immediately echoed back as its own memory.
    context = _memory_context(memory_os, query, tenant, workspace) if hook in context_events else ""
    result = adapter.ingest(hook, payload)

    output: dict[str, Any] = {"suppressOutput": True}
    if result.accepted and context:
        output["hookSpecificOutput"] = {"hookEventName": hook, "additionalContext": context}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
