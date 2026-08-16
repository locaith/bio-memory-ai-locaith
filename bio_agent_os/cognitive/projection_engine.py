"""Replay engine — turning owed projections back into a known state.

Separate from `SQLiteEventStore` and `MemoryOS` on purpose. The event store's
job is to append and never change; the facade's job is to serve callers.
Deciding what is missing, what may be rebuilt and in what order is a third
concern, and putting it in either of the other two is how append paths grow
slow and hard to reason about.

Three things this engine will not do:

* **Delete a raw event.** Not during repair, not during rebuild, not ever.
* **Rebuild a projection marked unreplayable.** A checkpoint pins a specific
  memory version; regenerating it from the event would silently repoint it at
  a newer one. That is reported, never done automatically.
* **Guess.** An event with no outbox row may be an orphan or a deliberate
  `observe()` with no projection. Both are reported as what they are.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .outbox import JobStatus, ProjectionJob, ProjectionOutbox, projection_key
from .tombstones import buried_among
from .projection_registry import (
    DependencyState,
    ProjectionType,
    current_version,
    dependencies_of,
    spec,
    topological_order,
)


class ReplayReason(str, Enum):
    """Why a job was selected for replay."""

    MISSING_OUTBOX = "missing_outbox"        # event exists, nothing owed recorded
    PENDING = "pending"                      # queued, never processed
    STALE_LEASE = "stale_lease"              # claimed by a worker that went away
    DEAD_LETTER = "dead_letter"              # exhausted retries
    VERSION_DRIFT = "version_drift"          # completed at an older version
    ORPHAN_PROJECTION = "orphan_projection"  # projection with no event


@dataclass(slots=True)
class ReplayCandidate:
    event_id: str
    tenant_id: str
    projection_type: str
    projection_version: int
    reason: str
    dependency_state: str = DependencyState.READY.value
    replayable: bool = True
    detail: str = ""

    @property
    def actionable(self) -> bool:
        return self.replayable and self.dependency_state == DependencyState.READY.value


@dataclass(slots=True)
class ReplayReport:
    """What a replay found, and what it did — or would do."""

    dry_run: bool = True
    scanned_events: int = 0
    candidates: list[ReplayCandidate] = field(default_factory=list)
    enqueued: int = 0
    reset: int = 0
    skipped_unreplayable: int = 0
    skipped_dependency: int = 0
    #: Events a deletion said must never be materialised again. Counted and
    #: printed rather than silently dropped — a recovery that quietly declines
    #: to restore something is indistinguishable from one that failed, and the
    #: operator running it needs to know which.
    skipped_tombstoned: int = 0
    errors: list[str] = field(default_factory=list)

    def by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.candidates:
            out[c.reason] = out.get(c.reason, 0) + 1
        return out

    def render(self, width: int = 78) -> str:
        mode = "DRY RUN — nothing was changed" if self.dry_run else "APPLIED"
        lines = [
            "=" * width,
            f"  PROJECTION REPLAY — {mode}",
            "=" * width,
            f"  events scanned        : {self.scanned_events:>8,}",
            f"  candidates found      : {len(self.candidates):>8,}",
            f"  enqueued              : {self.enqueued:>8,}",
            f"  reset for retry       : {self.reset:>8,}",
            f"  held: dependency      : {self.skipped_dependency:>8,}",
            f"  held: not replayable  : {self.skipped_unreplayable:>8,}",
            f"  held: tombstoned      : {self.skipped_tombstoned:>8,}",
        ]
        if self.candidates:
            lines.append("")
            lines.append("  BY REASON")
            for reason, n in sorted(self.by_reason().items(), key=lambda x: -x[1]):
                lines.append(f"    {reason:<22} {n:>6,}")
        if self.skipped_unreplayable:
            lines.append("")
            lines.append("  NOT REBUILT AUTOMATICALLY — these pin a specific version:")
            for c in self.candidates:
                if not c.replayable:
                    lines.append(f"    {c.projection_type:<22} event {c.event_id[:12]}")
        if self.errors:
            lines.append("")
            lines.append("  ERRORS")
            for e in self.errors[:10]:
                lines.append(f"    {e}")
        lines.append("=" * width)
        return "\n".join(lines)


class ProjectionReplayEngine:
    """Finds owed projections and requeues them. Never applies them itself —
    that is the worker's job, and keeping the two apart means a replay can be
    inspected before anything runs."""

    def __init__(self, conn: sqlite3.Connection, outbox: ProjectionOutbox | None = None) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.outbox = outbox or ProjectionOutbox(conn)

    # -- discovery ---------------------------------------------------------

    def _events(
        self, *, event_id: str | None = None, tenant_id: str | None = None, limit: int | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT event_id, tenant_id, workspace_id FROM cognitive_events"
        clauses, params = [], []
        if event_id:
            clauses.append("event_id = ?")
            params.append(event_id)
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY observed_at"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql, params).fetchall()

    def _dependency_state(self, event_id: str, projection_type: str) -> str:
        """Whether this job's parents have completed."""
        parents = dependencies_of(projection_type)
        if not parents:
            return DependencyState.READY.value
        for parent in parents:
            row = self.conn.execute(
                "SELECT status FROM projection_outbox "
                "WHERE event_id=? AND projection_type=? "
                "ORDER BY created_at DESC LIMIT 1",
                (event_id, parent.value),
            ).fetchone()
            if row is None:
                return DependencyState.PENDING_DEPENDENCY.value
            if row["status"] == JobStatus.DEAD_LETTER.value:
                # The parent can never complete, so this never becomes ready.
                return DependencyState.BLOCKED.value
            if row["status"] not in (JobStatus.COMPLETED.value, JobStatus.SKIPPED.value):
                return DependencyState.PENDING_DEPENDENCY.value
        return DependencyState.READY.value

    def scan(
        self,
        *,
        event_id: str | None = None,
        tenant_id: str | None = None,
        projection_types: tuple[str, ...] | None = None,
        include_version_drift: bool = True,
        lease_seconds: float = 300.0,
        limit: int | None = None,
    ) -> ReplayReport:
        """Find everything that is owed. Changes nothing."""
        report = ReplayReport(dry_run=True)
        wanted = projection_types or tuple(t.value for t in topological_order())
        rows = self._events(event_id=event_id, tenant_id=tenant_id, limit=limit)
        report.scanned_events = len(rows)

        for row in rows:
            for ptype in wanted:
                try:
                    ptype_spec = spec(ptype)
                except (KeyError, ValueError):
                    report.errors.append(f"unknown projection type: {ptype}")
                    continue
                version = ptype_spec.version
                job_row = self.conn.execute(
                    "SELECT status, projection_version, locked_at, attempts "
                    "FROM projection_outbox WHERE event_id=? AND projection_type=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (row["event_id"], ptype),
                ).fetchone()

                reason: str | None = None
                if job_row is None:
                    # Only the default projection is owed by an ordinary event.
                    # Others are opt-in, so their absence is not an orphan.
                    if ptype == ProjectionType.COGNITIVE_MEMORY.value:
                        reason = ReplayReason.MISSING_OUTBOX.value
                elif job_row["status"] == JobStatus.PENDING.value:
                    reason = ReplayReason.PENDING.value
                elif job_row["status"] == JobStatus.DEAD_LETTER.value:
                    reason = ReplayReason.DEAD_LETTER.value
                elif job_row["status"] == JobStatus.IN_PROGRESS.value:
                    locked_at = job_row["locked_at"]
                    # `>=`, not `>`. The Windows clock ticks at roughly 15 ms,
                    # so a claim and a check inside one tick give an elapsed of
                    # exactly 0.0 — and a zero-length lease would then never
                    # expire. A lease whose duration has elapsed is expired.
                    if locked_at is None or (_now() - float(locked_at)) >= lease_seconds:
                        reason = ReplayReason.STALE_LEASE.value
                elif (
                    include_version_drift
                    and job_row["status"] == JobStatus.COMPLETED.value
                    and int(job_row["projection_version"]) < version
                ):
                    reason = ReplayReason.VERSION_DRIFT.value

                if reason is None:
                    continue

                report.candidates.append(
                    ReplayCandidate(
                        event_id=row["event_id"],
                        tenant_id=row["tenant_id"],
                        projection_type=ptype,
                        projection_version=version,
                        reason=reason,
                        dependency_state=self._dependency_state(row["event_id"], ptype),
                        replayable=ptype_spec.replayable,
                        detail=ptype_spec.target_store,
                    )
                )
        return report

    def orphan_projections(self) -> list[dict[str, Any]]:
        """Outbox rows whose event no longer exists.

        Should be impossible — events are append-only — so anything here means
        the database was edited outside the API or restored inconsistently.
        Reported, never repaired automatically.
        """
        rows = self.conn.execute(
            "SELECT o.job_id, o.event_id, o.projection_type, o.tenant_id "
            "FROM projection_outbox o "
            "WHERE NOT EXISTS (SELECT 1 FROM cognitive_events e WHERE e.event_id = o.event_id)"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- action ------------------------------------------------------------

    def replay(
        self,
        *,
        event_id: str | None = None,
        tenant_id: str | None = None,
        projection_types: tuple[str, ...] | None = None,
        dry_run: bool = True,
        lease_seconds: float = 300.0,
        limit: int | None = None,
    ) -> ReplayReport:
        """Requeue what is owed.

        `dry_run` defaults to True. A recovery tool whose default is to act is
        a recovery tool that gets run by accident.
        """
        report = self.scan(
            event_id=event_id,
            tenant_id=tenant_id,
            projection_types=projection_types,
            lease_seconds=lease_seconds,
            limit=limit,
        )
        report.dry_run = dry_run

        # One query for the whole scan rather than one per candidate: this runs
        # on every replay of every event, and a check that costs more than the
        # work it guards is a check somebody eventually turns off.
        tombstoned = buried_among(
            self.conn, {c.event_id for c in report.candidates})

        for candidate in report.candidates:
            if not candidate.replayable:
                report.skipped_unreplayable += 1
                continue
            # Checked before dependencies and before `dry_run`, so a dry run
            # reports the same decision the real one would make. A preview that
            # disagrees with the act is worse than no preview.
            if candidate.event_id in tombstoned:
                report.skipped_tombstoned += 1
                continue
            if candidate.dependency_state != DependencyState.READY.value:
                report.skipped_dependency += 1
                continue
            if dry_run:
                continue
            try:
                if candidate.reason == ReplayReason.MISSING_OUTBOX.value:
                    self.outbox.enqueue(
                        ProjectionJob(
                            event_id=candidate.event_id,
                            projection_type=candidate.projection_type,
                            tenant_id=candidate.tenant_id,
                            projection_version=candidate.projection_version,
                        ),
                        commit=True,
                    )
                    report.enqueued += 1
                else:
                    self._reset(candidate)
                    report.reset += 1
            except sqlite3.Error as exc:
                report.errors.append(f"{candidate.event_id}/{candidate.projection_type}: {exc}")
        return report

    def _reset(self, candidate: ReplayCandidate) -> None:
        """Return a job to PENDING so a worker will pick it up again.

        `attempts` is zeroed because a replay is a deliberate human decision,
        not another automatic retry — carrying the old count forward would
        dead-letter it again almost immediately.
        """
        self.conn.execute(
            "UPDATE projection_outbox "
            "SET status=?, attempts=0, available_at=?, locked_by=NULL, locked_at=NULL, "
            "    last_error=NULL, completed_at=NULL "
            "WHERE event_id=? AND projection_type=?",
            (
                JobStatus.PENDING.value, _now(),
                candidate.event_id, candidate.projection_type,
            ),
        )
        self.conn.commit()

    def rebuild_version(
        self, projection_type: str, *, tenant_id: str | None = None, dry_run: bool = True
    ) -> ReplayReport:
        """Enqueue every event for this type at the registry's current version.

        Used after projection logic changes. The new version produces a
        different idempotency key, so the rebuilt rows coexist with the old
        ones rather than colliding — nothing is destroyed to make room.
        """
        version = current_version(projection_type)
        report = ReplayReport(dry_run=dry_run)
        rows = self._events(tenant_id=tenant_id)
        report.scanned_events = len(rows)
        replayable = spec(projection_type).replayable
        # A rebuild is the operation that resurrected every deleted memory in
        # the 15/08 reproduction: it walks the whole log by design, so it walks
        # straight over the deletions too unless it is told not to.
        tombstoned = buried_among(self.conn, {r["event_id"] for r in rows})

        for row in rows:
            if row["event_id"] in tombstoned:
                report.skipped_tombstoned += 1
                continue
            existing = self.conn.execute(
                "SELECT 1 FROM projection_outbox WHERE event_id=? AND projection_type=? "
                "AND projection_version=? AND status IN (?,?)",
                (
                    row["event_id"], projection_type, version,
                    JobStatus.COMPLETED.value, JobStatus.SKIPPED.value,
                ),
            ).fetchone()
            if existing:
                continue  # already at this version
            candidate = ReplayCandidate(
                event_id=row["event_id"],
                tenant_id=row["tenant_id"],
                projection_type=projection_type,
                projection_version=version,
                reason=ReplayReason.VERSION_DRIFT.value,
                dependency_state=self._dependency_state(row["event_id"], projection_type),
                replayable=replayable,
            )
            report.candidates.append(candidate)
            if not replayable:
                report.skipped_unreplayable += 1
                continue
            if candidate.dependency_state != DependencyState.READY.value:
                report.skipped_dependency += 1
                continue
            if dry_run:
                continue
            self.outbox.enqueue(
                ProjectionJob(
                    event_id=row["event_id"],
                    projection_type=projection_type,
                    tenant_id=row["tenant_id"],
                    projection_version=version,
                ),
                commit=True,
            )
            report.enqueued += 1
        return report

    # -- status ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        counts = self.outbox.counts()
        scan = self.scan()
        return {
            "outbox": counts,
            "lag_seconds": round(self.outbox.lag_seconds(), 3),
            "owed": len(scan.candidates),
            "owed_by_reason": scan.by_reason(),
            "held_by_dependency": sum(
                1 for c in scan.candidates
                if c.dependency_state == DependencyState.PENDING_DEPENDENCY.value
            ),
            "blocked_by_dead_parent": sum(
                1 for c in scan.candidates
                if c.dependency_state == DependencyState.BLOCKED.value
            ),
            "orphan_projections": len(self.orphan_projections()),
            "events": self.conn.execute(
                "SELECT COUNT(*) FROM cognitive_events"
            ).fetchone()[0],
        }


def _now() -> float:
    import time

    return time.time()


__all__ = [
    "ProjectionReplayEngine",
    "ReplayCandidate",
    "ReplayReason",
    "ReplayReport",
]
