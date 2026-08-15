"""Which runtime is actually running, recorded where it can be compared.

Written after a watcher process spent two days rejecting every capture it was
given. The code on disk had been fixed on 13/08; the process had been started
on the 11th and was still executing the old module. Nothing was wrong with the
repository, the tests, or the config — reading any of them said the system was
healthy, and eight inputs were quietly dropped.

The lesson is narrow and worth stating plainly:

    Code and config being right on disk says nothing about the process.

So a runtime says who it is, and says it into the database rather than into a
log line nobody greps. Three uses, in rising order of usefulness:

* `identity()`      answer "what am I" from inside the process
* `register()`      write it down, with a heartbeat, so another process can see
* `require()`       refuse to proceed against a runtime that is not the
                    expected one — the benchmark calls this before it measures
                    anything, because a number produced by an unknown build is
                    not evidence, it is a rumour

`fingerprint` deliberately covers only what changes behaviour: code, config,
embedding space, database. `pid` and `process_start_time` are recorded but not
fingerprinted — they differ on every honest restart, and a check that fails on
restart is a check people learn to skip.

The `git_dirty` flag is inside the fingerprint. An uncommitted edit is exactly
the case where the sha lies, and it is also the normal state during
development, which is why it changes the fingerprint rather than raising.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterable

from bio_agent_os import __version__

#: Environment variables that change what the system does. Their *values* are
#: hashed, never stored — several hold credentials.
#:
#: A name absent from here contributes nothing to the fingerprint, which is the
#: honest failure mode: a config that does not appear cannot claim to have been
#: checked. Add to this list when adding a switch, and the fingerprint changes
#: for everyone, which is the point.
CONFIG_KEYS: tuple[str, ...] = (
    "LLM_BACKEND",
    "LLM_MODEL",
    "EMBEDDING_BACKEND",
    "EMBEDDING_MODEL",
    "BIO_DB_PATH",
    "BIO_TENANT",
    "BIO_WORKSPACE",
    "BIO_AUTONOMY_LEVEL",
    "BIO_SCHEDULER_ENABLED",
)

_TABLE = "runtime_sessions"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    session_id           TEXT PRIMARY KEY,
    fingerprint          TEXT NOT NULL,
    package_version      TEXT NOT NULL,
    git_sha              TEXT NOT NULL,
    git_dirty            INTEGER NOT NULL,
    config_hash          TEXT NOT NULL,
    embedding_model      TEXT NOT NULL,
    embedding_dims       INTEGER NOT NULL,
    db_path              TEXT NOT NULL,
    host                 TEXT NOT NULL,
    pid                  INTEGER NOT NULL,
    python_version       TEXT NOT NULL,
    process_start_time   REAL NOT NULL,
    started_at           REAL NOT NULL,
    last_heartbeat       REAL NOT NULL,
    last_processed_event TEXT,
    processed_count      INTEGER NOT NULL DEFAULT 0,
    rejected_count       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runtime_sessions_fp
    ON {_TABLE} (fingerprint, last_heartbeat);
"""


class RuntimeMismatch(RuntimeError):
    """The running process is not the one whose results were expected.

    Carries both sides. "Fingerprint mismatch" alone sends the reader back to
    the shell to work out which field moved, and the field that moved is
    usually the whole diagnosis.
    """

    def __init__(self, expected: str, actual: "RuntimeIdentity",
                 differing: dict[str, tuple[Any, Any]] | None = None) -> None:
        self.expected = expected
        self.actual = actual
        self.differing = differing or {}
        detail = ""
        if self.differing:
            detail = " | " + ", ".join(
                f"{field_name}: kỳ vọng {want!r}, đang chạy {got!r}"
                for field_name, (want, got) in sorted(self.differing.items()))
        super().__init__(
            f"runtime không khớp: kỳ vọng {expected[:12]}, "
            f"đang chạy {actual.fingerprint[:12]}{detail}")


