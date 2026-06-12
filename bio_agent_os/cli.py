"""
Command-line interface for Bio-Agent OS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import uvicorn

from bio_agent_os.core.migration import SQLiteToPostgresMigrator
from bio_agent_os.rest_client import BioAgentRESTClient
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
    serve.add_argument(
        "--host",
        default=os.getenv("HOST", "127.0.0.1"),
        help="Bind address. Defaults to loopback; set BIO_AGENT_API_KEY before exposing other interfaces.",
    )
    serve.add_argument("--port", type=int, default=int(os.getenv("PORT", "8055")))

    serve_mcp = subparsers.add_parser(
        "serve-mcp",
        help="Run the MCP server over stdio (embedded memory, or proxy via --base-url)",
        parents=[common],
    )
    serve_mcp.add_argument(
        "--base-url",
        default=os.getenv("BIO_AGENT_BASE_URL", ""),
        help="Proxy tool calls to a running Bio-Agent OS sidecar instead of hosting memory in-process.",
    )
    serve_mcp.add_argument("--api-key", default=os.getenv("BIO_AGENT_API_KEY"))
    serve_mcp.add_argument(
        "--workspace-id",
        default=os.getenv("BIO_AGENT_WORKSPACE_ID"),
        help="Default workspace for tool calls that omit workspace_id.",
    )

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

    migrate = subparsers.add_parser("migrate-db", help="Migrate SQLite data to PostgreSQL", parents=[common])
    migrate.add_argument("--sqlite-path", default="")
    migrate.add_argument("--postgres-dsn", default=os.getenv("BIO_AGENT_DATABASE_URL", ""))

    rstatus = subparsers.add_parser("remote-status", help="Read status from a running Bio-Agent OS server")
    rstatus.add_argument("--base-url", default=os.getenv("BIO_AGENT_BASE_URL", "http://127.0.0.1:8055"))
    rstatus.add_argument("--api-key", default=os.getenv("BIO_AGENT_API_KEY"))

    rchat = subparsers.add_parser("remote-chat", help="Send one chat turn to a running Bio-Agent OS server")
    rchat.add_argument("message")
    rchat.add_argument("--base-url", default=os.getenv("BIO_AGENT_BASE_URL", "http://127.0.0.1:8055"))
    rchat.add_argument("--api-key", default=os.getenv("BIO_AGENT_API_KEY"))
    rchat.add_argument("--task-id")
    rchat.add_argument("--workspace-id")
    rchat.add_argument("--project-version")
    rchat.add_argument("--mode", default="implement")
    rchat.add_argument("--risk-level", default="medium")
    rchat.add_argument("--stress-state", default="normal")
    return parser


def _print(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main():
    parser = _base_parser()
    args = parser.parse_args()

    if args.command == "serve-api":
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        if args.host not in loopback_hosts and not os.getenv("BIO_AGENT_API_KEY"):
            print(
                "[bio-agent-os] WARNING: serving on a non-loopback interface "
                f"({args.host}) without BIO_AGENT_API_KEY — the API will accept "
                "unauthenticated requests from the network."
            )
        uvicorn.run("bio_agent_os.api.main:app", host=args.host, port=args.port, reload=False)
        return

    if args.command == "serve-mcp":
        from bio_agent_os.mcp_server import serve

        serve(
            agent_name=args.agent_name,
            storage_dir=args.storage_dir,
            base_url=args.base_url or None,
            api_key=args.api_key,
            default_workspace=args.workspace_id,
        )
        return

    if args.command == "remote-status":
        client = BioAgentRESTClient(base_url=args.base_url, api_key=args.api_key)
        _print(asyncio.run(client.status()))
        return

    if args.command == "remote-chat":
        client = BioAgentRESTClient(base_url=args.base_url, api_key=args.api_key)
        _print(
            asyncio.run(
                client.chat(
                    args.message,
                    task_id=args.task_id,
                    workspace_id=args.workspace_id,
                    project_version=args.project_version,
                    mode=args.mode,
                    risk_level=args.risk_level,
                    stress_state=args.stress_state,
                )
            )
        )
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

    if args.command == "migrate-db":
        sqlite_path = args.sqlite_path or str(Path(args.storage_dir) / "bio_agent_os.db")
        migrator = SQLiteToPostgresMigrator(sqlite_path=sqlite_path, postgres_dsn=args.postgres_dsn)
        summary = migrator.migrate()
        _print(
            {
                "sqlite_path": sqlite_path,
                "tables_created": summary.tables_created,
                "rows_copied": summary.rows_copied,
                "tables_skipped": summary.tables_skipped,
            }
        )
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
