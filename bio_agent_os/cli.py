"""
Command-line interface for Bio-Agent OS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import uvicorn

from bio_agent_os.sdk import BioAgentSDK


def _base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bio-agent-os")
    parser.add_argument("--agent-name", default=os.getenv("AGENT_NAME", "Bio-AI"))
    parser.add_argument("--storage-dir", default=os.getenv("STORAGE_DIR", "data"))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--agent-name", default=os.getenv("AGENT_NAME", "Bio-AI"))
    common.add_argument("--storage-dir", default=os.getenv("STORAGE_DIR", "data"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve-api", help="Run FastAPI server", parents=[common])
    serve.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8055")))

    chat = subparsers.add_parser("chat", help="Send one chat turn", parents=[common])
    chat.add_argument("message")
    chat.add_argument("--task-id")
    chat.add_argument("--workspace-id")
    chat.add_argument("--project-version")
    chat.add_argument("--mode", default="implement")
    chat.add_argument("--risk-level", default="medium")
    chat.add_argument("--stress-state", default="normal")

    ingest = subparsers.add_parser("ingest", help="Ingest one observation", parents=[common])
    ingest.add_argument("text")
    ingest.add_argument("--source", default="cli")
    ingest.add_argument("--task-id")
    ingest.add_argument("--workspace-id")
    ingest.add_argument("--project-version")

    subparsers.add_parser("sleep", help="Run garbage collection + consolidate", parents=[common])
    subparsers.add_parser("dream", help="Run dream cycle", parents=[common])

    reflect = subparsers.add_parser("reflect", help="Generate reflection summary", parents=[common])
    reflect.add_argument("--query", default="")

    subparsers.add_parser("status", help="Print health/status snapshot", parents=[common])
    return parser


def _print(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main():
    parser = _base_parser()
    args = parser.parse_args()

    if args.command == "serve-api":
        uvicorn.run("bio_agent_os.api.main:app", host=args.host, port=args.port, reload=False)
        return

    sdk = BioAgentSDK(agent_name=args.agent_name, storage_dir=args.storage_dir)

    if args.command == "chat":
        payload = asyncio.run(
            sdk.chat(
                args.message,
                task_id=args.task_id,
                workspace_id=args.workspace_id,
                project_version=args.project_version,
                mode=args.mode,
                risk_level=args.risk_level,
                stress_state=args.stress_state,
            )
        )
        _print(payload)
        return

    if args.command == "ingest":
        payload = asyncio.run(
            sdk.ingest(
                args.text,
                source=args.source,
                task_id=args.task_id,
                workspace_id=args.workspace_id,
                project_version=args.project_version,
            )
        )
        _print(payload)
        return

    if args.command == "sleep":
        _print(asyncio.run(sdk.sleep()))
        return

    if args.command == "dream":
        _print(asyncio.run(sdk.dream()))
        return

    if args.command == "reflect":
        _print(asyncio.run(sdk.reflect(query=args.query)))
        return

    if args.command == "status":
        _print(sdk.status())
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
