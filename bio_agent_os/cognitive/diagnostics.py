"""Deep diagnostics — telling healthy apart from owing apart from broken.

The pipeline can now recover from a crash. That is not the same as knowing
whether it currently needs to. This module answers the question a operator
actually asks: is anything wrong, what exactly, and can it be fixed
automatically.

Two rules shape every check:

* **Read only.** Doctor never writes. Repair is a separate, audited step that
  acts on a plan this module produces.
* **Unsupported is not corrupted.** Four of five projection types have no
  builder. Their absence is a missing capability, reported as such, and it
  never counts as damage — nor is it hidden so the summary can say "healthy".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .outbox import DEFAULT_MAX_ATTEMPTS, JobStatus, projection_key
from .projection_capability import CAPABILITIES, is_supported, supported_types, unsupported_types
from .projection_registry import ProjectionType, dependencies_of, detect_cycles
from .shadow import ComparisonStatus

#: How many rows a single expected-by-design check may name individually
#: before it folds the rest into one counted finding. A report is read by a
#: person; a check that emits one line per row stops being a report somewhere
#: around the thousandth line and becomes a data dump. Twenty is enough to
#: investigate a handful by hand and small enough that no check can dominate.
_MAX_ROW_FINDINGS = 20


class Severity(str, Enum):
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"
    CRITICAL = "CRITICAL"


_ORDER = {s: i for i, s in enumerate(
    [Severity.PASS, Severity.INFO, Severity.WARN, Severity.FAIL, Severity.CRITICAL]
)}


@dataclass(slots=True)
class Finding:
    code: str
    severity: str
    entity_type: str
    description: str
    entity_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    repairable: bool = False
    suggested_action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "description": self.description,
            "evidence": self.evidence,
            "repairable": self.repairable,
            "suggested_action": self.suggested_action,
        }


@dataclass(slots=True)
class DoctorReport:
    deep: bool = False
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    checks_run: int = 0
    queries: int = 0
    scan_error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    #: Per-check read-hold accounting, added after Run 8. A doctor that takes
    #: thirty seconds in three hundred hundred-millisecond slices is safe; one
    #: that takes ten seconds in a single slice is not. Only this distinguishes
    #: them, so the total duration is no longer the number to watch.
    reader_holds: dict[str, dict] = field(default_factory=dict)

    @property
    def max_hold_ms(self) -> float:
        return max((e["max_ms"] for e in self.reader_holds.values()), default=0.0)

    def holds_ranked(self) -> list[tuple[str, dict]]:
        return sorted(self.reader_holds.items(), key=lambda kv: -kv[1]["max_ms"])

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def by_severity(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out

    @property
    def worst(self) -> str:
        if not self.findings:
            return Severity.PASS.value
        return max((f.severity for f in self.findings), key=lambda s: _ORDER[Severity(s)])

    @property
    def repairable(self) -> list[Finding]:
        return [f for f in self.findings if f.repairable]

    @property
    def manual(self) -> list[Finding]:
        return [
            f for f in self.findings
            if not f.repairable and Severity(f.severity) in (Severity.FAIL, Severity.CRITICAL)
        ]

    @property
    def exit_code(self) -> int:
        """3 = doctor itself failed, 2 = CRITICAL, 1 = FAIL, 0 = clean.

        A scan that could not finish must never look like a clean bill of
        health, which is why the self-failure code outranks the findings.
        """
        if self.scan_error:
            return 3
        worst = self.worst
        if worst == Severity.CRITICAL.value:
            return 2
        if worst == Severity.FAIL.value:
            return 1
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "deep": self.deep,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 4),
            "checks_run": self.checks_run,
            "queries": self.queries,
            "scan_error": self.scan_error,
            "exit_code": self.exit_code,
            "worst_severity": self.worst,
            "by_severity": self.by_severity(),
            "counts": self.counts,
            "supported_projection_types": list(supported_types()),
            "unsupported_projection_types": list(unsupported_types()),
            "repairable_findings": len(self.repairable),
            "manual_review_required": len(self.manual),
            "findings": [f.as_dict() for f in self.findings],
        }

    def render(self, width: int = 78) -> str:
        lines = [
            "=" * width,
            f"  BIO-AGENT OS {'DEEP ' if self.deep else ''}DOCTOR",
            "=" * width,
        ]
        if self.scan_error:
            lines.append(f"  SCAN DID NOT COMPLETE: {self.scan_error}")
            lines.append("=" * width)
            return "\n".join(lines)

        groups: dict[str, list[Finding]] = {}
        for f in self.findings:
            groups.setdefault(f.entity_type, []).append(f)

        for entity, items in sorted(groups.items()):
            worst = max((i.severity for i in items), key=lambda s: _ORDER[Severity(s)])
            non_pass = [i for i in items if i.severity != Severity.PASS.value]
            detail = f"{len(non_pass)} finding(s)" if non_pass else "clean"
            lines.append(f"  {entity:<28} {worst:<9} {detail}")

        lines.append("  " + "-" * (width - 4))
        for f in self.findings:
            if f.severity == Severity.PASS.value:
                continue
            target = f.entity_id[:24] if f.entity_id else "-"
            lines.append(f"  [{f.severity:<8}] {f.code:<32} {target}")
            lines.append(f"             {f.description[:width - 14]}")
            if f.suggested_action:
                lines.append(f"             -> {f.suggested_action[:width - 17]}")

        lines.append("  " + "-" * (width - 4))
        lines.append(f"  supported projection types  : {', '.join(supported_types())}")
        lines.append(f"  unsupported                 : {len(unsupported_types())} "
                     f"({', '.join(unsupported_types())})")
        lines.append(f"  repairable findings         : {len(self.repairable)}")
        lines.append(f"  manual review required      : {len(self.manual)}")
        lines.append(f"  checks run                  : {self.checks_run}")
        lines.append(f"  scan time                   : {self.duration_s:.3f}s")
        lines.append("=" * width)
        lines.append(f"  verdict: {self.worst}   (exit {self.exit_code})")
        lines.append("=" * width)
        return "\n".join(lines)


class DeepDoctor:
    """Read-only consistency diagnosis across the projection pipeline."""

    def __init__(self, conn: sqlite3.Connection, *, tenant_id: str | None = None,
                 wal_manager: Any = None, reader_source: str = "doctor_deep",
                 hold_report_threshold_ms: float = 25.0) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.tenant_id = tenant_id
        self.report = DoctorReport()
        #: Optional. When given, holds above the threshold are reported to the
        #: WAL manager so a reclaim campaign can see who is in its way.
        self.wal_manager = wal_manager
        self._reader_source = reader_source
        self.hold_report_threshold_ms = hold_report_threshold_ms
        self._active_check: str | None = None

    # -- plumbing ----------------------------------------------------------

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """One read, held for exactly as long as the fetch takes.

        `fetchall()` matters and is not decoration: a lazily-iterated cursor
        keeps the read transaction — and therefore the WAL — open for as long as
        the caller takes to consume it, which is unbounded. Fetching completes
        the statement and drops the snapshot before any Python touches the rows.

        The timing is here because Run 8's post-mortem could only correlate: the
        log grew 30–43 MB during 8–12 second doctor runs, and nothing recorded
        *which* of the twenty-four queries was doing the holding. Now each one
        says so.
        """
        self.report.queries += 1
        started = time.perf_counter()
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        held_ms = (time.perf_counter() - started) * 1000
        self._record_hold(sql, held_ms, len(rows))
        return rows

    def _record_hold(self, sql: str, held_ms: float, rows: int) -> None:
        label = self._active_check or "unattributed"
        entry = self.report.reader_holds.setdefault(
            label, {"queries": 0, "total_ms": 0.0, "max_ms": 0.0, "rows": 0,
                    "slowest_sql": ""})
        entry["queries"] += 1
        entry["total_ms"] += held_ms
        entry["rows"] += rows
        if held_ms > entry["max_ms"]:
            entry["max_ms"] = held_ms
            entry["slowest_sql"] = " ".join(sql.split())[:160]
        if self.wal_manager is not None and held_ms >= self.hold_report_threshold_ms:
            # Only the holds worth a manager's attention. Registering every
            # sub-millisecond read would drown the signal it exists to carry.
            self.wal_manager.note_reader(f"doctor:{label}:{self.report.queries}",
                                         source=self._reader_source)
            self.wal_manager.release_reader(f"doctor:{label}:{self.report.queries}")

    def _scalar(self, sql: str, params: tuple = (), default: Any = 0) -> Any:
        rows = self._q(sql, params)
        return rows[0][0] if rows else default

    def _tenant_clause(self, alias: str = "") -> tuple[str, tuple]:
        """Scope predicates every check appends to its WHERE.

        Carries the incremental window too, because every windowed check
        already calls this with the alias of its driving table — adding a
        second helper would mean remembering to call it in ten places.
        """
        window_sql, window_params = self._window_clause()
        if not self.tenant_id:
            return window_sql, window_params
        prefix = f"{alias}." if alias else ""
        return f" AND {prefix}tenant_id = ?{window_sql}", (self.tenant_id, *window_params)

    def _check(self, fn) -> None:
        self.report.checks_run += 1
        self._active_check = getattr(fn, "__name__", "unknown")
        try:
            fn()
        finally:
            self._active_check = None

    #: Set by `IncrementalDoctor` for the duration of one windowed check, as
    #: `(alias, floor_rowid)`. The base class knows nothing else about it.
    _active_window: tuple[str, int] | None = None

    def _window_clause(self) -> tuple[str, tuple]:
        if not self._active_window:
            return "", ()
        alias, floor = self._active_window
        if floor <= 0:
            return "", ()
        prefix = f"{alias}." if alias else ""
        return f" AND {prefix}rowid > ?", (floor,)

    #: "A projection exists for this event", as an indexed lookup.
    #:
    #: Three checks used to ask this per row as
    #: `LIKE '%' || event_id || '%'`. A leading wildcard cannot use an index,
    #: so each of the N outer rows scanned all M memories: O(N*M). Measured at
    #: 1k/5k/10k events the deep scan took 0.78s / 23.7s / 98.7s — exponent
    #: 2.1, and an extrapolated 2.75 hours at 100k.
    #:
    #: An interim fix read the column once into a Python set. That was O(N+M)
    #: but still a full scan of every memory on every doctor run, and it held
    #: ~10MB of id strings at 100k memories. `memory_source_events` makes the
    #: relationship a row with an index on it, so the question is answered by
    #: a lookup instead of by a scan of any kind.
    #:
    #: The join back to `cognitive_memories` is deliberate: a link row that
    #: outlived its memory must not answer "yes, projected" for a projection
    #: that no longer exists. ON DELETE CASCADE handles that too, but only
    #: when foreign keys are enforced, and a diagnosis must not depend on a
    #: pragma being set.
    _PROJECTION_EXISTS = (
        "EXISTS (SELECT 1 FROM memory_source_events s"
        "        JOIN cognitive_memories m"
        "          ON m.memory_id = s.memory_id AND m.version = s.version"
        "        WHERE s.event_id = {column})"
    )

    def _projection_exists(self, column: str) -> str:
        return self._PROJECTION_EXISTS.format(column=column)

    # -- run ---------------------------------------------------------------

    def run(self, *, deep: bool = False) -> DoctorReport:
        self.report = DoctorReport(deep=deep)
        started = time.perf_counter()
        try:
            self._collect_counts()

            # The integrity pragma reads the whole database file, so its cost
            # follows file size and not scan depth. It belongs to the audit
            # that is meant to be expensive.
            #
            # 14bbbd5 took it out of the incremental scan but left it here, so
            # the quick scan kept paying it: canary run 5 measured the quick
            # doctor at 13.84s, then 37.30s, then 58.12s as the database grew
            # past a gigabyte, every thirty minutes. And SQLite cannot
            # checkpoint the WAL past the oldest reader, so the WAL only grows
            # for as long as it runs -- which is how that run reached 479 MB
            # against a 512 MB limit and stopped at 1.55h of 24.
            head = [self.check_schema, self.check_capabilities, self.check_outbox_basics]
            if deep:
                head.insert(0, self.check_sqlite)
            for fn in head:
                self._check(fn)
            if not deep:
                # Named, so a check that did not run cannot be mistaken for a
                # check that passed.
                self.report.add(Finding(
                    "SQLITE_INTEGRITY_DEFERRED", Severity.INFO.value, "sqlite",
                    f"{self.integrity_pragma} skipped on a quick scan; it reads the "
                    f"whole database file. Runs on the deep audit.",
                    evidence={"pragma": self.integrity_pragma, "scan": "quick"},
                ))
            if deep:
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
                    self._check(fn)
        except Exception as exc:  # a doctor that dies must not look healthy
            self.report.scan_error = f"{type(exc).__name__}: {exc}"
        self.report.duration_s = time.perf_counter() - started
        return self.report

    # -- checks that always run in full ------------------------------------

    #: Which checks can be bounded by a cursor, and by which of its positions.
    #: The value is `(cursor field, SQL alias)`; an empty alias means the
    #: check queries its table without one.
    #:
    #: Everything absent runs in full every time. That is the point of the
    #: split: schema, capabilities, stale leases, dead letters, dependencies,
    #: shadow and dangling references are cheap and global, and a defect in
    #: any of them is not confined to recent rows.
    WINDOWED_CHECKS: dict[str, tuple[str, str]] = {
        "check_event_integrity": ("last_event_rowid", ""),
        "check_event_debt": ("last_event_rowid", "e"),
        "check_orphan_jobs": ("last_outbox_rowid", "o"),
        "check_ledger_consistency": ("last_ledger_rowid", "l"),
        "check_projection_consistency": ("last_outbox_rowid", "o"),
    }

    # -- counts ------------------------------------------------------------

    def _collect_counts(self) -> None:
        for name, table in (
            ("events", "cognitive_events"),
            ("outbox_jobs", "projection_outbox"),
            ("projections", "cognitive_memories"),
            ("ledger", "projection_ledger"),
            ("shadow", "shadow_memories"),
        ):
            self.report.counts[name] = int(
                self._scalar(f"SELECT COUNT(*) FROM {table}", default=0)
            )

    # -- sqlite ------------------------------------------------------------

    #: `integrity_check` reads every page and every index entry, so its cost
    #: grows with the database — 366,715 events is around 40 seconds of the
    #: scan. `quick_check` skips the index cross-checks and is what an
    #: incremental run uses; a full audit still runs the complete one.
    integrity_pragma: str = "integrity_check"

    def _main_database_path(self) -> str | None:
        """The file behind `main`, or None for an in-memory database."""
        try:
            for row in self.conn.execute("PRAGMA database_list"):
                if row[1] == "main":
                    return str(row[2]) or None
        except sqlite3.Error:  # pragma: no cover - defensive
            pass
        return None

    def _integrity_verdict(self) -> str:
        """Run the integrity pragma on a connection with a current snapshot.

        This must not use the shared connection. Python's sqlite3 leaves a
        read transaction open after a SELECT, so a connection that has read
        anything is pinned to that snapshot — and a snapshot taken before
        another connection rewrote the FTS index makes SQLite report
        "malformed inverted index for FTS5 table" on a database that is
        perfectly healthy.

        That false positive is the worst kind: SQLITE_INTEGRITY is CRITICAL,
        it would stop a canary, and nothing is actually wrong. Six connections
        to one file makes it reachable in ordinary operation, which is how it
        was found.
        """
        path = self._main_database_path()
        if not path:
            return self._verdict_on(self.conn)
        try:
            fresh = sqlite3.connect(path, timeout=60.0)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            return f"unreadable: {exc}"
        try:
            return self._verdict_on(fresh)
        finally:
            fresh.close()

    def _verdict_on(self, conn: sqlite3.Connection) -> str:
        self.report.queries += 1
        try:
            row = conn.execute(f"PRAGMA {self.integrity_pragma}").fetchone()
        except sqlite3.Error as exc:
            return f"unreadable: {exc}"
        return str(row[0]) if row else "unreadable"

    def check_sqlite(self) -> None:
        verdict = self._integrity_verdict()
        if verdict == "ok":
            self.report.add(Finding(
                "SQLITE_INTEGRITY", Severity.PASS.value, "sqlite",
                f"{self.integrity_pragma} ok",
                evidence={"pragma": self.integrity_pragma},
            ))
        else:
            self.report.add(Finding(
                "SQLITE_INTEGRITY", Severity.CRITICAL.value, "sqlite",
                f"{self.integrity_pragma} reported: {verdict}",
                evidence={"result": verdict, "pragma": self.integrity_pragma},
                repairable=False,
                suggested_action="restore from backup; do not repair in place",
            ))

        fk = self._q("PRAGMA foreign_key_check")
        if fk:
            self.report.add(Finding(
                "SQLITE_FOREIGN_KEY", Severity.FAIL.value, "sqlite",
                f"{len(fk)} foreign key violation(s)", evidence={"violations": len(fk)},
            ))

        journal = self._q("PRAGMA journal_mode")
        mode = journal[0][0] if journal else "unknown"
        if mode not in ("wal", "memory"):
            self.report.add(Finding(
                "SQLITE_JOURNAL_MODE", Severity.WARN.value, "sqlite",
                f"journal_mode is {mode}; WAL is expected for concurrent access",
                evidence={"journal_mode": mode},
            ))

    def check_schema(self) -> None:
        present = {
            r[0] for r in self._q("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {
            "cognitive_events", "projection_outbox", "cognitive_memories",
        }
        missing = required - present
        if missing:
            self.report.add(Finding(
                "SCHEMA_MISSING_TABLE", Severity.CRITICAL.value, "schema",
                f"missing table(s): {', '.join(sorted(missing))}",
                evidence={"missing": sorted(missing)},
            ))
            return

        # A runtime that never ran a worker has no ledger; that is not damage.
        for optional in ("projection_ledger", "shadow_memories"):
            if optional not in present:
                self.report.add(Finding(
                    "SCHEMA_OPTIONAL_TABLE_ABSENT", Severity.INFO.value, "schema",
                    f"{optional} not created yet (no worker or shadow run on this database)",
                ))

        indexes = {
            r[0] for r in self._q("SELECT name FROM sqlite_master WHERE type='index'")
        }
        for expected in ("idx_outbox_claimable", "idx_outbox_event"):
            if "projection_outbox" in present and expected not in indexes:
                self.report.add(Finding(
                    "SCHEMA_MISSING_INDEX", Severity.WARN.value, "schema",
                    f"index {expected} is missing; claim scans will degrade",
                    entity_id=expected,
                ))

    # -- capabilities ------------------------------------------------------

    def check_capabilities(self) -> None:
        """Report unsupported types as capability gaps, never as damage."""
        for ptype in unsupported_types():
            cap = CAPABILITIES[ptype]
            self.report.add(Finding(
                "UNSUPPORTED_CAPABILITY", Severity.INFO.value, "capability",
                f"{ptype} has no builder: {cap.reason}",
                entity_id=ptype, repairable=False,
                suggested_action="no action; scheduled for a later release",
                evidence={"shadow_supported": False, "replayable": False},
            ))
        self.report.add(Finding(
            "CAPABILITY_SUMMARY", Severity.PASS.value, "capability",
            f"{len(supported_types())} supported, {len(unsupported_types())} unsupported",
            evidence={
                "supported": list(supported_types()),
                "unsupported": list(unsupported_types()),
            },
        ))

    # -- outbox ------------------------------------------------------------

    def check_outbox_basics(self) -> None:
        clause, params = self._tenant_clause()
        rows = self._q(
            f"SELECT status, COUNT(*) n FROM projection_outbox WHERE 1=1{clause} GROUP BY status",
            params,
        )
        counts = {r["status"]: int(r["n"]) for r in rows}
        pending = counts.get(JobStatus.PENDING.value, 0)
        dead = counts.get(JobStatus.DEAD_LETTER.value, 0)

        if pending:
            self.report.add(Finding(
                "OUTBOX_PENDING", Severity.WARN.value, "outbox",
                f"{pending} projection job(s) still owed",
                evidence={"pending": pending},
                # Not repairable by reconciliation: draining the queue is the
                # worker's job, and claiming otherwise would tell an operator
                # that `--repair` handles it.
                repairable=False,
                suggested_action="run `bio-agent-os projection worker --once`",
            ))
        if dead:
            self.report.add(Finding(
                "OUTBOX_DEAD_LETTER", Severity.FAIL.value, "outbox",
                f"{dead} job(s) in dead-letter",
                evidence={"dead_letter": dead}, repairable=False,
                suggested_action="inspect last_error; replay only after diagnosing",
            ))
        if not pending and not dead:
            self.report.add(Finding("OUTBOX_CLEAN", Severity.PASS.value, "outbox",
                                    "no pending or dead-lettered jobs"))

    def check_job_lifecycle(self) -> None:
        now = time.time()
        clause, params = self._tenant_clause()

        for row in self._q(
            f"SELECT * FROM projection_outbox WHERE status=?{clause}",
            (JobStatus.IN_PROGRESS.value, *params),
        ):
            locked_at = row["locked_at"]
            if not row["locked_by"]:
                self.report.add(Finding(
                    "JOB_CLAIMED_WITHOUT_OWNER", Severity.FAIL.value, "outbox",
                    "job is in progress with no lease owner",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=True,
                    suggested_action="release the lease and return the job to pending",
                ))
            elif locked_at is not None and (now - float(locked_at)) > 3600:
                self.report.add(Finding(
                    "JOB_STALE_LEASE", Severity.WARN.value, "outbox",
                    f"lease held for {int(now - float(locked_at))}s by {row['locked_by']}",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=True,
                    evidence={"held_seconds": int(now - float(locked_at))},
                    suggested_action="release the stale lease",
                ))

        for row in self._q(
            f"SELECT * FROM projection_outbox WHERE attempts > ? AND status != ?{clause}",
            (DEFAULT_MAX_ATTEMPTS, JobStatus.DEAD_LETTER.value, *params),
        ):
            self.report.add(Finding(
                "JOB_ATTEMPTS_EXCEEDED", Severity.FAIL.value, "outbox",
                f"{row['attempts']} attempts but status is {row['status']}",
                entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=True,
                evidence={"attempts": row["attempts"], "status": row["status"]},
                suggested_action="move to dead-letter",
            ))

        for row in self._q(
            f"SELECT * FROM projection_outbox WHERE status=? AND "
            f"(last_error IS NULL OR last_error=''){clause}",
            (JobStatus.DEAD_LETTER.value, *params),
        ):
            self.report.add(Finding(
                "DEAD_LETTER_WITHOUT_REASON", Severity.WARN.value, "outbox",
                "dead-lettered with no last_error recorded",
                entity_id=row["job_id"], tenant_id=row["tenant_id"],
            ))

        for row in self._q(
            f"SELECT * FROM projection_outbox WHERE 1=1{clause}", params
        ):
            expected = projection_key(
                row["event_id"], row["projection_type"], int(row["projection_version"])
            )
            if row["projection_key"] != expected:
                self.report.add(Finding(
                    "INVALID_PROJECTION_KEY", Severity.FAIL.value, "outbox",
                    "projection_key does not match its own identity fields",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=False,
                    evidence={"stored": row["projection_key"][:16], "expected": expected[:16]},
                    suggested_action="manual review: the row was edited outside the API",
                ))
            if int(row["projection_version"]) < 1:
                self.report.add(Finding(
                    "INVALID_PROJECTION_VERSION", Severity.FAIL.value, "outbox",
                    f"projection_version is {row['projection_version']}",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"],
                ))

    # -- events ------------------------------------------------------------

    def check_event_integrity(self) -> None:
        clause, params = self._tenant_clause()
        rows = self._q(
            f"SELECT event_id, tenant_id, checksum FROM cognitive_events WHERE 1=1{clause}",
            params,
        )
        seen: set[str] = set()
        for row in rows:
            if row["event_id"] in seen:
                self.report.add(Finding(
                    "DUPLICATE_EVENT_ID", Severity.CRITICAL.value, "event",
                    "the same immutable event id appears twice",
                    entity_id=row["event_id"], tenant_id=row["tenant_id"],
                ))
            seen.add(row["event_id"])
            if not row["checksum"]:
                self.report.add(Finding(
                    "EVENT_MISSING_CHECKSUM", Severity.FAIL.value, "event",
                    "event has no checksum", entity_id=row["event_id"],
                    tenant_id=row["tenant_id"],
                ))
        if rows and not any(f.entity_type == "event" for f in self.report.findings):
            self.report.add(Finding("EVENT_CHECKSUM_CHAIN", Severity.PASS.value, "event",
                                    f"{len(rows)} event(s) carry a checksum"))

    def check_event_debt(self) -> None:
        """Events with no projection debt where a projection exists anyway.

        An event with neither debt nor projection is a legacy observe() with no
        remember(), which is legal — so only the inconsistent case is reported.
        """
        clause, params = self._tenant_clause("e")
        rows = self._q(
            "SELECT e.event_id, e.tenant_id FROM cognitive_events e "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM projection_outbox o WHERE o.event_id = e.event_id)"
            f"  AND {self._projection_exists('e.event_id')}"
            f"{clause}",
            params,
        )
        # One finding per row, for a condition the docstring above calls legal
        # and the finding itself calls expected. Run 6 measured what that means
        # at scale: 234,745 identical INFO findings and a 111 MB report for a
        # 2.4 GB database. The same sentence, a quarter of a million times, in a
        # file no operator can open. Individual rows are still worth naming when
        # there are few enough to act on one by one; past that the count is the
        # only new information, so report the count.
        for row in rows[:_MAX_ROW_FINDINGS]:
            self.report.add(Finding(
                "EVENT_PROJECTED_WITHOUT_DEBT", Severity.INFO.value, "event",
                "projection exists but no outbox row records it was owed "
                "(legacy write path)",
                entity_id=row["event_id"], tenant_id=row["tenant_id"], repairable=False,
                suggested_action="expected while the legacy path is primary",
            ))
        if len(rows) > _MAX_ROW_FINDINGS:
            self.report.add(Finding(
                "EVENT_PROJECTED_WITHOUT_DEBT_BULK", Severity.INFO.value, "event",
                f"{len(rows):,} event(s) carry a projection with no outbox debt "
                f"(legacy write path); {_MAX_ROW_FINDINGS} listed individually",
                evidence={"total": len(rows), "listed": _MAX_ROW_FINDINGS},
                repairable=False,
                suggested_action="expected while the legacy path is primary",
            ))

    def check_orphan_jobs(self) -> None:
        clause, params = self._tenant_clause("o")
        for row in self._q(
            "SELECT o.job_id, o.event_id, o.tenant_id FROM projection_outbox o "
            "WHERE NOT EXISTS (SELECT 1 FROM cognitive_events e WHERE e.event_id = o.event_id)"
            f"{clause}",
            params,
        ):
            self.report.add(Finding(
                "OUTBOX_WITHOUT_EVENT", Severity.CRITICAL.value, "outbox",
                "projection job references an event that does not exist",
                entity_id=row["job_id"], tenant_id=row["tenant_id"],
                evidence={"event_id": row["event_id"]}, repairable=False,
                suggested_action="manual review: events are append-only, so this "
                                 "means the database was edited or restored inconsistently",
            ))

    # -- ledger and projections -------------------------------------------

    def check_ledger_consistency(self) -> None:
        if self.report.counts.get("ledger", 0) == 0:
            return
        clause, params = self._tenant_clause("l")

        for row in self._q(
            "SELECT l.* FROM projection_ledger l "
            "WHERE NOT EXISTS (SELECT 1 FROM cognitive_events e WHERE e.event_id = l.event_id)"
            f"{clause}",
            params,
        ):
            self.report.add(Finding(
                "LEDGER_WITHOUT_EVENT", Severity.CRITICAL.value, "ledger",
                "ledger row vouches for a projection of an event that does not exist",
                entity_id=row["projection_key"], tenant_id=row["tenant_id"], repairable=False,
            ))

        for row in self._q(
            "SELECT l.*, e.tenant_id AS event_tenant FROM projection_ledger l "
            "JOIN cognitive_events e ON e.event_id = l.event_id "
            f"WHERE l.tenant_id != e.tenant_id{clause}",
            params,
        ):
            self.report.add(Finding(
                "LEDGER_TENANT_MISMATCH", Severity.CRITICAL.value, "ledger",
                "ledger tenant differs from its event's tenant",
                entity_id=row["projection_key"], tenant_id=row["tenant_id"],
                evidence={"ledger_tenant": row["tenant_id"],
                          "event_tenant": row["event_tenant"]},
                repairable=False,
                suggested_action="manual review: never repaired automatically",
            ))

        for row in self._q(
            "SELECT l.* FROM projection_ledger l "
            "WHERE l.projection_type = 'cognitive_memory'"
            f"  AND NOT {self._projection_exists('l.event_id')}"
            f"{clause}",
            params,
        ):
            self.report.add(Finding(
                "LEDGER_WITHOUT_PROJECTION", Severity.FAIL.value, "ledger",
                "ledger claims a projection was built but none exists",
                entity_id=row["projection_key"], tenant_id=row["tenant_id"], repairable=False,
                evidence={"event_id": row["event_id"]},
                suggested_action="manual review: the ledger must never outlive its projection",
            ))

    def check_projection_consistency(self) -> None:
        clause, params = self._tenant_clause("o")
        for row in self._q(
            "SELECT o.* FROM projection_outbox o "
            "WHERE o.status = ? AND o.projection_type = 'cognitive_memory'"
            f"  AND NOT {self._projection_exists('o.event_id')}"
            f"{clause}",
            (JobStatus.COMPLETED.value, *params),
        ):
            self.report.add(Finding(
                "COMPLETED_WITHOUT_PROJECTION", Severity.FAIL.value, "projection",
                "job is completed but its target projection does not exist",
                entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=True,
                evidence={"event_id": row["event_id"]},
                suggested_action="reset the job to pending and let the worker rebuild it",
            ))

        for row in self._q(
            "SELECT o.* FROM projection_outbox o "
            "WHERE o.status = ? AND EXISTS ("
            "  SELECT 1 FROM projection_ledger l WHERE l.projection_key = o.projection_key)"
            f"{clause}",
            (JobStatus.SKIPPED.value, *params),
        ):
            self.report.add(Finding(
                "SKIPPED_WITH_LEDGER", Severity.FAIL.value, "projection",
                "job was skipped but a ledger row claims a projection was built",
                entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=False,
            ))

    # -- dependencies ------------------------------------------------------

    def check_dependencies(self) -> None:
        for cycle in detect_cycles():
            self.report.add(Finding(
                "DEPENDENCY_CYCLE", Severity.CRITICAL.value, "dependency",
                cycle, repairable=False,
                suggested_action="manual review: never resolved by deleting an edge",
            ))

        clause, params = self._tenant_clause("child")
        for child_type in (ProjectionType.CONTEXT_BLOCK, ProjectionType.CHECKPOINT_REFERENCE):
            parents = dependencies_of(child_type)
            if not parents:
                continue
            parent = parents[0].value
            for row in self._q(
                "SELECT child.* FROM projection_outbox child "
                "WHERE child.projection_type = ? AND child.status = ? AND EXISTS ("
                "  SELECT 1 FROM projection_outbox p WHERE p.event_id = child.event_id "
                "    AND p.projection_type = ? AND p.status = ?)"
                f"{clause}",
                (child_type.value, JobStatus.PENDING.value, parent,
                 JobStatus.DEAD_LETTER.value, *params),
            ):
                self.report.add(Finding(
                    "CHILD_PENDING_WITH_DEAD_PARENT", Severity.WARN.value, "dependency",
                    "child job is still pending although its parent is dead-lettered",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=True,
                    evidence={"parent_type": parent},
                    suggested_action="move the child to dead-letter as blocked",
                ))

            for row in self._q(
                "SELECT child.* FROM projection_outbox child "
                "WHERE child.projection_type = ? AND child.status = ? AND NOT EXISTS ("
                "  SELECT 1 FROM projection_outbox p WHERE p.event_id = child.event_id "
                "    AND p.projection_type = ? AND p.status IN (?,?))"
                f"{clause}",
                (child_type.value, JobStatus.COMPLETED.value, parent,
                 JobStatus.COMPLETED.value, JobStatus.SKIPPED.value, *params),
            ):
                self.report.add(Finding(
                    "CHILD_COMPLETED_BEFORE_PARENT", Severity.FAIL.value, "dependency",
                    "child projection completed although its parent has not",
                    entity_id=row["job_id"], tenant_id=row["tenant_id"], repairable=False,
                ))

    # -- shadow ------------------------------------------------------------

    def check_shadow(self) -> None:
        if self.report.counts.get("shadow", 0) == 0:
            self.report.add(Finding("SHADOW_NOT_IN_USE", Severity.INFO.value, "shadow",
                                    "no shadow projections on this database"))
            return

        clause, params = self._tenant_clause("s")
        total = self.report.counts["shadow"]

        for row in self._q(
            "SELECT s.* FROM shadow_memories s WHERE s.comparison_status IS NULL"
            f"{clause}", params
        ):
            self.report.add(Finding(
                "SHADOW_COMPARISON_MISSING", Severity.WARN.value, "shadow",
                "shadow projection has never been compared against legacy",
                entity_id=row["shadow_projection_key"], tenant_id=row["tenant_id"],
                repairable=True, suggested_action="re-run the shadow comparison",
            ))

        bad = [
            r for r in self._q(
                "SELECT s.* FROM shadow_memories s WHERE s.comparison_status IS NOT NULL "
                f"AND s.comparison_status NOT IN (?,?){clause}",
                (ComparisonStatus.MATCH.value, ComparisonStatus.MATCH_NORMALIZED.value, *params),
            )
        ]
        for row in bad:
            self.report.add(Finding(
                "SHADOW_MISMATCH", Severity.FAIL.value, "shadow",
                f"shadow differs from legacy: {row['comparison_status']}",
                entity_id=row["shadow_projection_key"], tenant_id=row["tenant_id"],
                evidence={"status": row["comparison_status"]}, repairable=False,
                suggested_action="do not cut over until this is explained",
            ))

        for row in self._q(
            "SELECT s.*, e.tenant_id AS event_tenant FROM shadow_memories s "
            "JOIN cognitive_events e ON e.event_id = s.source_event_id "
            f"WHERE s.tenant_id != e.tenant_id{clause}", params
        ):
            self.report.add(Finding(
                "SHADOW_TENANT_MISMATCH", Severity.CRITICAL.value, "shadow",
                "shadow projection tenant differs from its event's tenant",
                entity_id=row["shadow_projection_key"], tenant_id=row["tenant_id"],
                repairable=False,
            ))

        leaked = self._scalar(
            "SELECT COUNT(*) FROM cognitive_memories m JOIN shadow_memories s "
            "ON m.memory_id = s.shadow_projection_key", default=0
        )
        if leaked:
            self.report.add(Finding(
                "SHADOW_LEAKED_INTO_PRODUCTION", Severity.CRITICAL.value, "shadow",
                f"{leaked} shadow row(s) present in the production memory table",
                repairable=False,
                suggested_action="manual review: production recall may be returning shadow data",
            ))

        if not bad and not leaked:
            self.report.add(Finding(
                "SHADOW_CONSISTENT", Severity.PASS.value, "shadow",
                f"{total} shadow projection(s), no mismatches",
                evidence={"shadow_records": total},
            ))

    # -- dangling references ----------------------------------------------

    def check_dangling_references(self) -> None:
        """Context blocks and checkpoints pointing at memories that are gone.

        Neither type has a builder, so nothing here is repairable: rebuilding a
        checkpoint against a different memory version would produce a different
        checkpoint wearing the same name.
        """
        tables = {
            r[0] for r in self._q("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "context_blocks" in tables:
            dangling = self._scalar(
                "SELECT COUNT(*) FROM context_blocks b WHERE b.memory_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_memories m "
                "                WHERE m.memory_id = b.memory_id)", default=0
            )
            if dangling:
                self.report.add(Finding(
                    "CONTEXT_BLOCK_DANGLING", Severity.WARN.value, "context",
                    f"{dangling} context block(s) reference a missing memory",
                    repairable=False, suggested_action="UNSUPPORTED_REPAIR: no builder exists",
                ))
        if "agent_checkpoints" in tables:
            dangling = self._scalar(
                "SELECT COUNT(*) FROM agent_checkpoints c WHERE c.memory_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_memories m "
                "                WHERE m.memory_id = c.memory_id)", default=0
            )
            if dangling:
                self.report.add(Finding(
                    "CHECKPOINT_DANGLING", Severity.WARN.value, "checkpoint",
                    f"{dangling} checkpoint(s) reference a missing memory version",
                    repairable=False,
                    suggested_action="UNSUPPORTED_REPAIR: a checkpoint pins a version and "
                                     "must never be repointed automatically",
                ))


__all__ = ["DeepDoctor", "DoctorReport", "Finding", "Severity"]
