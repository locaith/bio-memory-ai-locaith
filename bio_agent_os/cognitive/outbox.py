"""Transactional outbox for projection jobs.

The write path commits an event and its projection on two different
connections. A crash between them leaves a durable event that nobody knows a
projection is owed for — see docs/v082/WRITE_PATH_ANALYSIS.md.

This module closes that window. The outbox table lives **on the event store's
own connection**, so the event row and the "a projection is owed" row are
written inside one transaction and commit together. Either both exist or
neither does.

What deliberately did not change:

* The event store keeps its own connection. Merging six connections into one
  would put slow projection work on the hot append path and would not survive
  the components moving to separate processes.
* `observe()` without a following `remember()` stays legal. A caller may
  request no projection, and that is recorded as an explicit choice rather
  than inferred from absence.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Bumped when projection logic changes in a way that makes old output stale.
#: Part of the idempotency key, so a version bump lets the same event be
#: re-projected without colliding with its previous result.
PROJECTION_VERSION = 1

#: How long a claimed job may be held before another worker may take it. A
#: worker that dies mid-job must not block the queue forever.
DEFAULT_LEASE_SECONDS = 300.0

#: Attempts before a job is parked in dead-letter rather than retried again.
DEFAULT_MAX_ATTEMPTS = 5


class JobStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"
    #: The caller explicitly asked for no projection. Distinct from PENDING so
    #: "nothing is owed here" is a recorded fact rather than a missing row.
    SKIPPED = "skipped"


def projection_key(event_id: str, projection_type: str, version: int = PROJECTION_VERSION) -> str:
    """Deterministic identity for one projection of one event.

    The same three inputs always produce the same key, so a retry writes to
    the same row rather than creating a second projection. Changing
    `version` deliberately produces a different key, which is how a projection
    can be rebuilt under new logic without clashing with the old one.
    """
    canonical = f"{event_id}\x1f{projection_type}\x1f{version}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ProjectionJob:
    """One unit of owed projection work."""

    event_id: str
    projection_type: str
    tenant_id: str
    projection_version: int = PROJECTION_VERSION
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = JobStatus.PENDING.value
    attempts: int = 0
    available_at: float = field(default_factory=time.time)
    locked_by: str | None = None
    locked_at: float | None = None
    last_error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return projection_key(self.event_id, self.projection_type, self.projection_version)

    def backoff_seconds(self, base: float = 2.0, cap: float = 300.0) -> float:
        """Exponential backoff, capped. Deterministic — a single-process worker
        has nothing to thunder against, and a fixed schedule is testable."""
        return min(base * (2 ** max(0, self.attempts - 1)), cap)


OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_outbox (
    job_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    projection_type TEXT NOT NULL,
    projection_version INTEGER NOT NULL DEFAULT 1,
    projection_key TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL,
    locked_by TEXT,
    locked_at REAL,
    last_error TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    completed_at REAL,
    UNIQUE(event_id, projection_type, projection_version)
);
CREATE INDEX IF NOT EXISTS idx_outbox_claimable
    ON projection_outbox(status, available_at);
CREATE INDEX IF NOT EXISTS idx_outbox_tenant
    ON projection_outbox(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_outbox_event
    ON projection_outbox(event_id);
-- Deliberately NOT unique. `projection_key` is derived from exactly
-- (event_id, projection_type, projection_version), which the composite UNIQUE
-- above already enforces. A second unique constraint on the same invariant is
-- not extra safety: SQLite's ON CONFLICT can name only one target, so the
-- redundant one turns an intended no-op into an IntegrityError. Kept as a
-- plain index for lookup by key.
CREATE INDEX IF NOT EXISTS idx_outbox_key
    ON projection_outbox(projection_key);
"""


# Fairness: một job bị bỏ rơi liên tục phải NHƯỜNG lượt, không bị trừng phạt.
#
# Bake-off (H1_REMEDIATION_BAKEOFF.md) đo được hai điều định hình các con số
# này. Một: backoff luỹ thừa chữa được starvation của poison nhưng phạt luôn
# job chỉ gặp trục trặc hạ tầng — ở chân trời 40 vòng nó tụt xuống DƯỚI hành vi
# hiện tại đúng hai fixture churn. Nên tuyến tính-có-trần, không luỹ thừa.
# Hai: cách ly tự động dựa trên bộ đếm mù nhốt nhầm 3/8 job lành, và H1.2
# chứng minh runtime không có tín hiệu nào phân biệt hạ tầng chập chờn với
# payload độc. Nên nhường là thao tác DUY NHẤT ở đây: không dead-letter, không
# quarantine, không mất việc.
FAIRNESS_YIELD_BASE = 1.0
FAIRNESS_YIELD_CAP = 60.0


