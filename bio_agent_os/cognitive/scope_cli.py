"""`scope status`, `scope doctor`, `scope migrate` — see the partition, and move it on purpose.

Migration is the dangerous one, so it is built to be hard to do by accident:

* `--dry-run` is the default and has to be turned off explicitly.
* Source and destination are both required. Nothing is inferred, and in
  particular nothing is inferred *from the content of a memory* — guessing that
  a row about a contract "belongs to" a tenant is exactly how customer data
  crosses a boundary it was placed behind on purpose.
* Nothing runs at startup. A migration is an operator pressing a key.
* The original tenant and workspace are preserved on every row that moves, with
  a reason and a timestamp, so a wrong move can be read back and reversed.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from .scope import resolve_scope
from .scope_doctor import diagnose, scopes_in_database

MIGRATION_TABLE = "memory_scope_migrations"


def _default_db() -> Path:
    return Path(os.environ.get(
        "BIO_MEMORY_DB", str(Path.cwd() / ".bio-agent-os" / "memory.db")))


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_audit_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
            migration_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id        TEXT NOT NULL,
            version          INTEGER,
            from_tenant_id   TEXT NOT NULL,
            from_workspace_id TEXT NOT NULL,
            to_tenant_id     TEXT NOT NULL,
            to_workspace_id  TEXT NOT NULL,
            reason           TEXT NOT NULL,
            migrated_at      TEXT NOT NULL,
            operator         TEXT
        )
    """)


# ==========================================================================
# commands
# ==========================================================================

def cmd_status(args: argparse.Namespace) -> int:
    scope = resolve_scope()
    payload = scope.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for key, value in payload.items():
        print(f"  {key:<20} {value}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    conn = _connect(args.db)
    try:
        report = diagnose(conn)
    finally:
        conn.close()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
          if args.json else report.render())
    return report.exit_code


def cmd_migrate(args: argparse.Namespace) -> int:
    """Move memories between partitions. Refuses to guess anything."""
    from_tenant, _, from_workspace = args.source.partition("/")
    to_tenant, _, to_workspace = args.dest.partition("/")
    if not (from_tenant and from_workspace and to_tenant and to_workspace):
        print("  --from and --to must both look like tenant/workspace", file=sys.stderr)
        return 2
    if (from_tenant, from_workspace) == (to_tenant, to_workspace):
        print("  source and destination are the same scope; nothing to do", file=sys.stderr)
        return 2
    if not args.reason:
        print("  --reason is required: a migration without a recorded why cannot be "
              "reviewed later", file=sys.stderr)
        return 2

    conn = _connect(args.db)
    try:
        known = {(r.tenant_id, r.workspace_id): r.memories for r in scopes_in_database(conn)}
        if (from_tenant, from_workspace) not in known:
            print(f"  source scope {args.source} does not exist in this database. "
                  f"Present: " + ", ".join(f"{t}/{w}" for t, w in known), file=sys.stderr)
            return 2

        rows = conn.execute(
            "SELECT memory_id, version, memory_type, content FROM cognitive_memories "
            "WHERE tenant_id = ? AND workspace_id = ?",
            (from_tenant, from_workspace),
        ).fetchall()

        print(f"  from   {from_tenant}/{from_workspace}")
        print(f"  to     {to_tenant}/{to_workspace}")
        print(f"  rows   {len(rows):,}")
        print(f"  reason {args.reason}")
        print()
        for r in rows[: args.preview]:
            body = " ".join(str(r["content"] or "").split())[:78]
            print(f"    [{r['memory_type']:<11} v{r['version']}] {body}")
        if len(rows) > args.preview:
            print(f"    ... and {len(rows) - args.preview:,} more")
        print()

        if args.dry_run:
            print("  DRY RUN — nothing was written. Re-run with --apply to migrate.")
            return 0

        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        operator = args.operator or os.environ.get("USERNAME") or os.environ.get("USER")
        _ensure_audit_table(conn)
        with conn:  # one transaction: the audit trail and the move land together
            conn.executemany(
                f"INSERT INTO {MIGRATION_TABLE} (memory_id, version, from_tenant_id, "
                f"from_workspace_id, to_tenant_id, to_workspace_id, reason, migrated_at, "
                f"operator) VALUES (?,?,?,?,?,?,?,?,?)",
                [(r["memory_id"], r["version"], from_tenant, from_workspace,
                  to_tenant, to_workspace, args.reason, stamp, operator) for r in rows],
            )
            conn.execute(
                "UPDATE cognitive_memories SET tenant_id = ?, workspace_id = ? "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (to_tenant, to_workspace, from_tenant, from_workspace),
            )
        print(f"  migrated {len(rows):,} memories, audit rows written to {MIGRATION_TABLE}")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bio-agent-os scope",
                                description="Inspect and repair memory scope")
    p.add_argument("--db", default=str(_default_db()), help="path to memory.db")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="what scope does this process resolve to")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="compare the resolved scope against the database")
    d.set_defaults(func=cmd_doctor)

    m = sub.add_parser("migrate", help="move memories between scopes (dry run by default)")
    m.add_argument("--from", dest="source", required=True, metavar="TENANT/WORKSPACE")
    m.add_argument("--to", dest="dest", required=True, metavar="TENANT/WORKSPACE")
    m.add_argument("--reason", required=True, help="why this migration is correct")
    m.add_argument("--operator", default=None, help="who authorised it")
    m.add_argument("--preview", type=int, default=10, help="rows to show (default 10)")
    grp = m.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    grp.add_argument("--apply", dest="dry_run", action="store_false",
                     help="actually write the change")
    m.set_defaults(func=cmd_migrate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
