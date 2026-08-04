from __future__ import annotations

import argparse
import json
from pathlib import Path

from bio_agent_os.cognitive.hooks import SUPPORTED_CLAUDE_HOOKS


CONTEXT_EVENTS = {"SessionStart", "UserPromptSubmit", "UserPromptExpansion", "PostCompact", "SubagentStart"}


def handler(event: str) -> dict:
    item = {
        "type": "command",
        "command": "bio-memory-hook",
        "args": [event],
        "timeout": 10 if event in CONTEXT_EVENTS else 5,
    }
    if event not in CONTEXT_EVENTS and event not in {"PreToolUse", "PermissionRequest"}:
        item["async"] = True
    return item


def install(project: Path, shared: bool = False) -> Path:
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    target = claude_dir / ("settings.json" if shared else "settings.local.json")
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event in sorted(SUPPORTED_CLAUDE_HOOKS):
        group = {"matcher": "*", "hooks": [handler(event)]}
        existing = hooks.setdefault(event, [])
        signature = ("bio-memory-hook", (event,))
        already = False
        for current_group in existing:
            for current_handler in current_group.get("hooks", []):
                if (current_handler.get("command"), tuple(current_handler.get("args", []))) == signature:
                    already = True
                    break
        if not already:
            existing.append(group)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Bio-AGI Memory OS hooks into Claude Code")
    parser.add_argument("--project", default=".")
    parser.add_argument("--shared", action="store_true", help="Write .claude/settings.json instead of local settings")
    args = parser.parse_args()
    target = install(Path(args.project).resolve(), shared=args.shared)
    print(f"Installed {len(SUPPORTED_CLAUDE_HOOKS)} Bio-AGI memory hook events into {target}")


if __name__ == "__main__":
    main()
