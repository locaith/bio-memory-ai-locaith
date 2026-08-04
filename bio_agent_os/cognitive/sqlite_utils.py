from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

#: Anchor connections for shared in-memory databases.
#:
#: A SQLite shared-cache in-memory database exists only while at least one
#: connection to it is open. Every store in a runtime opening and closing its
#: own connection would let the database vanish between calls, so the runtime
#: holds one connection open for as long as it lives.
_ANCHORS: dict[str, sqlite3.Connection] = {}
_ANCHOR_LOCK = threading.Lock()


class MultiStoreMemoryError(RuntimeError):
    """Raised when several stores would silently get separate databases."""


def is_plain_memory(path: str | Path) -> bool:
    return str(path).strip() == ":memory:"


def shared_memory_uri(runtime_id: str | None = None) -> str:
    """A URI that several connections can share as one in-memory database.

    Plain ``:memory:`` gives every connection its own private database. With
    six stores in a runtime that produces six databases that cannot see each
    other, and tests pass while proving nothing about consistency. The shared
    URI form makes them one database.
    """
    rid = runtime_id or uuid.uuid4().hex[:16]
    return f"file:bio_agent_os_{rid}?mode=memory&cache=shared"


def resolve_runtime_path(path: str | Path, *, runtime_id: str | None = None) -> str:
    """Path a multi-store runtime should actually open.

    Plain ``:memory:`` is rewritten to a shared URI unique to this runtime, so
    the stores share one database while separate runtimes stay isolated.
    Anything else is returned unchanged.
    """
    if not is_plain_memory(path):
        return str(path)
    uri = shared_memory_uri(runtime_id)
    _hold_anchor(uri)
    return uri


def _hold_anchor(uri: str) -> None:
    """Keep a shared in-memory database alive for the process lifetime."""
    with _ANCHOR_LOCK:
        if uri not in _ANCHORS:
            _ANCHORS[uri] = sqlite3.connect(uri, uri=True, check_same_thread=False)


def release_anchor(uri: str) -> None:
    """Drop a shared in-memory database. Only the runtime that made it should
    call this, and only when it is finished — the data goes with it."""
    with _ANCHOR_LOCK:
        conn = _ANCHORS.pop(uri, None)
    if conn is not None:
        conn.close()


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    """Open a tuned connection.

    Accepts a shared-memory URI as well as a file path; `uri=True` is set when
    the string looks like one, so callers do not have to know which they hold.
    """
    target = str(path)
    conn = sqlite3.connect(target, timeout=30.0, uri=target.startswith("file:"))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    # A shared in-memory database has no WAL to write and rejects the pragma;
    # everything else below applies to both kinds.
    if not _is_memory_uri(target):
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA wal_autocheckpoint=1000')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _is_memory_uri(target: str) -> bool:
    return target == ":memory:" or "mode=memory" in target


__all__ = [
    "MultiStoreMemoryError",
    "connect_sqlite",
    "is_plain_memory",
    "release_anchor",
    "resolve_runtime_path",
    "shared_memory_uri",
]
