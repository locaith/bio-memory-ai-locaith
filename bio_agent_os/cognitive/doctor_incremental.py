"""Scanning only what has arrived since the last scan.

A deep scan of 366,715 events takes 48 seconds. That is right for an audit and
wrong for something an operator wants to run every few minutes.

Kept out of `diagnostics.py` so the checks and the thing that bounds them stay
separable: the checks are the same either way, and only the window changes.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .diagnostics import DeepDoctor, DoctorReport, Finding, Severity


class IncrementalDoctor(DeepDoctor):
    """A deep scan bounded to what has arrived since the last one.

    48 seconds on 366,715 events is right for an audit and wrong for something
    an operator wants to run every few minutes. This scans new events, new
    outbox rows and new ledger rows from a stored cursor, and runs every cheap
    global invariant — schema, indexes, stale leases, unexplained dead letters,
    dependency cycles, shadow consistency, dangling references and a SQLite
    quick check — in full, every time. A defect in those is not confined to
    recent rows, and pretending otherwise is how an incremental scanner
    develops a blind spot.

    The cursor advances only after a completed scan, and only when that scan
    found nothing outstanding. A crash mid-scan re-covers the same ground next
    time; a FAIL or CRITICAL holds the cursor where it is until the problem is
    gone. Stepping over an unhandled finding would hide it permanently.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        tenant_id: str | None = None,
        cursor_store: Any | None = None,
    ) -> None:
        super().__init__(conn, tenant_id=tenant_id)
        from .doctor_cursor import CursorStore

        # Cheap enough to run every few minutes. The full index cross-check
        # belongs to the audit, not to the loop.
        self.integrity_pragma = "quick_check"
        self.cursors = cursor_store or CursorStore(conn)
        self.cursor = None
        self.window_start: dict[str, int] = {}
        self.window_end: dict[str, int] = {}

    # -- scan --------------------------------------------------------------

    def run_incremental(
        self,
        *,
        since_event_id: str | None = None,
        since_time: str | None = None,
        full: bool = False,
        advance: bool = True,
    ) -> DoctorReport:
        """Scan from the cursor (or an explicit starting point) to now."""
        from .doctor_cursor import GLOBAL_SCOPE, Cursor, schema_fingerprint

        scope = self.tenant_id or GLOBAL_SCOPE
        stored = self.cursors.load(scope)

        if full:
            start = Cursor(scope=scope, schema_fingerprint=stored.schema_fingerprint,
                           reason="--full requested; the stored cursor is ignored")
        elif since_event_id is not None:
            rowid = self.cursors.rowid_of_event(since_event_id)
            if rowid is None:
                report = DoctorReport(deep=True)
                report.scan_error = f"unknown event_id: {since_event_id}"
                return report
            start = Cursor(scope=scope, last_event_rowid=rowid,
                           schema_fingerprint=stored.schema_fingerprint,
                           reason=f"--since-event {since_event_id} (rowid {rowid})")
        elif since_time is not None:
            rowid = self.cursors.rowid_at_time(since_time)
            start = Cursor(scope=scope, last_event_rowid=rowid,
                           schema_fingerprint=stored.schema_fingerprint,
                           reason=f"--since-time {since_time} (rowid {rowid})")
        else:
            start = stored

        # The high-water mark is read *before* the scan. Anything appended
        # while it runs stays outside this window and is picked up next time,
        # rather than being counted as covered by a scan that never saw it.
        events, ledger, outbox, event_id, observed_at = self.cursors.high_water(scope)
        self.cursor = start
        self.window_start = {
            "events": start.last_event_rowid,
            "ledger": start.last_ledger_rowid,
            "outbox": start.last_outbox_rowid,
        }
        self.window_end = {"events": events, "ledger": ledger, "outbox": outbox}

        report = self._run_windowed(start, full=full)
        report.counts["incremental"] = 1
        # Counted, not subtracted. Rowids are global, so `high_water - floor`
        # would report another tenant's rows as belonging to this window.
        report.counts["window_events"] = self._window_rows(
            "cognitive_events", start.last_event_rowid, events)
        report.counts["window_ledger"] = self._window_rows(
            "projection_ledger", start.last_ledger_rowid, ledger)
        report.counts["window_outbox"] = self._window_rows(
            "projection_outbox", start.last_outbox_rowid, outbox)

        if not advance:
            return report
        if report.scan_error:
            # The scan did not finish. The cursor must not move.
            return report

        blocking = [
            f for f in report.findings
            if Severity(f.severity) in (Severity.FAIL, Severity.CRITICAL)
        ]
        if blocking:
            held = Cursor(
                scope=scope,
                last_event_rowid=start.last_event_rowid,
                last_ledger_rowid=start.last_ledger_rowid,
                last_outbox_rowid=start.last_outbox_rowid,
                last_event_id=start.last_event_id,
                last_observed_at=start.last_observed_at,
                schema_fingerprint=schema_fingerprint(self.conn),
                blocked_by_findings=True,
                blocked_reason=(
                    f"{len(blocking)} unresolved finding(s): "
                    + ", ".join(sorted({f.code for f in blocking})[:5])
                ),
            )
            self.cursors.save(held)
            return report

        self.cursors.save(Cursor(
            scope=scope, last_event_rowid=events, last_ledger_rowid=ledger,
            last_outbox_rowid=outbox, last_event_id=event_id,
            last_observed_at=observed_at,
            schema_fingerprint=schema_fingerprint(self.conn),
        ))
        return report

    def _window_rows(self, table: str, floor: int, ceiling: int) -> int:
        """How many rows of this tenant's actually fall in the window."""
        sql = f"SELECT COUNT(*) FROM {table} WHERE rowid > ? AND rowid <= ?"
        params: tuple = (floor, ceiling)
        if self.tenant_id:
            sql += " AND tenant_id = ?"
            params = (*params, self.tenant_id)
        try:
            return int(self.conn.execute(sql, params).fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    def _run_windowed(self, start: Any, *, full: bool = False) -> DoctorReport:
        self.report = DoctorReport(deep=True)
        started = time.perf_counter()
        try:
            self._collect_counts()

            # PRAGMA integrity/quick_check reads the WHOLE database file, so its
            # cost follows file size, not window size. Running it on every
            # incremental scan makes an O(window) scan O(database) — and worse,
            # SQLite cannot checkpoint the WAL past the oldest reader, so the WAL
            # only grows for as long as the pragma runs.
            #
            # Canary run 4, measured: 1.811s on a 244 MB database, ~12.6s at
            # 1.7 GB, and the 24-hour projection put it at ~148s out of every
            # 300s. Checkpointing blocked half the time is how a WAL never comes
            # back. The pragma belongs to the audit that is meant to be
            # expensive, not to a five-minute scan.
            head = [self.check_schema, self.check_capabilities, self.check_outbox_basics]
            if full:
                head.insert(0, self.check_sqlite)
            for fn in head:
                self._check(fn)
            if not full:
                # Said out loud. A check that quietly did not run turns a report
                # into a claim it never made.
                self.report.add(Finding(
                    "SQLITE_INTEGRITY_DEFERRED", Severity.INFO.value, "sqlite",
                    f"{self.integrity_pragma} skipped on an incremental scan; it "
                    f"reads the whole database file. Runs on --full and on the "
                    f"deep audit.",
                    evidence={"pragma": self.integrity_pragma, "scan": "incremental"},
                ))
            for fn in (
                self.check_event_integrity,
                self.check_event_debt,
                self.check_orphan_jobs,
                self.check_job_lifecycle,
                self.check_ledger_consistency,
                self.check_projection_consistency,
                self.check_dependencies,
                self.check_shadow,
                self.check_dangling_references,
            ):
                name = fn.__name__
                window = self.WINDOWED_CHECKS.get(name)
                if window:
                    field, alias = window
                    self._active_window = (alias, getattr(start, field, 0))
                else:
                    self._active_window = None
                try:
                    self._check(fn)
                finally:
                    self._active_window = None
        except Exception as exc:  # a doctor that dies must not look healthy
            self.report.scan_error = f"{type(exc).__name__}: {exc}"
        self.report.duration_s = time.perf_counter() - started
        return self.report

    def describe(self) -> dict[str, Any]:
        return {
            "cursor_before": self.cursor.as_dict() if self.cursor else None,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "windowed_checks": sorted(self.WINDOWED_CHECKS),
            "always_full_checks": [
                "check_sqlite", "check_schema", "check_capabilities",
                "check_outbox_basics", "check_job_lifecycle",
                "check_dependencies", "check_shadow", "check_dangling_references",
            ],
        }

__all__ = ["IncrementalDoctor"]
