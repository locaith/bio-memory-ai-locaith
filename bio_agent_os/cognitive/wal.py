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

#: Where the log has to fall back to before pressure is declared over.
#:
#: Expressed as a fraction of the soft limit, because the point is the gap, not
#: the number. Without one, a log oscillating around the threshold resumes at
#: 63.9 MB and re-enters pressure at 64.1 MB, and the manager spends its life
#: checkpointing. Run 7 measured the shape of that: 81% of samples above the
#: soft limit, median 118 MB against a 64 MB limit.
DEFAULT_RESUME_FRACTION = 0.5

#: Minimum gap between two TRUNCATE attempts on the scheduled path.
#:
#: A blocked TRUNCATE costs the budget above and achieves nothing, so retrying
#: it every interval under sustained load is a checkpoint storm that taxes
#: every writer for no reclaim. Wait, then try again — the reader that blocked
#: it is usually gone within a cooldown.
DEFAULT_TRUNCATE_COOLDOWN_SECONDS = 20.0


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
    """Where the log sits *right now*. Instantaneous, no memory."""

    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"


class WALState(str, Enum):
    """Where the manager *believes* it is. Hysteretic, and the thing policy reads.

    Distinct from `WALLevel` on purpose. Level answers "how big is the file";
    state answers "am I in a reclaim campaign". They differ exactly in the
    hysteresis band — between the resume threshold and the soft limit — where
    the level reads OK but the state stays elevated because the campaign that
    got it there has not finished yet.

    NORMAL     log below the resume threshold. PASSIVE on schedule, no blocking.
    PRESSURE   log crossed the soft limit. Bounded TRUNCATE, on a cooldown.
    CRITICAL   log crossed the hard limit. Same action, plus the harness is
               expected to stop adding load until the state falls back.
    """

    NORMAL = "normal"
    PRESSURE = "pressure"
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
    # -- state machine, added after Run 7 --
    state: str = WALState.NORMAL.value
    resume_threshold_bytes: int = 0
    seconds_in_state: float = 0.0
    time_above_soft_seconds: float = 0.0
    time_above_hard_seconds: float = 0.0
    truncate_attempts: int = 0
    truncate_succeeded: int = 0
    truncate_busy: int = 0
    truncate_ineffective: int = 0
    # Run 8 ran without these and the post-mortem needed all four. The counters
    # existed in `metrics` the whole time; they simply were not carried into the
    # status object the canary samples, so every minute recorded a hole where
    # "how much did reclaim actually return" should have been.
    truncate_bytes_reclaimed: int = 0
    truncate_skipped_cooldown: int = 0
    truncate_skipped_reader: int = 0
    state_transitions: int = 0

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
            "state": self.state,
            "resume_threshold_mb": round(self.resume_threshold_bytes / 1048576, 1),
            "seconds_in_state": round(self.seconds_in_state, 1),
            "time_above_soft_seconds": round(self.time_above_soft_seconds, 1),
            "time_above_hard_seconds": round(self.time_above_hard_seconds, 1),
            "truncate_attempts": self.truncate_attempts,
            "truncate_succeeded": self.truncate_succeeded,
            "truncate_busy": self.truncate_busy,
            "truncate_ineffective": self.truncate_ineffective,
            "truncate_bytes_reclaimed": self.truncate_bytes_reclaimed,
            "truncate_mb_reclaimed": round(self.truncate_bytes_reclaimed / 1048576, 1),
            "truncate_skipped_cooldown": self.truncate_skipped_cooldown,
            "truncate_skipped_reader": self.truncate_skipped_reader,
            "state_transitions": self.state_transitions,
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
        resume_threshold_bytes: int | None = None,
        truncate_cooldown_seconds: float | None = None,
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
        self.resume_threshold_bytes = (
            resume_threshold_bytes if resume_threshold_bytes is not None
            else _env_bytes("BIO_AGENT_WAL_RESUME_MB", 0)
            or int(self.soft_limit_bytes * DEFAULT_RESUME_FRACTION)
        )
        # A resume threshold at or above the soft limit is not hysteresis, it is
        # a coin toss on every sample. Refuse it rather than pretend.
        if self.resume_threshold_bytes >= self.soft_limit_bytes:
            logger.warning(
                "resume threshold must sit below the soft limit; falling back to half",
                extra={"requested": self.resume_threshold_bytes,
                       "soft_limit": self.soft_limit_bytes},
            )
            self.resume_threshold_bytes = int(self.soft_limit_bytes * DEFAULT_RESUME_FRACTION)
        self.truncate_cooldown_seconds = (
            truncate_cooldown_seconds if truncate_cooldown_seconds is not None
            else _env_seconds("BIO_AGENT_WAL_TRUNCATE_COOLDOWN_SECONDS",
                              DEFAULT_TRUNCATE_COOLDOWN_SECONDS)
        )
        self._lock = threading.Lock()
        self._readers: dict[str, float] = {}
        self._last_run: float = 0.0
        self._last_result: CheckpointResult | None = None
        self._consecutive_busy = 0
        self._state = WALState.NORMAL
        self._state_since: float = time.time()
        self._last_seen: float = time.time()
        self._last_truncate_attempt: float = 0.0
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
            # -- state machine, added after Run 7 --
            "truncate_attempts": 0,
            "truncate_succeeded": 0,
            "truncate_busy": 0,
            "truncate_ineffective": 0,   # finished, blocked nobody, reclaimed nothing
            "truncate_bytes_reclaimed": 0,
            "truncate_skipped_cooldown": 0,
            "truncate_skipped_reader": 0,
            "state_transitions": 0,
            "time_above_soft_seconds": 0.0,
            "time_above_hard_seconds": 0.0,
            "by_state": {},
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
            state = self._state.value
            seconds_in_state = time.time() - self._state_since
            m = self.metrics
            above_soft = m["time_above_soft_seconds"]
            above_hard = m["time_above_hard_seconds"]
            t_att, t_ok = m["truncate_attempts"], m["truncate_succeeded"]
            t_busy, t_none = m["truncate_busy"], m["truncate_ineffective"]
            t_bytes = m["truncate_bytes_reclaimed"]
            t_cool, t_rdr = m["truncate_skipped_cooldown"], m["truncate_skipped_reader"]
            transitions = m["state_transitions"]
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
            state=state,
            resume_threshold_bytes=self.resume_threshold_bytes,
            seconds_in_state=seconds_in_state,
            time_above_soft_seconds=above_soft,
            time_above_hard_seconds=above_hard,
            truncate_attempts=t_att,
            truncate_succeeded=t_ok,
            truncate_busy=t_busy,
            truncate_ineffective=t_none,
            truncate_bytes_reclaimed=t_bytes,
            truncate_skipped_cooldown=t_cool,
            truncate_skipped_reader=t_rdr,
            state_transitions=transitions,
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

    def state(self) -> str:
        """The hysteretic state, without advancing it. Safe to poll."""
        with self._lock:
            return self._state.value

    def _advance_state(self, wal_bytes: int, now: float) -> WALState:
        """Fold the current size into the state, and bill the time since last look.

        Hysteresis lives here and nowhere else. Crossing the soft limit enters
        PRESSURE; crossing the hard limit enters CRITICAL; and the only way back
        to NORMAL is falling below the *resume* threshold, which sits strictly
        under the soft limit. Between resume and soft the state is held, which
        is the whole point: a log oscillating around its own trigger must not
        re-arm the campaign on every sample.
        """
        with self._lock:
            previous = self._state
            elapsed = max(0.0, now - self._last_seen)
            self._last_seen = now
            # Bill elapsed time against the size we last observed, not the new
            # one — the interval that just passed was spent at the old size.
            if previous is WALState.CRITICAL:
                self.metrics["time_above_hard_seconds"] += elapsed
                self.metrics["time_above_soft_seconds"] += elapsed
            elif previous is WALState.PRESSURE:
                self.metrics["time_above_soft_seconds"] += elapsed

            if wal_bytes >= self.hard_limit_bytes:
                nxt = WALState.CRITICAL
            elif wal_bytes < self.resume_threshold_bytes:
                nxt = WALState.NORMAL
            elif previous is WALState.NORMAL and wal_bytes >= self.soft_limit_bytes:
                nxt = WALState.PRESSURE
            else:
                nxt = previous  # inside the hysteresis band, or draining but not yet clear

            if nxt is not previous:
                self._state = nxt
                self._state_since = now
                self.metrics["state_transitions"] += 1
                self.metrics["by_state"][nxt.value] = (
                    self.metrics["by_state"].get(nxt.value, 0) + 1
                )
            return nxt

    def _truncate_on_cooldown(self, now: float) -> bool:
        with self._lock:
            return (now - self._last_truncate_attempt) < self.truncate_cooldown_seconds

    def maybe_checkpoint(self, *, force: bool = False) -> CheckpointResult | None:
        """The scheduled path. Returns None when nothing was due.

        A three-state machine, replacing the two-branch policy that Run 7
        disproved. The old policy only truncated above the *hard* limit, on the
        reasoning that the soft limit is an alerting threshold and the log would
        not run away below it. Run 7 measured otherwise: 81% of samples above a
        64 MB soft limit, a median of 118 MB, and then 220 MB to 612 MB in
        forty-two seconds. PASSIVE copies frames back but never returns the
        file, so a log that only ever gets PASSIVE has no downward force at all
        — it ratchets, and the hard limit is where the ratchet is noticed, not
        where it starts.

        NORMAL     PASSIVE on schedule. Nothing blocks, nothing waits.
        PRESSURE   bounded TRUNCATE, on a cooldown. This is the reclaim
                   campaign, and it is the branch that was missing.
        CRITICAL   the same bounded TRUNCATE, and `state()` reads CRITICAL so
                   the harness can stop adding load. The manager cannot throttle
                   producers itself and must not pretend to.

        A TRUNCATE that returns busy is **not** a success. It reclaimed nothing,
        it is counted as busy, and the cooldown defers the retry rather than
        letting the manager spin against a reader that is not going anywhere.
        """
        if not force and not self.due():
            return None

        now = time.time()
        _, wal_bytes, _ = self.file_sizes()
        state = self._advance_state(wal_bytes, now)

        if state is WALState.NORMAL:
            return self.checkpoint(CheckpointMode.PASSIVE)

        # -- PRESSURE and CRITICAL share an action; they differ in what the
        #    caller is expected to do about it.
        if state is WALState.CRITICAL:
            self.metrics["critical_events"] += 1
            logger.warning(
                "wal above hard limit; reclaim campaign running, stop adding load",
                extra={"wal_bytes": wal_bytes, "hard_limit": self.hard_limit_bytes,
                       "resume_threshold": self.resume_threshold_bytes},
            )
        else:
            self.metrics["warn_events"] += 1
            logger.warning(
                "wal above soft limit; reclaim campaign running",
                extra={"wal_bytes": wal_bytes, "soft_limit": self.soft_limit_bytes,
                       "resume_threshold": self.resume_threshold_bytes},
            )

        if self._truncate_on_cooldown(now):
            # Still worth copying frames back; just not worth blocking for.
            self.metrics["truncate_skipped_cooldown"] += 1
            return self.checkpoint(CheckpointMode.PASSIVE)

        # A registered long reader means the answer is already known: TRUNCATE
        # will be busy. Taking the write lock for the budget to learn that is
        # pure waste, so skip it and let the cooldown bring us back.
        #
        # This is an optimisation, not the safety mechanism. The budget is the
        # safety mechanism — registration is voluntary and mostly unused, and
        # Run 7 is the proof: `registered_readers` read 0 for every one of its
        # 382 samples while readers demonstrably existed. A guard that depends
        # on callers opting in cannot be load-bearing.
        reader_age = self.oldest_reader_age()
        if reader_age is not None:
            self.metrics["truncate_skipped_reader"] += 1
            logger.info(
                "reclaim deferred: a long reader is registered",
                extra={"oldest_reader_age_seconds": reader_age, "wal_bytes": wal_bytes},
            )
            return self.checkpoint(CheckpointMode.PASSIVE)

        with self._lock:
            self._last_truncate_attempt = now
            self.metrics["truncate_attempts"] += 1

        result = self.checkpoint(
            CheckpointMode.TRUNCATE, allow_blocking=True,
            busy_timeout_ms=SCHEDULED_TRUNCATE_BUDGET_MS,
        )
        with self._lock:
            if result.busy or not result.ok:
                self.metrics["truncate_busy"] += 1
            elif result.reclaimed_bytes > 0:
                self.metrics["truncate_succeeded"] += 1
                self.metrics["truncate_bytes_reclaimed"] += result.reclaimed_bytes
            else:
                # Finished, nothing blocked it, and the file did not shrink.
                # Distinct from busy and worth its own counter: it means the log
                # is genuinely all live frames, not that a reader is in the way.
                self.metrics["truncate_ineffective"] += 1
        return result

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
        # A blocked reclaim is not a failure, but it is not a success either, and
        # silence would let the campaign look like it is working.
        if status.truncate_busy > 0 and status.truncate_succeeded == 0:
            out.append({
                "severity": "WARN", "code": "WAL_CHECKPOINT_BUSY",
                "truncate_attempts": status.truncate_attempts,
                "truncate_busy": status.truncate_busy,
                "wal_mb": round(status.wal_bytes / 1048576, 1),
                "action": "every reclaim attempt was blocked; the log has no downward "
                          "force until a reader releases",
            })
        # Nothing blocked it, nothing came back: the log really is all live frames.
        if status.truncate_ineffective >= 3 and status.truncate_succeeded == 0:
            out.append({
                "severity": "WARN", "code": "WAL_RECLAIM_INEFFECTIVE",
                "truncate_ineffective": status.truncate_ineffective,
                "wal_mb": round(status.wal_bytes / 1048576, 1),
                "action": "truncate completed unblocked but reclaimed nothing; look for "
                          "a writer producing frames faster than they can be retired",
            })
        if status.state == WALState.CRITICAL.value:
            out.append({
                "severity": "CRITICAL", "code": "WAL_RECLAIM_CAMPAIGN",
                "state": status.state,
                "seconds_in_state": round(status.seconds_in_state, 1),
                "resume_threshold_mb": round(status.resume_threshold_bytes / 1048576, 1),
                "action": "stop adding load until the log falls below the resume threshold",
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
    "DEFAULT_RESUME_FRACTION",
    "DEFAULT_SOFT_LIMIT_MB",
    "DEFAULT_TRUNCATE_COOLDOWN_SECONDS",
    "SCHEDULED_TRUNCATE_BUDGET_MS",
    "CheckpointMode",
    "CheckpointResult",
    "WALCheckpointManager",
    "WALLevel",
    "WALState",
    "WALStatus",
    "manager_for",
]