class ProjectionOutbox:
    """Queue of owed projections, sharing the event store's connection.

    Every method that writes takes the connection it was given. It never opens
    its own and never commits on its own during an enqueue — the whole point is
    that the caller's transaction decides when the event and the job become
    durable together.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(OUTBOX_SCHEMA)
        self.conn.commit()

    # -- enqueue (inside the caller's transaction) -------------------------

    def enqueue(
        self,
        job: ProjectionJob,
        *,
        commit: bool = False,
    ) -> ProjectionJob:
        """Record that a projection is owed.

        `commit=False` by default and that default is the contract: the caller
        is mid-transaction with the event insert, and committing here would
        reopen exactly the window this table exists to close.

        Enqueueing the same (event, type, version) twice is a no-op. That makes
        the whole append path safe to retry.
        """
        self.conn.execute(
            """
            INSERT INTO projection_outbox(
                job_id, event_id, projection_type, projection_version, projection_key,
                tenant_id, status, attempts, available_at, locked_by, locked_at,
                last_error, payload_json, created_at, completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id, projection_type, projection_version) DO NOTHING
            """,
            (
                job.job_id, job.event_id, job.projection_type, job.projection_version,
                job.key, job.tenant_id, job.status, job.attempts, job.available_at,
                job.locked_by, job.locked_at, job.last_error,
                json.dumps(job.payload, ensure_ascii=False, sort_keys=True),
                job.created_at, job.completed_at,
            ),
        )
        if commit:
            self.conn.commit()
        return job

    # -- claim / complete --------------------------------------------------

    def _yield_expired_leases(
        self,
        now: float,
        stale_before: float,
        tenant_id: str | None,
        *,
        yield_base: float = FAIRNESS_YIELD_BASE,
        yield_cap: float = FAIRNESS_YIELD_CAP,
    ) -> list[str]:
        """Cho một lease đã hết hạn nhường lượt — CHỈ KHI có người để nhường.

        Đo được trên hành vi trước bước này: 12 vòng `batch_size=1`, cả 12 lượt
        giao đều về một job mà worker chết mỗi lần chạm vào; hai job lành phía
        sau đứng nguyên `pending attempts=0`, chưa từng được giao một lần nào.
        Chúng không chậm — chúng không bao giờ tới lượt. Chuyện đó xảy ra cả
        khi job lành thuộc **tenant khác**.

        Nguyên nhân: `claim()` thu hồi lease hết hạn ngay tại chỗ, nên job bị bỏ
        rơi quay lại đầu hàng ở **mỗi** vòng. Nó không cần độc hại để chặn cả
        đoàn; nó chỉ cần luôn đủ điều kiện.

        Nên ở đây job đó được đặt lại `pending` với một `available_at` đẩy tới.
        Ba điều nó **không** làm, và mỗi điều là một quyết định:

        Không dead-letter, không quarantine. `H1_2_ABANDONMENT_ATTRIBUTION_AUDIT`
        đo 11 tín hiệu và thấy 2 tín hiệu bền, cả hai chỉ là định danh công
        việc. Hạ tầng chập chờn và payload độc để lại **cùng một dấu vết**.
        Kết thúc một job dựa trên bộ đếm không phân biệt được hai thứ đó là mã
        hoá một sự chắc chắn mà hệ thống không có.

        Không luỹ thừa. Bake-off đo backoff luỹ thừa ở chân trời 40 vòng và
        thấy nó tụt xuống DƯỚI hành vi cũ đúng hai fixture churn: nó phạt luôn
        job chỉ gặp trục trặc hạ tầng. Tuyến tính-có-trần nhường đủ để đoàn
        phía sau đi, mà không biến một sự cố tạm thời thành một hình phạt dài.

        Không nhường khi không có ai đợi. Nhường vào một hàng đợi rỗng chỉ làm
        queue đứng im — không ai được lợi, và một job đơn độc gặp crash sẽ bị
        làm chậm không vì lý do gì. Fairness là quan hệ giữa các job, nên nó
        chỉ có nghĩa khi có nhiều hơn một.

        Trả về job_id đã nhường, để phía gọi quan sát được. Bộ đếm `attempts`
        được dùng để định cỡ lượt nhường, và **chỉ** để định cỡ: nó không quyết
        định số phận của job nào.
        """
        scoped = tenant_id is not None
        clause = " AND tenant_id = ?" if scoped else ""
        args: tuple = (tenant_id,) if scoped else ()

        expired = self.conn.execute(
            f"""
            SELECT job_id, attempts FROM projection_outbox
            WHERE status = ?
              AND (locked_at IS NULL OR locked_at <= ?)
              {clause}
            """,
            (JobStatus.IN_PROGRESS.value, stale_before, *args),
        ).fetchall()
        if not expired:
            return []

        waiting = self.conn.execute(
            f"""
            SELECT COUNT(*) FROM projection_outbox
            WHERE status = ? AND available_at <= ? {clause}
            """,
            (JobStatus.PENDING.value, now, *args),
        ).fetchone()[0]
        if not waiting:
            return []           # không có ai để nhường; thu hồi ngay như cũ

        yielded: list[str] = []
        for row in expired:
            wait = min(yield_base * max(1, int(row["attempts"])), yield_cap)
            self.conn.execute(
                """
                UPDATE projection_outbox
                SET status=?, locked_by=NULL, locked_at=NULL, available_at=?
                WHERE job_id=?
                """,
                (JobStatus.PENDING.value, now + wait, row["job_id"]),
            )
            yielded.append(str(row["job_id"]))
        self.conn.commit()
        return yielded

    def claim(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        now: float | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        tenant_id: str | None = None,
        yield_base: float = FAIRNESS_YIELD_BASE,
        yield_cap: float = FAIRNESS_YIELD_CAP,
    ) -> list[ProjectionJob]:
        """Take up to `limit` jobs for this worker.

        The UPDATE carries its own `status='pending'` predicate, so two workers
        racing for the same row cannot both win: SQLite serialises the writes
        and the loser updates zero rows. This is the SQLite equivalent of
        `FOR UPDATE SKIP LOCKED`, which the PostgreSQL backend will use instead.

        Expired leases are reclaimed here rather than by a separate sweeper, so
        a worker that died holding a job cannot block it indefinitely.

        **`tenant_id` constrains ACQUISITION, not processing.**

        It used to have no tenant parameter at all, so a tenant-scoped worker
        could only filter *after* claiming — and `reconciliation_worker.run_once`
        did exactly that. Measured on a queue holding one job for `tenant-B`,
        with a worker scoped to `tenant-A`:

            metrics  claimed=0 completed=0        every cycle
            store    tenant-B job -> in_progress, locked_by=<A's worker>,
                     attempts=1

        The boundary had already been crossed by the time anyone looked at the
        tenant, and the metric reported nothing at all — not a wrong number, an
        absent one. Releasing the row quickly afterwards would not fix it: the
        lease was taken, another tenant's queue was touched, and a concurrent
        worker for the rightful tenant was blocked for that window.

            ISOLATION MUST CONSTRAIN ACQUISITION, NOT MERELY PROCESSING.

        So the predicate goes in the SQL, in **both** the SELECT and the UPDATE
        guard — the guard especially, because that is the statement that
        actually decides the race. `None` means "every tenant", which is what an
        unscoped drain or a single-tenant deployment wants.
        """
        now = time.time() if now is None else now
        stale_before = now - lease_seconds
        claimed: list[ProjectionJob] = []
        scoped = tenant_id is not None
        tenant_clause = " AND tenant_id = ?" if scoped else ""

        self._yield_expired_leases(now, stale_before, tenant_id,
                                   yield_base=yield_base, yield_cap=yield_cap)

        rows = self.conn.execute(
            f"""
            SELECT * FROM projection_outbox
            WHERE available_at <= ?
              AND (status = ?
                   OR (status = ? AND (locked_at IS NULL OR locked_at <= ?)))
              {tenant_clause}
            ORDER BY created_at
            LIMIT ?
            """,
            (now, JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value,
             stale_before, *((tenant_id,) if scoped else ()), limit),
        ).fetchall()

        for row in rows:
            cur = self.conn.execute(
                f"""
                UPDATE projection_outbox
                SET status=?, locked_by=?, locked_at=?, attempts=attempts+1
                WHERE job_id=?
                  AND (status=? OR (status=? AND (locked_at IS NULL OR locked_at <= ?)))
                  {tenant_clause}
                """,
                (
                    JobStatus.IN_PROGRESS.value, worker_id, now, row["job_id"],
                    JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value,
                    stale_before, *((tenant_id,) if scoped else ()),
                ),
            )
            if cur.rowcount:
                claimed.append(self._row(row, status=JobStatus.IN_PROGRESS.value,
                                         locked_by=worker_id, locked_at=now,
                                         attempts=row["attempts"] + 1))
        self.conn.commit()
        return claimed

    def complete(self, job_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        cur = self.conn.execute(
            "UPDATE projection_outbox SET status=?, completed_at=?, locked_by=NULL, "
            "locked_at=NULL, last_error=NULL WHERE job_id=?",
            (JobStatus.COMPLETED.value, now, job_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        now: float | None = None,
    ) -> str:
        """Record a failure and decide whether to retry or park it.

        Returns the resulting status so a caller can log it without a re-read.
        """
        now = time.time() if now is None else now
        row = self.conn.execute(
            "SELECT attempts FROM projection_outbox WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            return ""
        attempts = int(row["attempts"])
        if attempts >= max_attempts:
            status, available_at = JobStatus.DEAD_LETTER.value, now
        else:
            status = JobStatus.PENDING.value
            available_at = now + min(2.0 * (2 ** max(0, attempts - 1)), 300.0)
        self.conn.execute(
            "UPDATE projection_outbox SET status=?, last_error=?, available_at=?, "
            "locked_by=NULL, locked_at=NULL WHERE job_id=?",
            (status, error[:2000], available_at, job_id),
        )
        self.conn.commit()
        return status

    def skip(self, event_id: str, projection_type: str, *, reason: str = "") -> bool:
        """Mark that no projection is owed for this event after all.

        Used when the projection layer legitimately declines — a quarantined
        memory, for instance. Recording the decision keeps "nothing is owed"
        distinguishable from "nobody has got to it yet".
        """
        cur = self.conn.execute(
            "UPDATE projection_outbox SET status=?, last_error=?, completed_at=? "
            "WHERE event_id=? AND projection_type=? AND status IN (?,?)",
            (
                JobStatus.SKIPPED.value, reason[:2000], time.time(),
                event_id, projection_type,
                JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value,
            ),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    # -- inspection --------------------------------------------------------

    def get(self, job_id: str) -> ProjectionJob | None:
        row = self.conn.execute(
            "SELECT * FROM projection_outbox WHERE job_id=?", (job_id,)
        ).fetchone()
        return self._row(row) if row else None

    def by_event(self, event_id: str) -> list[ProjectionJob]:
        rows = self.conn.execute(
            "SELECT * FROM projection_outbox WHERE event_id=? ORDER BY created_at", (event_id,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def pending(self, *, tenant_id: str | None = None, limit: int = 1000) -> list[ProjectionJob]:
        if tenant_id:
            rows = self.conn.execute(
                "SELECT * FROM projection_outbox WHERE status=? AND tenant_id=? "
                "ORDER BY created_at LIMIT ?",
                (JobStatus.PENDING.value, tenant_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM projection_outbox WHERE status=? ORDER BY created_at LIMIT ?",
                (JobStatus.PENDING.value, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def dead_letters(self, limit: int = 1000) -> list[ProjectionJob]:
        rows = self.conn.execute(
            "SELECT * FROM projection_outbox WHERE status=? ORDER BY created_at LIMIT ?",
            (JobStatus.DEAD_LETTER.value, limit),
        ).fetchall()
        return [self._row(r) for r in rows]

    def stale(self, *, lease_seconds: float = DEFAULT_LEASE_SECONDS,
              now: float | None = None) -> list[ProjectionJob]:
        """Jobs held by a worker that has stopped renewing its lease."""
        now = time.time() if now is None else now
        rows = self.conn.execute(
            "SELECT * FROM projection_outbox WHERE status=? AND locked_at <= ?",
            (JobStatus.IN_PROGRESS.value, now - lease_seconds),
        ).fetchall()
        return [self._row(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM projection_outbox GROUP BY status"
        ).fetchall()
        out = {s.value: 0 for s in JobStatus}
        for r in rows:
            out[r["status"]] = int(r["n"])
        return out

    def lag_seconds(self, *, now: float | None = None) -> float:
        """Age of the oldest pending job. The queue's health in one number."""
        now = time.time() if now is None else now
        row = self.conn.execute(
            "SELECT MIN(created_at) AS oldest FROM projection_outbox WHERE status=?",
            (JobStatus.PENDING.value,),
        ).fetchone()
        oldest = row["oldest"] if row else None
        return 0.0 if oldest is None else max(0.0, now - float(oldest))

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row, **overrides: Any) -> ProjectionJob:
        data = {
            "job_id": row["job_id"],
            "event_id": row["event_id"],
            "projection_type": row["projection_type"],
            "projection_version": int(row["projection_version"]),
            "tenant_id": row["tenant_id"],
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "available_at": float(row["available_at"]),
            "locked_by": row["locked_by"],
            "locked_at": row["locked_at"],
            "last_error": row["last_error"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "created_at": float(row["created_at"]),
            "completed_at": row["completed_at"],
        }
        data.update(overrides)
        return ProjectionJob(**data)


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "OUTBOX_SCHEMA",
    "PROJECTION_VERSION",
    "JobStatus",
    "ProjectionJob",
    "ProjectionOutbox",
    "projection_key",
]
