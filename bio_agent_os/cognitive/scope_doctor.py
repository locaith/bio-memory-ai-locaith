"""Does this process agree with the database about which partition it is in?

Written because the answer was no for eighteen hours and nothing said so. The
symptom was three unrelated queries returning the same four useless rows, which
reads exactly like a broken index — and the first diagnosis, mine, was that
ranking or full-text search had failed. It had not. The process was resolving to
a partition holding seven junk rows while thirty real memories sat in another.

A correct isolation boundary and a broken retriever are indistinguishable from
inside a single query. They are trivially distinguishable from one level up: ask
what partitions exist, and how many memories are in each. So that is what this
does, and it reports `SCOPE_CONFIGURATION_MISMATCH` rather than a retrieval
failure, because that is what it is.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .scope import MemoryScope, ScopeSource, WorkspaceStrategy, resolve_scope


@dataclass(frozen=True, slots=True)
class ScopeRow:
    tenant_id: str
    workspace_id: str
    memories: int
    first_seen: str | None
    last_seen: str | None

    @property
    def looks_like_a_path(self) -> bool:
        w = self.workspace_id or ""
        return ("/" in w or "\\" in w or (len(w) > 2 and w[1:2] == ":"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "memories": self.memories,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class ScopeReport:
    scope: MemoryScope
    scopes_in_database: list[ScopeRow] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_memories(self) -> int:
        for row in self.scopes_in_database:
            if (row.tenant_id, row.workspace_id) == (self.scope.tenant_id,
                                                      self.scope.workspace_id):
                return row.memories
        return 0

    @property
    def exit_code(self) -> int:
        return 1 if any(f["severity"] in ("FAIL", "CRITICAL") for f in self.findings) else 0

    def add(self, code: str, severity: str, message: str, **evidence: Any) -> None:
        self.findings.append({"code": code, "severity": severity,
                              "message": message, **evidence})

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.scope.as_dict(),
            "resolved_memories": self.resolved_memories,
            "scopes_in_database": [r.as_dict() for r in self.scopes_in_database],
            "findings": self.findings,
            "exit_code": self.exit_code,
        }

    def render(self, width: int = 74) -> str:
        mark = {"PASS": "  ", "INFO": "  ", "WARN": " !", "FAIL": "!!", "CRITICAL": "!!"}
        lines = [
            "=" * width,
            "  MEMORY SCOPE",
            "=" * width,
            f"  tenant                    {self.scope.tenant_id}",
            f"  workspace                 {self.scope.workspace_id}",
            f"  fingerprint               {self.scope.fingerprint}",
            f"  tenant resolved from      {self.scope.tenant_source}",
            f"  workspace resolved from   {self.scope.workspace_source}",
            f"  strategy                  {self.scope.strategy}",
            f"  memories in this scope    {self.resolved_memories:,}",
            "-" * width,
            "  SCOPES PRESENT IN THIS DATABASE",
        ]
        if not self.scopes_in_database:
            lines.append("    (none)")
        for row in sorted(self.scopes_in_database, key=lambda r: -r.memories):
            here = " <- resolved" if (row.tenant_id, row.workspace_id) == (
                self.scope.tenant_id, self.scope.workspace_id) else ""
            ws = row.workspace_id if len(row.workspace_id) <= 40 else row.workspace_id[:37] + "..."
            lines.append(f"    {row.memories:>6,}  {row.tenant_id} / {ws}{here}")
        lines.append("-" * width)
        for f in self.findings:
            lines.append(f"  {mark.get(f['severity'], '  ')} {f['code']}")
            lines.append(f"       {f['message']}")
        lines.append("=" * width)
        return "\n".join(lines)


def scopes_in_database(conn: sqlite3.Connection) -> list[ScopeRow]:
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT tenant_id, workspace_id, COUNT(*) n, "
            "       MIN(created_at) first_seen, MAX(created_at) last_seen "
            "FROM cognitive_memories GROUP BY tenant_id, workspace_id"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [ScopeRow(tenant_id=r["tenant_id"] or "", workspace_id=r["workspace_id"] or "",
                     memories=int(r["n"]), first_seen=r["first_seen"], last_seen=r["last_seen"])
            for r in rows]


def diagnose(conn: sqlite3.Connection, scope: MemoryScope | None = None) -> ScopeReport:
    """Compare what this process resolved against what the database contains."""
    scope = scope or resolve_scope()
    report = ScopeReport(scope=scope, scopes_in_database=scopes_in_database(conn))
    rows = report.scopes_in_database
    mine = report.resolved_memories
    others = [r for r in rows
              if (r.tenant_id, r.workspace_id) != (scope.tenant_id, scope.workspace_id)]
    biggest_other = max(others, key=lambda r: r.memories, default=None)

    # -- the finding this module exists for ---------------------------------
    if biggest_other and biggest_other.memories > max(mine * 2, 5):
        report.add(
            "SCOPE_CONFIGURATION_MISMATCH", "FAIL",
            f"this process resolved to {scope.tenant_id}/{scope.workspace_id}, which "
            f"holds {mine:,} memories, while {biggest_other.tenant_id}/"
            f"{biggest_other.workspace_id} holds {biggest_other.memories:,}. Recall will "
            f"look empty or wrong and it is not a retrieval fault — the partitions are "
            f"doing their job. Align the scope, or migrate deliberately.",
            resolved=scope.as_dict(), resolved_memories=mine,
            largest_other=biggest_other.as_dict(),
        )

    # -- how a mismatch is usually born -------------------------------------
    if scope.workspace_looks_like_a_path and scope.strategy != WorkspaceStrategy.PROJECT_PATH.value:
        report.add(
            "WORKSPACE_ID_IS_A_PATH", "WARN",
            f"the workspace id {scope.workspace_id!r} looks like a filesystem path but "
            f"the strategy is {scope.strategy!r}. Memory then fragments once per "
            f"directory. Set a stable workspace id, or ask for project_path on purpose.",
            workspace_id=scope.workspace_id, strategy=scope.strategy,
        )
    if scope.is_fallback and rows:
        report.add(
            "SCOPE_UNCONFIGURED", "WARN",
            f"nothing configured this scope (tenant from {scope.tenant_source}, workspace "
            f"from {scope.workspace_source}) yet this database already holds "
            f"{len(rows)} scope(s). A fallback that lands next to real data is usually "
            f"an entry point nobody wired up.",
            **scope.as_dict(),
        )
    if scope.workspace_source == ScopeSource.LEGACY_ENV.value or \
            scope.tenant_source == ScopeSource.LEGACY_ENV.value:
        report.add(
            "SCOPE_FROM_LEGACY_ENV", "INFO",
            "resolved from BIO_MEMORY_* rather than BIO_AGENT_*. Supported, but the "
            "canonical names are what the other entry points read first.",
            **scope.as_dict(),
        )

    # -- fragmentation, even without a clear winner --------------------------
    path_like = [r for r in rows if r.looks_like_a_path]
    if len(rows) > 1 and path_like:
        report.add(
            "SCOPE_FRAGMENTED_BY_PATH", "WARN",
            f"{len(path_like)} of {len(rows)} scopes are named after filesystem paths. "
            f"Each directory a process starts in becomes its own memory.",
            path_scopes=[r.as_dict() for r in path_like],
        )

    if not report.findings:
        report.add("SCOPE_CONSISTENT", "PASS",
                   f"{scope.tenant_id}/{scope.workspace_id} holds {mine:,} memories and "
                   f"nothing larger sits beside it.")
    return report
