"""WAL checkpointing that can be watched, and that never guesses.

The soak measured the write-ahead log growing to 500 MB over an hour — 46% of
the database — and returning to zero only when the last connection closed. A
passive checkpoint can copy frames back only as far as the oldest reader's
snapshot, and with connections permanently open it rarely gets to reset the
file at all. Nothing was lost, but an operator watching disk has to count it,
and a process that never closes never reclaims it.

Two things this module refuses to do.

**It does not checkpoint on the hot path.** `maybe_checkpoint()` returns
immediately unless the interval has elapsed, and the mode it runs by default
is PASSIVE, which by definition yields to any reader rather than waiting for
one.

**It does not report a number it cannot measure.** SQLite tells us whether a
checkpoint was blocked (`busy`), how many frames are in the log, and how many
were moved. It does not expose how old the oldest reader is. That field is
populated from readers registered with `note_reader`, and is `None` — not
zero — when nothing has registered. A zero would read as "no reader is
holding anything back", which is the opposite of not knowing.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("bio_agent_os.wal")

#: Defaults, overridable by environment. Chosen from the soak: the WAL passed
#: 256 MB around the half-hour mark and 500 MB by the end, so the soft limit
#: is where an operator should start looking and the hard limit is where the
#: measured run ended up.
DEFAULT_SOFT_LIMIT_MB = 256
DEFAULT_HARD_LIMIT_MB = 512
DEFAULT_INTERVAL_SECONDS = 60.0

#: How long a *scheduled* TRUNCATE may wait for readers before giving up and
#: leaving the job to the next interval.
#:
#: Run 6 measured the unbounded version. The checkpoint that finally landed
#: reported `wal_frames=0, frames_checkpointed=0` and took 23,236 ms: it moved
#: nothing and spent every one of those milliseconds waiting. Inside that same
#: window a producer's observe() reached 23,457 ms and one write died on its
#: own 10-second busy timeout — 1 failure in 465,738, but a fatal SLO breach
#: that stopped a 24-hour run at 2.9 hours.
#:
#: Since the work is zero and the wait is everything, a budget costs nothing
#: and removes the stall: either a natural gap in the readers appears within
#: the budget, or the log stays large for another interval. A WAL that is
#: 130 MB is an operator's problem; a checkpoint that freezes every writer for
#: 23 seconds is everyone's.
SCHEDULED_TRUNCATE_BUDGET_MS = 250


class CheckpointMode(str, Enum):
    """SQLite's four modes, in increasing order of how much they block.

    PASSIVE never blocks: it copies what it can and gives up on the rest.
    FULL waits for writers. RESTART additionally forces the next writer to
    start a new WAL. TRUNCATE does everything RESTART does and then shrinks
    the file to zero — and blocks until every reader has finished.
    """

    PASSIVE = "PASSIVE"
    FULL = "FULL"
    RESTART = "RESTART"
    TRUNCATE = "TRUNCATE"

    @property
    def blocks_readers(self) -> bool:
        return self in (CheckpointMode.RESTART, CheckpointMode.TRUNCATE)


class WALLevel(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class CheckpointResult:
    """What one checkpoint attempt actually did.

    `busy` is SQLite's own answer, not an inference: 1 means a reader or
    writer prevented the checkpoint from finishing. `frames_checkpointed`
    of -1 means SQLite declined to report, which happens when the database
    is not in WAL mode.
    """

    mode: str
    busy: bool
    wal_frames: int
    frames_checkpointed: int
    duration_ms: float
    wal_bytes_before: int
    wal_bytes_after: int
    error: str | None = None
    refused: str | None = None

    @property
    def reclaimed_bytes(self) -> int:
        return max(0, self.wal_bytes_before - self.wal_bytes_after)

    @property
    def ok(self) -> bool:
        return self.error is None and self.refused is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "busy": self.busy,
            "wal_frames": self.wal_frames,
            "frames_checkpointed": self.frames_checkpointed,
            "duration_ms": round(self.duration_ms, 3),
            "wal_bytes_before": self.wal_bytes_before,
            "wal_bytes_after": self.wal_bytes_after,
            "reclaimed_bytes": self.reclaimed_bytes,
            "error": self.error,
            "refused": self.refused,
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class WALStatus:
    db_path: str
    journal_mode: str
    wal_bytes: int
    shm_bytes: int
    db_bytes: int
    autocheckpoint_pages: int
    page_size: int
    soft_limit_bytes: int
    hard_limit_bytes: int
    level: str
    registered_readers: int
    oldest_reader_age_seconds: float | None
    consecutive_busy_checkpoints: int
    last_checkpoint: dict[str, Any] | None

    @property
    def wal_pct_of_database(self) -> float:
        return round(100 * self.wal_bytes / self.db_bytes, 1) if self.db_bytes else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "journal_mode": self.journal_mode,
            "wal_bytes": self.wal_bytes,
            "wal_mb": round(self.wal_bytes / 1048576, 1),
            "shm_bytes": self.shm_bytes,
            "db_bytes": self.db_bytes,
            "db_mb": round(self.db_bytes / 1048576, 1),
            "wal_pct_of_database": self.wal_pct_of_database,
            "autocheckpoint_pages": self.autocheckpoint_pages,
            "page_size": self.page_size,
            "soft_limit_mb": round(self.soft_limit_bytes / 1048576, 1),
            "hard_limit_mb": round(self.hard_limit_bytes / 1048576, 1),
            "level": self.level,
            "registered_readers": self.registered_readers,
            "oldest_reader_age_seconds": self.oldest_reader_age_seconds,
            "consecutive_busy_checkpoints": self.consecutive_busy_checkpoints,
            "last_checkpoint": self.last_checkpoint,
        }

    def render(self, width: int = 66) -> str:
        marks = {WALLevel.OK.value: " ", WALLevel.WARN.value: "!",
                 WALLevel.CRITICAL.value: "!!"}
        lines = [
            "=" * width,
            "  WAL STATUS",
            "=" * width,
            f"  database                  {self.db_path}",
            f"  journal mode              {self.journal_mode}",
            f"  database size             {self.db_bytes / 1048576:,.1f} MB",
            f"  wal size                  {self.wal_bytes / 1048576:,.1f} MB "
            f"({self.wal_pct_of_database}% of database) {marks.get(self.level, '')}",
            f"  shared-memory index       {self.shm_bytes / 1048576:,.2f} MB",
            f"  autocheckpoint            {self.autocheckpoint_pages} pages "
            f"({self.autocheckpoint_pages * self.page_size / 1048576:.1f} MB)",
            f"  soft / hard limit         {self.soft_limit_bytes / 1048576:,.0f} MB"
            f" / {self.hard_limit_bytes / 1048576:,.0f} MB",
            f"  level                     {self.level.upper()}",
            f"  registered readers        {self.registered_readers}",
            f"  oldest reader age         "
            f"{'not tracked' if self.oldest_reader_age_seconds is None else f'{self.oldest_reader_age_seconds:.1f}s'}",
            f"  consecutive busy          {self.consecutive_busy_checkpoints}",
        ]
        if self.last_checkpoint:
            last = self.last_checkpoint
            lines.append(
                f"  last checkpoint           {last['mode']} "
                f"busy={last['busy']} frames={last['frames_checkpointed']}/"
                f"{last['wal_frames']} in {last['duration_ms']}ms"
            )
        lines.append("=" * width)
        return "\n".join(lines)


def _env_bytes(name: str, default_mb: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(float(raw)) * 1048576 if raw else default_mb * 1048576
    except ValueError:
        logger.warning("ignoring unparsable %s=%r", name, raw)
        return default_mb * 1048576


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("ignoring unparsable %s=%r", name, raw)
        return default


class WALCheckpointManager:
    """Watches the WAL and checkpoints it on a schedule, never on the hot path.

    Holds no connection of its own for the checkpoint: it is given one, so it
    checkpoints the same database its caller is using rather than opening a
    connection whose read snapshot would itself block the checkpoint.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        db_path: str | Path,
        *,
        soft_limit_bytes: int | None = None,
        hard_limit_bytes: int | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.conn = conn
        self.db_path = str(db_path)
        self.soft_limit_bytes = (
            soft_limit_bytes if soft_limit_bytes is not None
            else _env_bytes("BIO_AGENT_WAL_SOFT_LIMIT_MB", DEFAULT_SOFT_LIMIT_MB)
        )
        self.hard_limit_bytes = (
            hard_limit_bytes if hard_limit_bytes is not None
            else _env_bytes("BIO_AGENT_WAL_HARD_LIMIT_MB", DEFAULT_HARD_LIMIT_MB)
        )
        self.interval_seconds = (
            interval_seconds if interval_seconds is not None
            else _env_seconds("BIO_AGENT_WAL_CHECKPOINT_INTERVAL_SECONDS",
                              DEFAULT_INTERVAL_SECONDS)
        )
        self._lock = threading.Lock()
        self._readers: dict[str, float] = {}
        self._last_run: float = 0.0
        self._last_result: CheckpointResult | None = None
        self._consecutive_busy = 0
        self.metrics: dict[str, Any] = {
            "checkpoints_attempted": 0,
            "checkpoints_busy": 0,
            "checkpoints_refused": 0,
            "checkpoints_failed": 0,
            "frames_checkpointed": 0,
            "bytes_reclaimed": 0,
            "total_duration_ms": 0.0,
            "warn_events": 0,
            "critical_events": 0,
            "by_mode": {},
        }

    # -- readers -----------------------------------------------------------

    def note_reader(self, reader_id: str) -> None:
        """Register a long-lived read snapshot so its age can be reported.

        Optional. SQLite has no API for this, so an unregistered reader is
        invisible here and shows up only as a busy checkpoint.
        """
        with self._lock:
            self._readers.setdefault(reader_id, time.time())

    def release_reader(self, reader_id: str) -> None:
        with self._lock:
            self._readers.pop(reader_id, None)

    def oldest_reader_age(self) -> float | None:
        with self._lock:
            if not self._readers:
                return None
            return round(time.time() - min(self._readers.values()), 3)

    # -- observation -------------------------------------------------------

    def file_sizes(self) -> tuple[int, int, int]:
        """db, wal, shm — in bytes, zero for whatever does not exist."""
        base = Path(self.db_path)
        def _size(suffix: str) -> int:
            path = Path(str(base) + suffix)
            try:
                return path.stat().st_size
            except OSError:
                return 0
        return _size(""), _size("-wal"), _size("-shm")

    def level(self, wal_bytes: int | None = None) -> str:
        if wal_bytes is None:
            _, wal_bytes, _ = self.file_sizes()
        if wal_bytes >= self.hard_limit_bytes:
            return WALLevel.CRITICAL.value
        if wal_bytes >= self.soft_limit_bytes:
            return WALLevel.WARN.value
        return WALLevel.OK.value

    def status(self) -> WALStatus:
        db_bytes, wal_bytes, shm_bytes = self.file_sizes()
        journal_mode = "unknown"
        autocheckpoint = 0
        page_size = 0
        try:
            journal_mode = str(self.conn.execute("PRAGMA journal_mode").fetchone()[0])
            autocheckpoint = int(self.conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0])
            page_size = int(self.conn.execute("PRAGMA page_size").fetchone()[0])
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            logger.warning("could not read WAL pragmas: %s", exc)
        with self._lock:
            readers = len(self._readers)
            consecutive_busy = self._consecutive_busy
            last = self._last_result.as_dict() if self._last_result else None
        return WALStatus(
            db_path=self.db_path,
            journal_mode=journal_mode,
            wal_bytes=wal_bytes,
            shm_bytes=shm_bytes,
            db_bytes=db_bytes,
            autocheckpoint_pages=autocheckpoint,
            page_size=page_size,
            soft_limit_bytes=self.soft_limit_bytes,
            hard_limit_bytes=self.hard_limit_bytes,
            level=self.level(wal_bytes),
            registered_readers=readers,
            oldest_reader_age_seconds=self.oldest_reader_age(),
            consecutive_busy_checkpoints=consecutive_busy,
            last_checkpoint=last,
        )

    # -- action ------------------------------------------------------------

    def checkpoint(
        self,
        mode: CheckpointMode | str = CheckpointMode.PASSIVE,
        *,
        allow_blocking: bool = False,
        busy_timeout_ms: int | None = None,
    ) -> CheckpointResult:
        """Run one checkpoint.

        A blocking mode is refused unless `allow_blocking` is set, because
        RESTART and TRUNCATE wait for readers and a scheduled job that waits
        on a reader is a scheduled job that stalls the process it runs in.
        The CLI sets it; the periodic policy does not, except for RESTART
        above the hard limit with no reader registered.

        `busy_timeout_ms` bounds that wait for this call only and restores the
        connection's own setting afterwards. Without it a blocked TRUNCATE
        inherits the runtime's 30-second timeout, which is right for an
        append but far too long for an operator holding a terminal — and for
        anything that has to answer promptly, "blocked" is the answer.
        """
        # `str()` on a str-Enum member gives "CheckpointMode.PASSIVE" on 3.11,
        # not "PASSIVE", so an already-typed argument must not go through it.
        mode = mode if isinstance(mode, CheckpointMode) else CheckpointMode(str(mode).upper())
        db_before, wal_before, _ = self.file_sizes()

        if mode.blocks_readers and not allow_blocking:
            result = CheckpointResult(
                mode=mode.value, busy=False, wal_frames=-1, frames_checkpointed=-1,
                duration_ms=0.0, wal_bytes_before=wal_before, wal_bytes_after=wal_before,
                refused=f"{mode.value} blocks readers; pass allow_blocking to run it",
            )
            self._record(result)
            return result

        previous_timeout: int | None = None
        if busy_timeout_ms is not None:
            try:
                previous_timeout = int(self.conn.execute("PRAGMA busy_timeout").fetchone()[0])
                self.conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            except sqlite3.Error:  # pragma: no cover - defensive
                previous_timeout = None

        started = time.perf_counter()
        try:
            row = self.conn.execute(f"PRAGMA wal_checkpoint({mode.value})").fetchone()
            busy, frames, checkpointed = (int(row[0]), int(row[1]), int(row[2])) if row else (1, -1, -1)
            error = None
        except sqlite3.Error as exc:
            busy, frames, checkpointed, error = 1, -1, -1, f"{type(exc).__name__}: {exc}"
        finally:
            if previous_timeout is not None:
                try:
                    self.conn.execute(f"PRAGMA busy_timeout={previous_timeout}")
                except sqlite3.Error:  # pragma: no cover - defensive
                    pass
        duration_ms = (time.perf_counter() - started) * 1000

        _, wal_after, _ = self.file_sizes()
        result = CheckpointResult(
            mode=mode.value, busy=bool(busy), wal_frames=frames,
            frames_checkpointed=checkpointed, duration_ms=duration_ms,
            wal_bytes_before=wal_before, wal_bytes_after=wal_after, error=error,
        )
        self._record(result)
        if result.busy:
            logger.info(
                "wal checkpoint blocked by a reader",
                extra={"mode": mode.value, "wal_frames": frames},
            )
        return result

    def _record(self, result: CheckpointResult) -> None:
        with self._lock:
            self._last_result = result
            self._last_run = time.time()
            self.metrics["checkpoints_attempted"] += 1
            self.metrics["by_mode"][result.mode] = (
                self.metrics["by_mode"].get(result.mode, 0) + 1
            )
            self.metrics["total_duration_ms"] += result.duration_ms
            if result.refused:
                self.metrics["checkpoints_refused"] += 1
            elif result.error:
                self.metrics["checkpoints_failed"] += 1
            if result.busy:
                self.metrics["checkpoints_busy"] += 1
                self._consecutive_busy += 1
            elif result.ok:
                self._consecutive_busy = 0
            if result.frames_checkpointed > 0:
                self.metrics["frames_checkpointed"] += result.frames_checkpointed
            self.metrics["bytes_reclaimed"] += result.reclaimed_bytes

    # -- policy ------------------------------------------------------------

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            return (now - self._last_run) >= self.interval_seconds

    def maybe_checkpoint(self, *, force: bool = False) -> CheckpointResult | None:
        """The scheduled path. Returns None when nothing was due.

        below soft limit : PASSIVE
        above soft limit : PASSIVE, and a warning
        above hard limit : TRUNCATE when no reader is registered, otherwise
                           PASSIVE and a critical warning.

        TRUNCATE is reached only on the branch that had already chosen to
        block. It waits for readers exactly as RESTART does, so on that branch
        it costs nothing extra, and it is the only mode that returns the file
        to the filesystem: PASSIVE and RESTART reset where SQLite writes next
        and leave the file at its high-water mark. The staging canary showed
        what that costs - a fully checkpointed WAL, (0, 27033, 27033) with no
        reader blocking, still holding 106 MB, and a high-water mark that only
        ever rose until it crossed this limit and stopped the run.

        Below the hard limit nothing blocks, which is the property that
        matters: a background job must never wait on readers unless the
        alternative is running out of disk.

        And when it does wait, it waits on a clock. The TRUNCATE here is
        capped at SCHEDULED_TRUNCATE_BUDGET_MS; a blocked one returns busy and
        the next interval tries again. Run 6 is why: unbounded, it held every
        writer for 23 seconds to move zero frames.
        """
        if not force and not self.due():
            return None

        _, wal_bytes, _ = self.file_sizes()
        level = self.level(wal_bytes)
        readers = self.oldest_reader_age()

        if level == WALLevel.CRITICAL.value:
            self.metrics["critical_events"] += 1
            if readers is None:
                logger.warning(
                    "wal above hard limit; truncating the log",
                    extra={"wal_bytes": wal_bytes, "hard_limit": self.hard_limit_bytes},
                )
                return self.checkpoint(
                    CheckpointMode.TRUNCATE, allow_blocking=True,
                    busy_timeout_ms=SCHEDULED_TRUNCATE_BUDGET_MS,
                )
            logger.warning(
                "wal above hard limit but a reader is registered; passive only",
                extra={"wal_bytes": wal_bytes, "oldest_reader_age_seconds": readers},
            )
            return self.checkpoint(CheckpointMode.PASSIVE)

        if level == WALLevel.WARN.value:
            self.metrics["warn_events"] += 1
            logger.warning(
                "wal above soft limit",
                extra={"wal_bytes": wal_bytes, "soft_limit": self.soft_limit_bytes},
            )
        return self.checkpoint(CheckpointMode.PASSIVE)

    def alerts(self) -> list[dict[str, Any]]:
        """What a monitor should page on, with the numbers behind it."""
        status = self.status()
        out: list[dict[str, Any]] = []
        if status.level == WALLevel.CRITICAL.value:
            out.append({
                "severity": "CRITICAL", "code": "WAL_ABOVE_HARD_LIMIT",
                "wal_mb": round(status.wal_bytes / 1048576, 1),
                "limit_mb": round(status.hard_limit_bytes / 1048576, 1),
                "action": "run `storage checkpoint --mode restart`, or truncate "
                          "in maintenance mode once readers are drained",
            })
        elif status.level == WALLevel.WARN.value:
            out.append({
                "severity": "WARN", "code": "WAL_ABOVE_SOFT_LIMIT",
                "wal_mb": round(status.wal_bytes / 1048576, 1),
                "limit_mb": round(status.soft_limit_bytes / 1048576, 1),
                "action": "passive checkpointing continues; watch for a long-lived reader",
            })
        if status.consecutive_busy_checkpoints >= 5:
            out.append({
                "severity": "WARN", "code": "WAL_CHECKPOINT_REPEATEDLY_BLOCKED",
                "consecutive_busy": status.consecutive_busy_checkpoints,
                "oldest_reader_age_seconds": status.oldest_reader_age_seconds,
                "action": "a long-lived read snapshot is preventing reclamation",
            })
        if status.journal_mode.lower() != "wal":
            out.append({
                "severity": "INFO", "code": "NOT_IN_WAL_MODE",
                "journal_mode": status.journal_mode,
                "action": "checkpointing does not apply outside WAL mode",
            })
        return out

    def metrics_snapshot(self) -> dict[str, Any]:
        status = self.status()
        with self._lock:
            metrics = dict(self.metrics)
            metrics["by_mode"] = dict(self.metrics["by_mode"])
        metrics.update({
            "wal_bytes": status.wal_bytes,
            "db_bytes": status.db_bytes,
            "wal_pct_of_database": status.wal_pct_of_database,
            "level": status.level,
            "registered_readers": status.registered_readers,
            "oldest_reader_age_seconds": status.oldest_reader_age_seconds,
            "consecutive_busy_checkpoints": status.consecutive_busy_checkpoints,
            "mean_checkpoint_ms": (
                round(metrics["total_duration_ms"] / metrics["checkpoints_attempted"], 3)
                if metrics["checkpoints_attempted"] else 0.0
            ),
        })
        return metrics


def manager_for(memory_os: Any, **kwargs: Any) -> WALCheckpointManager:
    """Wire a manager to a runtime's event connection.

    The event store's connection is the one the append path commits on, so it
    is the one whose WAL matters most and the one least likely to be sitting
    inside a long read.
    """
    return WALCheckpointManager(memory_os.events.conn, memory_os.db_path, **kwargs)


__all__ = [
    "DEFAULT_HARD_LIMIT_MB",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_SOFT_LIMIT_MB",
    "SCHEDULED_TRUNCATE_BUDGET_MS",
    "CheckpointMode",
    "CheckpointResult",
    "WALCheckpointManager",
    "WALLevel",
    "WALStatus",
    "manager_for",
]
