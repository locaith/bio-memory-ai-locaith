"""Who is asking — resolved once, the same way, for every entry point.

Memories are partitioned by `(tenant_id, workspace_id)` and the partition is
enforced all the way down to the SQL. That is correct and it is not the thing
that went wrong. What went wrong is that every entry point invented its own
answer to "which partition am I":

    locaith_os bridge     tenant="locaith"          workspace="personal"
    locaith CLI           -                         workspace="personal"
    Claude Code hook      env or "local"            env or **the current directory**
    bio doctor CLI        tenant="doctor"           workspace=None
    canary supervisor     its own                   its own

The hook was installed on 2026-08-07 at 01:12. From that minute it wrote into a
partition named after a filesystem path, and read back from the same one, while
thirty memories written by the CLI sat one partition away. Three different
queries returned the same four useless rows, and the obvious reading was that
retrieval had failed. Retrieval had not failed. Isolation was working perfectly
and hiding exactly what it is supposed to hide.

That is the failure mode this module exists to prevent, and it is a nasty one
precisely because a correct isolation boundary and a broken index look identical
from the outside. So:

**One resolver.** Every entry point calls `resolve_scope()`. None of them decide
for themselves.

**No silent path scoping.** A workspace named after `cwd` fragments memory once
per directory, which is almost never what anyone wants and was never asked for.
Path scoping is still available, but only when someone writes it down:
`workspace_strategy="project_path"`.

**A fingerprint that can be compared.** Two processes that believe they share a
workspace can print one short string each and find out in one glance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("bio_agent_os.scope")

#: Used when nothing else says otherwise. A name, deliberately — not a path, not
#: a hostname, not anything that varies by where the process happened to start.
FALLBACK_TENANT_ID = "default"
FALLBACK_WORKSPACE_ID = "default"

#: Canonical environment variables.
ENV_TENANT = "BIO_AGENT_TENANT_ID"
ENV_WORKSPACE = "BIO_AGENT_WORKSPACE_ID"
ENV_STRATEGY = "BIO_AGENT_WORKSPACE_STRATEGY"
ENV_PROFILE = "BIO_AGENT_PROFILE"

#: Accepted for compatibility with the hook that shipped first. Canonical names
#: win when both are set.
LEGACY_ENV_TENANT = "BIO_MEMORY_TENANT"
LEGACY_ENV_WORKSPACE = "BIO_MEMORY_WORKSPACE"

DEFAULT_PROFILE_PATH = Path.home() / ".bio-agent-os" / "profile.json"


class WorkspaceStrategy(str, Enum):
    """How a workspace id is arrived at, when one is not handed over directly.

    EXPLICIT      the id comes from an argument, the environment, or a profile.
                  This is the default and the only one that keeps a workspace
                  stable across directories.
    PROJECT_PATH  the id is derived from the project directory. Legitimate for
                  genuinely per-checkout memory, but it must be asked for. It
                  cannot be a default while any other entry point uses a stable
                  name, because then the two silently disagree.
    """

    EXPLICIT = "explicit"
    PROJECT_PATH = "project_path"


class ScopeSource(str, Enum):
    """Which rung of the precedence ladder actually answered."""

    EXPLICIT = "explicit"
    ENV = "env"
    LEGACY_ENV = "legacy_env"
    PROFILE = "profile"
    PROJECT_PATH = "project_path"
    FALLBACK = "fallback"


def _fingerprint(tenant_id: str, workspace_id: str) -> str:
    """A short, stable, comparable id for a partition.

    Contains no secret: tenant and workspace ids are names, and the digest is
    here so two processes can be compared in a log line without printing a
    Windows path into somebody's terminal.
    """
    raw = f"{tenant_id}\x00{workspace_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """A resolved partition, and the receipt showing how it was resolved."""

    tenant_id: str
    workspace_id: str
    tenant_source: str
    workspace_source: str
    strategy: str

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.tenant_id, self.workspace_id)

    @property
    def is_fallback(self) -> bool:
        return (self.tenant_source == ScopeSource.FALLBACK.value
                or self.workspace_source == ScopeSource.FALLBACK.value)

    @property
    def workspace_looks_like_a_path(self) -> bool:
        w = self.workspace_id
        return ("/" in w or "\\" in w or (len(w) > 2 and w[1] == ":"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "tenant_source": self.tenant_source,
            "workspace_source": self.workspace_source,
            "strategy": self.strategy,
            "scope_fingerprint": self.fingerprint,
        }

    def render(self) -> str:
        return (f"tenant={self.tenant_id} workspace={self.workspace_id} "
                f"fingerprint={self.fingerprint} "
                f"(tenant from {self.tenant_source}, workspace from {self.workspace_source})")


def _load_profile(path: str | Path | None = None) -> dict[str, Any]:
    """Read the configured profile, or an empty one.

    A missing profile is normal and silent. A malformed profile is not: it means
    someone tried to configure this and the configuration is being ignored,
    which is exactly how a scope mismatch is born.
    """
    candidate = Path(path) if path else Path(os.environ.get(ENV_PROFILE, DEFAULT_PROFILE_PATH))
    try:
        raw = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("ignoring unparsable scope profile at %s: %s", candidate, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _project_workspace_id(project_path: str | Path | None) -> str:
    """A stable id derived from a directory, for the explicit path strategy.

    The directory name plus a digest of the full path: readable enough to
    recognise, unique enough not to collide between two checkouts of the same
    repository.
    """
    p = Path(project_path or os.getcwd()).resolve()
    return f"{p.name}-{hashlib.sha256(str(p).encode('utf-8')).hexdigest()[:8]}"


def resolve_scope(
    *,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    workspace_strategy: str | WorkspaceStrategy | None = None,
    project_path: str | Path | None = None,
    profile_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> MemoryScope:
    """Resolve the partition, in one documented order, for every caller.

        explicit argument
        -> BIO_AGENT_TENANT_ID / BIO_AGENT_WORKSPACE_ID
        -> BIO_MEMORY_TENANT / BIO_MEMORY_WORKSPACE   (compatibility)
        -> configured profile
        -> project path, but only when that strategy was asked for
        -> documented fallback ("default"/"default")

    Tenant and workspace are resolved independently — it is normal to pin one in
    the environment and leave the other to a profile — but each carries a record
    of which rung answered, so a mismatch can be explained rather than guessed
    at.
    """
    environ = os.environ if env is None else env
    profile = _load_profile(profile_path)

    strategy_raw = (
        workspace_strategy
        or environ.get(ENV_STRATEGY)
        or profile.get("workspace_strategy")
        or WorkspaceStrategy.EXPLICIT
    )
    try:
        strategy = WorkspaceStrategy(str(getattr(strategy_raw, "value", strategy_raw)).lower())
    except ValueError:
        logger.warning("unknown workspace strategy %r; using explicit", strategy_raw)
        strategy = WorkspaceStrategy.EXPLICIT

    # -- tenant --------------------------------------------------------------
    if tenant_id:
        t, t_src = tenant_id, ScopeSource.EXPLICIT
    elif environ.get(ENV_TENANT):
        t, t_src = environ[ENV_TENANT], ScopeSource.ENV
    elif environ.get(LEGACY_ENV_TENANT):
        t, t_src = environ[LEGACY_ENV_TENANT], ScopeSource.LEGACY_ENV
    elif profile.get("tenant_id"):
        t, t_src = str(profile["tenant_id"]), ScopeSource.PROFILE
    else:
        t, t_src = FALLBACK_TENANT_ID, ScopeSource.FALLBACK

    # -- workspace -----------------------------------------------------------
    if workspace_id:
        w, w_src = workspace_id, ScopeSource.EXPLICIT
    elif environ.get(ENV_WORKSPACE):
        w, w_src = environ[ENV_WORKSPACE], ScopeSource.ENV
    elif environ.get(LEGACY_ENV_WORKSPACE):
        w, w_src = environ[LEGACY_ENV_WORKSPACE], ScopeSource.LEGACY_ENV
    elif profile.get("workspace_id"):
        w, w_src = str(profile["workspace_id"]), ScopeSource.PROFILE
    elif strategy is WorkspaceStrategy.PROJECT_PATH:
        w, w_src = _project_workspace_id(project_path), ScopeSource.PROJECT_PATH
    else:
        w, w_src = FALLBACK_WORKSPACE_ID, ScopeSource.FALLBACK

    return MemoryScope(
        tenant_id=t, workspace_id=w,
        tenant_source=t_src.value, workspace_source=w_src.value,
        strategy=strategy.value,
    )


def log_scope(scope: MemoryScope, *, entrypoint: str) -> dict[str, Any]:
    """Announce the resolved scope, structured, at startup.

    Every entry point does this so that "are we in the same workspace" is a
    question answered by reading two log lines rather than by writing a SQL
    query at midnight.
    """
    payload = {"entrypoint": entrypoint, **scope.as_dict()}
    logger.info("resolved memory scope", extra=payload)
    return payload


__all__ = [
    "DEFAULT_PROFILE_PATH",
    "ENV_PROFILE",
    "ENV_STRATEGY",
    "ENV_TENANT",
    "ENV_WORKSPACE",
    "FALLBACK_TENANT_ID",
    "FALLBACK_WORKSPACE_ID",
    "LEGACY_ENV_TENANT",
    "LEGACY_ENV_WORKSPACE",
    "MemoryScope",
    "ScopeSource",
    "WorkspaceStrategy",
    "log_scope",
    "resolve_scope",
]