@dataclass(frozen=True)
class RuntimeIdentity:
    """What this process is, as far as it can honestly tell."""

    package_version: str
    git_sha: str
    git_dirty: bool
    config_hash: str
    embedding_model: str
    embedding_dims: int
    db_path: str
    host: str
    pid: int
    python_version: str
    process_start_time: float

    #: Everything above that changes behaviour. A ClassVar, so it stays out of
    #: `__init__` and `asdict`. Order is fixed on purpose: a fingerprint that
    #: depends on dict ordering is not a fingerprint.
    FINGERPRINTED: ClassVar[tuple[str, ...]] = (
        "package_version", "git_sha", "git_dirty", "config_hash",
        "embedding_model", "embedding_dims", "db_path",
    )

    @property
    def fingerprint(self) -> str:
        material = json.dumps(
            {name: getattr(self, name) for name in self.FINGERPRINTED},
            sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def differences(self, other: "RuntimeIdentity") -> dict[str, tuple[Any, Any]]:
        """Which fingerprinted fields differ, for a message worth reading."""
        return {name: (getattr(self, name), getattr(other, name))
                for name in self.FINGERPRINTED
                if getattr(self, name) != getattr(other, name)}

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint
        return payload


def _git(*args: str) -> str | None:
    """Run a git command, or return None. Never raises, never blocks for long.

    A missing git, a detached worktree and an unpacked tarball are all normal
    ways to run this code, and none of them should stop a process from starting.
    They do change the answer to "which build is this", so the absence is
    recorded as `unknown` rather than papered over with a default.
    """
    try:
        out = subprocess.run(
            ("git", *args), cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def config_hash(keys: Iterable[str] = CONFIG_KEYS,
                environ: dict[str, str] | None = None) -> str:
    """Hash the configuration that changes behaviour, without storing it.

    Values are hashed rather than recorded because `LLM_BACKEND` sits in the
    same file as the API keys, and a provenance table that quietly accumulates
    secrets is a worse problem than the one it solves.

    An unset key hashes as unset, distinctly from an empty string: `FOO=` and
    no `FOO` at all mean different things to `os.getenv`, and the difference has
    already cost a day on this project.
    """
    from bio_agent_os.core.env import load_project_env

    if environ is None:
        load_project_env()
        environ = dict(os.environ)
    material = json.dumps(
        [[key, environ.get(key)] for key in sorted(keys)],
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _process_start_time() -> float:
    """When this process started, not when this function was called.

    Falls back to import time where `psutil` is absent, which is honest but
    weaker: it cannot distinguish a long-running stale worker from a fresh one
    if the module is reimported. The stale-worker case is the whole reason this
    file exists, so the real value is worth the optional dependency.
    """
    try:
        import psutil                                    # noqa: PLC0415
    except ImportError:
        return _IMPORTED_AT
    try:
        return float(psutil.Process(os.getpid()).create_time())
    except Exception:                                    # noqa: BLE001
        return _IMPORTED_AT


_IMPORTED_AT = time.time()


def identity(*, db_path: str | Path = "", embedder: Any = None,
             environ: dict[str, str] | None = None) -> RuntimeIdentity:
    """Describe this process. Cheap enough to call on every startup."""
    sha = _git("rev-parse", "HEAD") or "unknown"
    status = _git("status", "--porcelain")
    dirty = bool(status) if status is not None else False

    model, dims = "", 0
    if embedder is not None:
        model = str(getattr(embedder, "model_name", None)
                    or getattr(embedder, "backend", None) or "")
        dims = int(getattr(embedder, "dims", 0)
                   or getattr(embedder, "dimensions", 0) or 0)

    return RuntimeIdentity(
        package_version=__version__,
        git_sha=sha,
        git_dirty=dirty,
        config_hash=config_hash(environ=environ),
        embedding_model=model,
        embedding_dims=dims,
        db_path=str(Path(db_path).resolve()) if db_path else "",
        host=socket.gethostname(),
        pid=os.getpid(),
        python_version=platform.python_version(),
        process_start_time=_process_start_time(),
    )


class RuntimeRegistry:
    """The `runtime_sessions` table, and the three things worth doing to it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def register(self, who: RuntimeIdentity, *,
                 session_id: str | None = None) -> str:
        """Record a running process and return its session id."""
        session_id = session_id or uuid.uuid4().hex
        now = time.time()
        row = who.as_dict()
        self.conn.execute(
            f"""INSERT OR REPLACE INTO {_TABLE} (
                    session_id, fingerprint, package_version, git_sha,
                    git_dirty, config_hash, embedding_model, embedding_dims,
                    db_path, host, pid, python_version, process_start_time,
                    started_at, last_heartbeat, last_processed_event,
                    processed_count, rejected_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0,0)""",
            (session_id, row["fingerprint"], row["package_version"],
             row["git_sha"], int(row["git_dirty"]), row["config_hash"],
             row["embedding_model"], row["embedding_dims"], row["db_path"],
             row["host"], row["pid"], row["python_version"],
             row["process_start_time"], now, now))
        self.conn.commit()
        return session_id

    def heartbeat(self, session_id: str, *,
                  last_processed_event: str | None = None,
                  processed: int = 0, rejected: int = 0) -> None:
        """Say the process is still alive, and what it has got through.

        `processed` and `rejected` are increments, not totals. A worker that
        reports totals has to remember them across restarts, and the one that
        forgot is the reason this module exists.
        """
        self.conn.execute(
            f"""UPDATE {_TABLE}
                   SET last_heartbeat = ?,
                       last_processed_event = COALESCE(?, last_processed_event),
                       processed_count = processed_count + ?,
                       rejected_count = rejected_count + ?
                 WHERE session_id = ?""",
            (time.time(), last_processed_event, int(processed), int(rejected),
             session_id))
        self.conn.commit()

    def sessions(self, *, since: float | None = None) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            f"SELECT * FROM {_TABLE} "
            f"{'WHERE last_heartbeat >= ?' if since else ''} "
            f"ORDER BY last_heartbeat DESC",
            (since,) if since else ())
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def stale(self, *, older_than_seconds: float) -> list[dict[str, Any]]:
        """Sessions that have not checked in. The watcher case, found early."""
        cutoff = time.time() - older_than_seconds
        return [s for s in self.sessions() if s["last_heartbeat"] < cutoff]


def require(expected: str | RuntimeIdentity, *, actual: RuntimeIdentity) -> None:
    """Refuse to continue unless the runtime is the expected one.

    Called by anything whose output will be compared against an earlier number.
    Two runs of a benchmark are only comparable if the thing being measured did
    not change underneath, and "I am fairly sure I did not change anything" is
    not a control.
    """
    if isinstance(expected, RuntimeIdentity):
        wanted, differing = expected.fingerprint, expected.differences(actual)
    else:
        wanted, differing = str(expected), {}
    if wanted != actual.fingerprint:
        raise RuntimeMismatch(wanted, actual, differing)


__all__ = [
    "CONFIG_KEYS",
    "RuntimeIdentity",
    "RuntimeMismatch",
    "RuntimeRegistry",
    "config_hash",
    "identity",
    "require",
]
