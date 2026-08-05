"""Reconciliation — turning findings into audited, reversible repair.

Doctor never writes. This module takes its findings, builds a plan, and only
acts when told to. Three properties it is designed around:

* **Dry run is the default.** A recovery tool whose default is to act is a
  recovery tool that gets run by accident.
* **Preconditions are re-checked at apply time.** State can move between the
  scan and the repair; an action whose world changed is skipped as a conflict
  rather than applied to a situation nobody diagnosed.
* **Idempotent.** Running twice changes nothing the second time, because every
  action's precondition stops being true once it has been applied.

What is never automatic: deleting a raw event, editing a tenant, changing a
security label, guessing provenance, repointing a checkpoint at another memory
version, breaking a dependency cycle, building an unsupported projection, or
deleting an orphaned projection before a human has looked at it.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .diagnostics import DeepDoctor, DoctorReport, Finding, Severity
from .outbox import JobStatus
from .projection_capability import is_supported


class RiskLevel(str, Enum):
    SAFE = "safe"            # derived state, deterministically reproducible
    MODERATE = "moderate"    # re-runs work that may cost time
    UNSAFE = "unsafe"        # never automatic


class ActionStatus(str, Enum):
    PLANNED = "planned"
    APPLIED = "applied"
    SKIPPED_DRY_RUN = "skipped_dry_run"
    CONFLICT = "conflict"        # precondition no longer true
    REFUSED = "refused"          # policy forbids automating this
    FAILED = "failed"


@dataclass(slots=True)
class RepairAction:
    finding_code: str
    entity_id: str | None
    precondition: str
    action: str
    expected_postcondition: str
    risk_level: str
    tenant_id: str | None = None
    action_id: str = field(default_factory=lambda: f"act_{uuid.uuid4().hex[:12]}")
    status: str = ActionStatus.PLANNED.value
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "finding_code": self.finding_code,
            "entity_id": self.entity_id,
            "tenant_id": self.tenant_id,
            "precondition": self.precondition,
            "action": self.action,
            "expected_postcondition": self.expected_postcondition,
            "risk_level": self.risk_level,
            "status": self.status,
            "detail": self.detail,
        }


#: Finding codes this engine may repair without a human, and how.
#:
#: Everything absent from this table is refused by default. An allow-list is
#: the only safe shape here: a deny-list would silently automate whatever a
#: future check happens to add.
_REPAIRABLE: dict[str, tuple[str, str, str, str]] = {
    # code: (precondition, action, postcondition, risk)
    "JOB_STALE_LEASE": (
        "job is in_progress and its lease has expired",
        "clear locked_by/locked_at and return the job to pending",
        "job is pending and claimable",
        RiskLevel.SAFE.value,
    ),
    "JOB_CLAIMED_WITHOUT_OWNER": (
        "job is in_progress with no lease owner",
        "return the job to pending",
        "job is pending and claimable",
        RiskLevel.SAFE.value,
    ),
    "JOB_ATTEMPTS_EXCEEDED": (
        "attempts exceed the maximum but status is not dead_letter",
        "move the job to dead_letter, preserving attempts and last_error",
        "job is dead_letter with its history intact",
        RiskLevel.SAFE.value,
    ),
    "COMPLETED_WITHOUT_PROJECTION": (
        "job is completed, its event exists, and no projection was built",
        "reset the job to pending so the worker rebuilds the projection",
        "job is pending; the worker will rebuild it",
        RiskLevel.MODERATE.value,
    ),
    "CHILD_PENDING_WITH_DEAD_PARENT": (
        "child job is pending and its parent is dead-lettered",
        "move the child to dead_letter recorded as blocked by its parent",
        "child is dead_letter and no longer retried",
        RiskLevel.SAFE.value,
    ),
    "SHADOW_COMPARISON_MISSING": (
        "shadow projection exists with no comparison_status",
        "re-run the legacy-versus-shadow comparison for this event",
        "the shadow row carries a comparison result",
        RiskLevel.SAFE.value,
    ),
}

#: Invariant: every finding the doctor marks `repairable` must appear above.
#: A finding that advertises a repair and produces no action is worse than one
#: that says nothing — it tells an operator the system will handle it.
REPAIRABLE_CODES = frozenset(_REPAIRABLE)


@dataclass(slots=True)
class ReconciliationResult:
    dry_run: bool
    started_at: float
    operator: str
    before: DoctorReport
    after: DoctorReport | None = None
    actions: list[RepairAction] = field(default_factory=list)
    counts_before: dict[str, int] = field(default_factory=dict)
    counts_after: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.status] = out.get(a.status, 0) + 1
        return out

    @property
    def applied(self) -> int:
        return sum(1 for a in self.actions if a.status == ActionStatus.APPLIED.value)

    @property
    def refused(self) -> int:
        return sum(1 for a in self.actions if a.status == ActionStatus.REFUSED.value)

    @property
    def changed_rows(self) -> bool:
        return self.counts_before != self.counts_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "operator": self.operator,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 4),
            "counts_before": self.counts_before,
            "counts_after": self.counts_after,
            "actions_by_status": self.by_status(),
            "applied": self.applied,
            "refused": self.refused,
            "actions": [a.as_dict() for a in self.actions],
            "findings_before": [f.as_dict() for f in self.before.findings],
            "findings_after": (
                [f.as_dict() for f in self.after.findings] if self.after else None
            ),
            "exit_code_before": self.before.exit_code,
            "exit_code_after": self.after.exit_code if self.after else None,
        }

    def render(self, width: int = 78) -> str:
        mode = "DRY RUN — nothing was changed" if self.dry_run else "REPAIR APPLIED"
        lines = [
            "=" * width,
            f"  RECONCILIATION — {mode}",
            "=" * width,
            f"  operator          : {self.operator}",
            f"  findings before   : {len(self.before.findings)}",
            f"  actions planned   : {len(self.actions)}",
            f"  applied           : {self.applied}",
            f"  refused by policy : {self.refused}",
        ]
        for status, n in sorted(self.by_status().items()):
            lines.append(f"    {status:<22} {n:>5}")
        if self.actions:
            lines.append("  " + "-" * (width - 4))
            for a in self.actions:
                lines.append(f"  [{a.status:<16}] {a.finding_code:<32} risk={a.risk_level}")
                lines.append(f"      {a.action[:width - 10]}")
                if a.detail:
                    lines.append(f"      note: {a.detail[:width - 16]}")
        lines.append("  " + "-" * (width - 4))
        lines.append(f"  rows before : {self.counts_before}")
        lines.append(f"  rows after  : {self.counts_after}")
        if self.after:
            lines.append(f"  findings after : {len(self.after.findings)} "
                         f"(exit {self.after.exit_code})")
        lines.append("=" * width)
        return "\n".join(lines)


class ReconciliationEngine:
    """Plans and, when asked, applies repairs to derived state only."""

    def __init__(self, conn: sqlite3.Connection, *, tenant_id: str | None = None,
                 operator: str = "cli", runtime: Any = None) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.tenant_id = tenant_id
        self.operator = operator
        #: Needed only by repairs that must reach more than one store, such as
        #: re-running a shadow comparison. Absent means those are refused with
        #: a stated reason rather than half-attempted.
        self.runtime = runtime

    # -- plan --------------------------------------------------------------

    def plan(self, report: DoctorReport) -> list[RepairAction]:
        actions: list[RepairAction] = []
        for finding in report.findings:
            if finding.severity in (Severity.PASS.value, Severity.INFO.value):
                continue
            recipe = _REPAIRABLE.get(finding.code)
            if recipe is None:
                if finding.severity in (Severity.FAIL.value, Severity.CRITICAL.value):
                    actions.append(RepairAction(
                        finding_code=finding.code, entity_id=finding.entity_id,
                        tenant_id=finding.tenant_id,
                        precondition="n/a",
                        action="no automatic repair for this finding",
                        expected_postcondition="unchanged",
                        risk_level=RiskLevel.UNSAFE.value,
                        status=ActionStatus.REFUSED.value,
                        detail=finding.suggested_action or "requires human review",
                    ))
                continue
            precondition, action, postcondition, risk = recipe
            actions.append(RepairAction(
                finding_code=finding.code, entity_id=finding.entity_id,
                tenant_id=finding.tenant_id, precondition=precondition,
                action=action, expected_postcondition=postcondition, risk_level=risk,
            ))
        return actions

    # -- apply -------------------------------------------------------------

    def _job(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projection_outbox WHERE job_id=?", (job_id,)
        ).fetchone()

    def _still_applies(self, action: RepairAction) -> bool:
        """Re-check the precondition against live state.

        The gap between scanning and repairing is exactly where a worker may
        have fixed the problem itself. Applying anyway would undo real work.
        """
        job = self._job(action.entity_id) if action.entity_id else None
        code = action.finding_code
        if code in ("JOB_STALE_LEASE", "JOB_CLAIMED_WITHOUT_OWNER"):
            return bool(job and job["status"] == JobStatus.IN_PROGRESS.value)
        if code == "JOB_ATTEMPTS_EXCEEDED":
            return bool(job and job["status"] != JobStatus.DEAD_LETTER.value)
        if code == "COMPLETED_WITHOUT_PROJECTION":
            if not job or job["status"] != JobStatus.COMPLETED.value:
                return False
            if not is_supported(job["projection_type"]):
                return False
            # Indexed lookup through the link table. This runs once per action,
            # so a repair plan over 10,000 findings used to mean 10,000 full
            # scans of every memory.
            built = self.conn.execute(
                "SELECT 1 FROM memory_source_events s "
                "JOIN cognitive_memories m"
                "  ON m.memory_id = s.memory_id AND m.version = s.version "
                "WHERE s.event_id = ? LIMIT 1",
                (job["event_id"],),
            ).fetchone()
            return built is None
        if code == "CHILD_PENDING_WITH_DEAD_PARENT":
            return bool(job and job["status"] == JobStatus.PENDING.value)
        if code == "SHADOW_COMPARISON_MISSING":
            row = self.conn.execute(
                "SELECT comparison_status FROM shadow_memories WHERE shadow_projection_key=?",
                (action.entity_id,),
            ).fetchone()
            return bool(row and row["comparison_status"] is None)
        return False

    def _execute(self, action: RepairAction) -> None:
        code = action.finding_code
        now = time.time()
        if code in ("JOB_STALE_LEASE", "JOB_CLAIMED_WITHOUT_OWNER"):
            self.conn.execute(
                "UPDATE projection_outbox SET status=?, locked_by=NULL, locked_at=NULL, "
                "available_at=? WHERE job_id=?",
                (JobStatus.PENDING.value, now, action.entity_id),
            )
        elif code == "JOB_ATTEMPTS_EXCEEDED":
            self.conn.execute(
                "UPDATE projection_outbox SET status=?, locked_by=NULL, locked_at=NULL "
                "WHERE job_id=?",
                (JobStatus.DEAD_LETTER.value, action.entity_id),
            )
        elif code == "COMPLETED_WITHOUT_PROJECTION":
            self.conn.execute(
                "UPDATE projection_outbox SET status=?, attempts=0, completed_at=NULL, "
                "locked_by=NULL, locked_at=NULL, available_at=? WHERE job_id=?",
                (JobStatus.PENDING.value, now, action.entity_id),
            )
        elif code == "CHILD_PENDING_WITH_DEAD_PARENT":
            self.conn.execute(
                "UPDATE projection_outbox SET status=?, last_error=? WHERE job_id=?",
                (JobStatus.DEAD_LETTER.value,
                 "blocked: parent projection is dead-lettered", action.entity_id),
            )
        elif code == "SHADOW_COMPARISON_MISSING":
            # Deterministic and derived, so re-running it is safe by definition —
            # but it needs the runtime to reach both tables.
            if self.runtime is None:
                raise ValueError(
                    "re-running a shadow comparison needs a runtime; "
                    "construct ReconciliationEngine(runtime=...)"
                )
            from .shadow_runner import compare_event

            row = self.conn.execute(
                "SELECT source_event_id FROM shadow_memories WHERE shadow_projection_key=?",
                (action.entity_id,),
            ).fetchone()
            if row is None:
                raise ValueError("shadow row disappeared between plan and apply")
            compare_event(self.runtime, row["source_event_id"])
        else:  # pragma: no cover - guarded by the allow-list
            raise ValueError(f"no executor for {code}")
        self.conn.commit()

    def run(self, *, deep: bool = True, repair: bool = False,
            event_id: str | None = None) -> ReconciliationResult:
        started = time.perf_counter()
        doctor = DeepDoctor(self.conn, tenant_id=self.tenant_id)
        before = doctor.run(deep=deep)

        result = ReconciliationResult(
            dry_run=not repair, started_at=time.time(), operator=self.operator,
            before=before, counts_before=dict(before.counts),
        )
        result.actions = self.plan(before)
        if event_id:
            result.actions = [a for a in result.actions if a.entity_id == event_id]

        for action in result.actions:
            if action.status == ActionStatus.REFUSED.value:
                continue
            if not repair:
                action.status = ActionStatus.SKIPPED_DRY_RUN.value
                continue
            if not self._still_applies(action):
                action.status = ActionStatus.CONFLICT.value
                action.detail = "precondition no longer true when the repair ran"
                continue
            try:
                self._execute(action)
                action.status = ActionStatus.APPLIED.value
            except Exception as exc:
                self.conn.rollback()
                action.status = ActionStatus.FAILED.value
                action.detail = f"{type(exc).__name__}: {exc}"

        if repair:
            result.after = DeepDoctor(self.conn, tenant_id=self.tenant_id).run(deep=deep)
            result.counts_after = dict(result.after.counts)
        else:
            result.counts_after = dict(before.counts)

        result.duration_s = time.perf_counter() - started
        return result


def write_audit(result: ReconciliationResult, reports_dir: Path) -> tuple[Path, Path]:
    """Every repair leaves a record. `--repair` without one is not allowed."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(result.started_at))
    json_path = reports_dir / f"reconciliation_{stamp}.json"
    md_path = reports_dir / f"reconciliation_{stamp}.md"
    json_path.write_text(
        json.dumps(result.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(
        f"# Reconciliation {stamp}\n\n```\n{result.render()}\n```\n\n"
        f"Raw findings and actions: `{json_path.name}`\n",
        encoding="utf-8",
    )
    return json_path, md_path


__all__ = [
    "ActionStatus",
    "ReconciliationEngine",
    "ReconciliationResult",
    "RepairAction",
    "RiskLevel",
    "write_audit",
]
