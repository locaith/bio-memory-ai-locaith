"""Corruption and repair matrix: does doctor see it, and is repair safe?

Every case plants a specific inconsistency and asserts the exact finding code.
A doctor that reports "something is wrong" is not useful; the code is what a
runbook keys off.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive import diagnostics
from bio_agent_os.cognitive.diagnostics import _MAX_ROW_FINDINGS, DeepDoctor, Severity
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import EventRecord, MemoryType
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob, projection_key
from bio_agent_os.cognitive.projection_capability import unsupported_types
from bio_agent_os.cognitive.projection_registry import ProjectionType
from bio_agent_os.cognitive.reconciliation import (
    ActionStatus,
    ReconciliationEngine,
    RiskLevel,
    write_audit,
)
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.shadow_runner import run_shadow_comparison, shadow_worker

MEMORY = ProjectionType.COGNITIVE_MEMORY.value
CONTEXT = ProjectionType.CONTEXT_BLOCK.value


@pytest.fixture()
def db(tmp_path) -> str:
    return str(tmp_path / "doctor.db")


@pytest.fixture()
def os_(db) -> MemoryOS:
    return MemoryOS(db, projection_mode="shadow")


def _observe(os_: MemoryOS, content="content", tenant="t1", event_id=None):
    event = os_.observe(tenant_id=tenant, actor="a", source="unit", content=content)
    os_.remember(event=event, memory_type=MemoryType.EPISODIC, content=content)
    return event


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


def _scan(os_: MemoryOS, *, deep=True, tenant=None):
    return DeepDoctor(os_.events.conn, tenant_id=tenant).run(deep=deep)


def _counts(db_path: str) -> dict[str, int]:
    """Row counts read from a fresh connection on the closed file."""
    conn = sqlite3.connect(db_path)
    out = {}
    for table in ("cognitive_events", "projection_outbox", "cognitive_memories",
                  "projection_ledger", "shadow_memories"):
        try:
            out[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = 0
    conn.close()
    return out


# ==========================================================================
# clean baseline
# ==========================================================================


def test_a_healthy_database_passes(os_):
    _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    run_shadow_comparison(os_, [r["event_id"] for r in
                                os_.events.conn.execute("SELECT event_id FROM cognitive_events")])
    report = _scan(os_)
    assert report.exit_code == 0
    assert report.worst in (Severity.PASS.value, Severity.INFO.value, Severity.WARN.value)


def test_exit_codes_map_to_severity(os_):
    report = _scan(os_)
    assert report.exit_code == 0
    report.add(type(report.findings[0])("X", Severity.FAIL.value, "test", "d"))
    assert report.exit_code == 1
    report.add(type(report.findings[0])("Y", Severity.CRITICAL.value, "test", "d"))
    assert report.exit_code == 2


def test_a_doctor_that_cannot_finish_never_looks_healthy(os_):
    report = _scan(os_)
    report.scan_error = "database vanished mid-scan"
    assert report.exit_code == 3
    assert "SCAN DID NOT COMPLETE" in report.render()


# ==========================================================================
# capability awareness
# ==========================================================================


def test_unsupported_capabilities_are_reported_as_capability_gaps(os_):
    report = _scan(os_)
    caps = [f for f in report.findings if f.code == "UNSUPPORTED_CAPABILITY"]
    assert len(caps) == len(unsupported_types())
    for f in caps:
        assert f.severity == Severity.INFO.value, "unsupported is not damage"
        assert not f.repairable
        assert f.entity_id in unsupported_types()


def test_summary_never_hides_unsupported_types(os_):
    data = _scan(os_).as_dict()
    assert data["supported_projection_types"] == [MEMORY]
    assert set(data["unsupported_projection_types"]) == set(unsupported_types())
    assert "unsupported" in _scan(os_).render()


# ==========================================================================
# corruption fixtures
# ==========================================================================


def test_debt_without_event_is_critical(os_):
    os_.events.outbox.enqueue(
        ProjectionJob(event_id="ghost", projection_type=MEMORY, tenant_id="t1"), commit=True
    )
    report = _scan(os_)
    assert "OUTBOX_WITHOUT_EVENT" in _codes(report)
    assert report.exit_code == 2


def test_completed_job_without_projection(os_):
    event = _observe(os_)
    job = os_.events.outbox.by_event(event.event_id)[0]
    os_.events.outbox.complete(job.job_id)
    os_.memories.conn.execute("DELETE FROM cognitive_memories")
    os_.memories.conn.commit()

    report = _scan(os_)
    assert "COMPLETED_WITHOUT_PROJECTION" in _codes(report)
    finding = next(f for f in report.findings if f.code == "COMPLETED_WITHOUT_PROJECTION")
    assert finding.repairable


def test_ledger_without_projection(os_):
    event = _observe(os_)
    worker_for(os_, worker_id="w", lease_seconds=0).run_once()
    os_.memories.conn.execute("DELETE FROM cognitive_memories")
    os_.memories.conn.commit()
    assert "LEDGER_WITHOUT_PROJECTION" in _codes(_scan(os_))


def test_ledger_tenant_mismatch_is_critical_and_not_repairable(os_):
    event = _observe(os_)
    worker_for(os_, worker_id="w", lease_seconds=0).run_once()
    os_.memories.conn.execute("UPDATE projection_ledger SET tenant_id='other-tenant'")
    os_.memories.conn.commit()

    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "LEDGER_TENANT_MISMATCH")
    assert finding.severity == Severity.CRITICAL.value
    assert not finding.repairable, "a tenant is never repaired by guessing"


def test_invalid_projection_key_is_detected(os_):
    _observe(os_)
    os_.events.conn.execute("UPDATE projection_outbox SET projection_key='deadbeef'")
    os_.events.conn.commit()
    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "INVALID_PROJECTION_KEY")
    assert not finding.repairable


def test_invalid_projection_version_is_detected(os_):
    _observe(os_)
    os_.events.conn.execute("UPDATE projection_outbox SET projection_version=0")
    os_.events.conn.commit()
    assert "INVALID_PROJECTION_VERSION" in _codes(_scan(os_))


def test_stale_lease_is_detected_and_repairable(os_):
    _observe(os_)
    os_.events.outbox.claim("dead-worker")
    os_.events.conn.execute(
        "UPDATE projection_outbox SET locked_at=?", (time.time() - 100_000,)
    )
    os_.events.conn.commit()
    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "JOB_STALE_LEASE")
    assert finding.repairable


def test_claimed_without_owner_is_detected(os_):
    _observe(os_)
    os_.events.outbox.claim("w")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_by=NULL")
    os_.events.conn.commit()
    assert "JOB_CLAIMED_WITHOUT_OWNER" in _codes(_scan(os_))


def test_attempts_exceeded_without_dead_letter(os_):
    _observe(os_)
    os_.events.conn.execute("UPDATE projection_outbox SET attempts=99, status=?",
                            (JobStatus.PENDING.value,))
    os_.events.conn.commit()
    assert "JOB_ATTEMPTS_EXCEEDED" in _codes(_scan(os_))


def test_dead_letter_without_reason(os_):
    _observe(os_)
    os_.events.conn.execute(
        "UPDATE projection_outbox SET status=?, last_error=NULL",
        (JobStatus.DEAD_LETTER.value,),
    )
    os_.events.conn.commit()
    assert "DEAD_LETTER_WITHOUT_REASON" in _codes(_scan(os_))


def test_skipped_job_with_a_ledger_row(os_):
    event = _observe(os_)
    worker_for(os_, worker_id="w", lease_seconds=0).run_once()
    os_.events.conn.execute("UPDATE projection_outbox SET status=?",
                            (JobStatus.SKIPPED.value,))
    os_.events.conn.commit()
    assert "SKIPPED_WITH_LEDGER" in _codes(_scan(os_))


def test_child_pending_with_a_dead_parent(os_):
    event = os_.events.append(
        EventRecord(tenant_id="t1", actor="a", source="u",
                    payload={"content": "x"}, event_id="evt-dep"),
        projection_types=(MEMORY, CONTEXT),
    )
    parent = [j for j in os_.events.outbox.by_event("evt-dep")
              if j.projection_type == MEMORY][0]
    os_.events.outbox.fail(parent.job_id, "fatal", max_attempts=0)
    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "CHILD_PENDING_WITH_DEAD_PARENT")
    assert finding.repairable


def test_duplicate_event_ids_are_impossible_at_the_storage_layer(os_):
    """SQLite refuses this outright, which is stronger than detecting it.

    `event_id` is the primary key, so a duplicate cannot be written at all. The
    doctor's DUPLICATE_EVENT_ID check stays as defence in depth for a backend
    where the constraint might be looser, but on SQLite the storage layer is
    the guarantee — and a test that asserts the guarantee is worth more than
    one that asserts we would have noticed.
    """
    _observe(os_)
    with pytest.raises(sqlite3.IntegrityError):
        os_.events.conn.execute(
            "INSERT INTO cognitive_events(event_id, tenant_id, workspace_id, actor, source,"
            " payload_json, trust_tier, security_label, valid_from, valid_to, observed_at,"
            " checksum, metadata_json, modality, epistemic_status)"
            " SELECT event_id, tenant_id, workspace_id, actor, source, payload_json,"
            " trust_tier, security_label, valid_from, valid_to, observed_at,"
            " checksum || 'x', metadata_json, modality, epistemic_status"
            " FROM cognitive_events LIMIT 1"
        )
    os_.events.conn.rollback()


def test_missing_event_checksum_is_detected(os_):
    """The reachable half of event integrity: a row whose checksum went away."""
    _observe(os_)
    os_.events.conn.execute("DROP TRIGGER IF EXISTS cognitive_events_no_update")
    os_.events.conn.execute("UPDATE cognitive_events SET checksum=''")
    os_.events.conn.commit()
    assert "EVENT_MISSING_CHECKSUM" in _codes(_scan(os_))


def test_shadow_mismatch_is_detected(os_):
    event = _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    os_.memories.conn.execute(
        "UPDATE shadow_memories SET comparison_status='CONTENT_MISMATCH'"
    )
    os_.memories.conn.commit()
    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "SHADOW_MISMATCH")
    assert finding.severity == Severity.FAIL.value
    assert not finding.repairable


def test_shadow_comparison_not_yet_run(os_):
    _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    report = _scan(os_)
    finding = next(f for f in report.findings if f.code == "SHADOW_COMPARISON_MISSING")
    assert finding.repairable


def test_shadow_tenant_mismatch_is_critical(os_):
    _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    os_.memories.conn.execute("UPDATE shadow_memories SET tenant_id='wrong'")
    os_.memories.conn.commit()
    assert "SHADOW_TENANT_MISMATCH" in _codes(_scan(os_))


def test_missing_index_is_reported(os_):
    _observe(os_)
    os_.events.conn.execute("DROP INDEX IF EXISTS idx_outbox_claimable")
    os_.events.conn.commit()
    assert "SCHEMA_MISSING_INDEX" in _codes(_scan(os_))


def test_sqlite_integrity_is_checked(os_):
    report = _scan(os_)
    assert "SQLITE_INTEGRITY" in _codes(report)
    finding = next(f for f in report.findings if f.code == "SQLITE_INTEGRITY")
    assert finding.severity == Severity.PASS.value


def test_legacy_projection_without_debt_is_info_not_damage(db):
    """The legacy path is still primary; its shape must not read as corruption."""
    legacy = MemoryOS(db)  # legacy mode
    _observe(legacy)
    report = DeepDoctor(legacy.events.conn).run(deep=True)
    finding = next(f for f in report.findings if f.code == "EVENT_PROJECTED_WITHOUT_DEBT")
    assert finding.severity == Severity.INFO.value
    assert report.exit_code == 0


def test_an_expected_condition_is_counted_not_listed_row_by_row(db):
    """Regression, Run 6.

    Every legacy write produces one of these, and the check produced one
    finding for each: 234,745 identical INFO lines and a 111 MB report for a
    2.4 GB database. Nothing was wrong — that is the point. The report was
    unopenable, and it grows with the data, so the bigger the database the
    less the diagnostic can be read.
    """
    legacy = MemoryOS(db)
    for i in range(_MAX_ROW_FINDINGS * 3):
        _observe(legacy, content=f"legacy row {i}")

    report = DeepDoctor(legacy.events.conn).run(deep=True)
    listed = [f for f in report.findings if f.code == "EVENT_PROJECTED_WITHOUT_DEBT"]
    bulk = [f for f in report.findings if f.code == "EVENT_PROJECTED_WITHOUT_DEBT_BULK"]

    assert len(listed) == _MAX_ROW_FINDINGS, (
        f"{len(listed)} rows listed individually; the cap is {_MAX_ROW_FINDINGS}")
    assert len(bulk) == 1, "the rows that were folded away must still be counted"
    # The count is the whole reason the roll-up exists: folding must not lose it.
    assert bulk[0].evidence["total"] == _MAX_ROW_FINDINGS * 3
    assert bulk[0].severity == Severity.INFO.value
    assert report.exit_code == 0


# ==========================================================================
# reconciliation
# ==========================================================================


def test_dry_run_changes_nothing(db, os_):
    _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    before = _counts(db)
    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=False)
    after = _counts(db)

    assert result.dry_run
    assert before == after, "a dry run must not touch a single row"
    assert all(a.status in (ActionStatus.SKIPPED_DRY_RUN.value, ActionStatus.REFUSED.value)
               for a in result.actions)
    job = os_.events.outbox.by_event(
        os_.events.conn.execute("SELECT event_id FROM cognitive_events").fetchone()[0]
    )[0]
    assert job.status == JobStatus.IN_PROGRESS.value, "the stale lease is still there"


def test_repair_releases_a_stale_lease(os_):
    event = _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    assert result.applied >= 1
    job = os_.events.outbox.by_event(event.event_id)[0]
    assert job.status == JobStatus.PENDING.value
    assert job.locked_by is None


def test_repair_is_idempotent(os_):
    _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    first = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    second = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    assert first.applied >= 1
    assert second.applied == 0, "a second run must be a no-op"


def test_repair_rebuilds_a_missing_projection(os_):
    event = _observe(os_)
    job = os_.events.outbox.by_event(event.event_id)[0]
    os_.events.outbox.complete(job.job_id)
    os_.memories.conn.execute("DELETE FROM cognitive_memories")
    os_.memories.conn.commit()

    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    assert any(a.finding_code == "COMPLETED_WITHOUT_PROJECTION"
               and a.status == ActionStatus.APPLIED.value for a in result.actions)
    assert os_.events.outbox.by_event(event.event_id)[0].status == JobStatus.PENDING.value

    shadow_worker(os_, worker_id="rebuild", lease_seconds=0).run_once()
    assert os_.shadow_memories.count() == 1


def test_forbidden_repairs_are_refused_with_a_reason(os_):
    _observe(os_)
    worker_for(os_, worker_id="w", lease_seconds=0).run_once()
    os_.memories.conn.execute("UPDATE projection_ledger SET tenant_id='wrong'")
    os_.memories.conn.commit()

    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    refused = [a for a in result.actions if a.finding_code == "LEDGER_TENANT_MISMATCH"]
    assert refused and refused[0].status == ActionStatus.REFUSED.value
    assert refused[0].risk_level == RiskLevel.UNSAFE.value
    assert os_.memories.conn.execute(
        "SELECT tenant_id FROM projection_ledger"
    ).fetchone()[0] == "wrong", "refused means untouched"


def test_repair_never_deletes_a_raw_event(db, os_):
    _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()
    before = _counts(db)["cognitive_events"]
    ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    assert _counts(db)["cognitive_events"] == before


def test_repair_does_not_touch_another_tenant(os_):
    a = _observe(os_, tenant="tenant-a", content="a")
    b = _observe(os_, tenant="tenant-b", content="b")
    for job in os_.events.outbox.pending():
        pass
    os_.events.outbox.claim("dead", limit=10)
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    ReconciliationEngine(os_.events.conn, tenant_id="tenant-a", operator="test").run(repair=True)
    a_job = os_.events.outbox.by_event(a.event_id)[0]
    b_job = os_.events.outbox.by_event(b.event_id)[0]
    assert a_job.status == JobStatus.PENDING.value
    assert b_job.status == JobStatus.IN_PROGRESS.value, "tenant-b must be untouched"


def test_a_precondition_that_changed_is_reported_as_conflict(os_):
    event = _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    engine = ReconciliationEngine(os_.events.conn, operator="test")
    report = DeepDoctor(os_.events.conn).run(deep=True)
    actions = engine.plan(report)

    # A worker fixes it between the scan and the repair.
    os_.events.conn.execute(
        "UPDATE projection_outbox SET status=?, locked_by=NULL, locked_at=NULL",
        (JobStatus.COMPLETED.value,),
    )
    os_.events.conn.commit()

    stale = [a for a in actions if a.finding_code == "JOB_STALE_LEASE"][0]
    assert not engine._still_applies(stale), "the precondition is gone"


def test_repair_writes_an_audit_report(os_, tmp_path):
    _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    result = ReconciliationEngine(os_.events.conn, operator="tuananh").run(repair=True)
    json_path, md_path = write_audit(result, tmp_path / "reports")

    assert json_path.exists() and md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["operator"] == "tuananh"
    assert data["dry_run"] is False
    assert data["findings_before"] and data["findings_after"] is not None
    assert data["counts_before"] and data["counts_after"]
    assert any(a["status"] == ActionStatus.APPLIED.value for a in data["actions"])


def test_every_action_carries_its_contract(os_):
    _observe(os_)
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()
    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=False)
    for action in result.actions:
        assert action.action_id.startswith("act_")
        assert action.finding_code
        assert action.precondition
        assert action.action
        assert action.expected_postcondition
        assert action.risk_level in {r.value for r in RiskLevel}


def test_every_repairable_finding_has_a_recipe(os_):
    """Regression guard for the bug found on 2026-08-05.

    Doctor marked 50 findings repairable while reconcile planned 0 actions:
    SHADOW_COMPARISON_MISSING advertised a repair that did not exist. A finding
    that says the system will handle it, and then does not, is worse than one
    that says nothing.
    """
    from bio_agent_os.cognitive.diagnostics import DeepDoctor as _D
    from bio_agent_os.cognitive.reconciliation import REPAIRABLE_CODES

    # Plant one of everything the doctor can call repairable.
    event = _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    os_.events.outbox.claim("dead")
    os_.events.conn.execute("UPDATE projection_outbox SET locked_at=?",
                            (time.time() - 100_000,))
    os_.events.conn.commit()

    report = _D(os_.events.conn).run(deep=True)
    advertised = {f.code for f in report.findings if f.repairable}
    assert advertised, "the fixture should produce repairable findings"
    missing = advertised - REPAIRABLE_CODES
    assert not missing, f"marked repairable with no recipe: {sorted(missing)}"


def test_shadow_comparison_is_re_run_by_repair(os_):
    event = _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    assert os_.memories.conn.execute(
        "SELECT comparison_status FROM shadow_memories"
    ).fetchone()[0] is None

    result = ReconciliationEngine(
        os_.events.conn, operator="test", runtime=os_
    ).run(repair=True)
    assert any(a.finding_code == "SHADOW_COMPARISON_MISSING"
               and a.status == ActionStatus.APPLIED.value for a in result.actions)
    assert os_.memories.conn.execute(
        "SELECT comparison_status FROM shadow_memories"
    ).fetchone()[0] == "MATCH"


def test_a_repair_needing_a_runtime_fails_loudly_without_one(os_):
    _observe(os_)
    shadow_worker(os_, worker_id="w", lease_seconds=0).run_once()
    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=True)
    failed = [a for a in result.actions if a.finding_code == "SHADOW_COMPARISON_MISSING"]
    assert failed and failed[0].status == ActionStatus.FAILED.value
    assert "needs a runtime" in failed[0].detail


def test_unsupported_types_never_get_a_repair_action(os_):
    os_.events.outbox.enqueue(
        ProjectionJob(event_id="e", projection_type=CONTEXT, tenant_id="t1"), commit=True
    )
    result = ReconciliationEngine(os_.events.conn, operator="test").run(repair=False)
    for action in result.actions:
        assert action.finding_code != "UNSUPPORTED_CAPABILITY"


# ==========================================================================
# durability and scale
# ==========================================================================


def test_findings_survive_a_reopen(db, os_):
    _observe(os_)
    os_.events.conn.execute("UPDATE projection_outbox SET attempts=99")
    os_.events.conn.commit()
    os_.events.conn.close()
    os_.memories.conn.close()

    reopened = MemoryOS(db, projection_mode="shadow")
    assert "JOB_ATTEMPTS_EXCEEDED" in _codes(DeepDoctor(reopened.events.conn).run(deep=True))


def test_quick_scan_is_cheaper_than_deep(os_):
    for i in range(20):
        _observe(os_, content=f"row {i}")
    quick = _scan(os_, deep=False)
    deep = _scan(os_, deep=True)
    assert quick.checks_run < deep.checks_run
    assert quick.queries < deep.queries


def test_tenant_scoped_scan_ignores_other_tenants(os_):
    _observe(os_, tenant="tenant-a")
    _observe(os_, tenant="tenant-b")
    os_.events.conn.execute(
        "UPDATE projection_outbox SET attempts=99 WHERE tenant_id='tenant-b'"
    )
    os_.events.conn.commit()

    scoped = _scan(os_, tenant="tenant-a")
    assert "JOB_ATTEMPTS_EXCEEDED" not in _codes(scoped)
    assert "JOB_ATTEMPTS_EXCEEDED" in _codes(_scan(os_))


def test_json_output_is_stable_for_monitoring(os_):
    _observe(os_)
    data = _scan(os_).as_dict()
    for key in ("deep", "duration_s", "checks_run", "exit_code", "worst_severity",
                "by_severity", "counts", "supported_projection_types",
                "unsupported_projection_types", "repairable_findings",
                "manual_review_required", "findings"):
        assert key in data
    json.dumps(data)  # must be serialisable


# ==========================================================================
# the scan must not be quadratic
#
# Three deep checks used a correlated `LIKE '%' || event_id || '%'`, which
# cannot use an index and so scanned every memory once per outer row. Measured
# at 1k/5k/10k events the deep scan took 0.78s / 23.7s / 98.7s: an exponent of
# 2.1, and an extrapolated 2.75 hours at 100k. It was found because it stalled
# a benchmark, which is a bad way to find it.
# ==========================================================================


def test_no_deep_check_scans_with_a_leading_wildcard():
    """A source-level guard, because the behavioural test below can only fail
    once the database is large enough to be slow — by which time the change is
    already merged.

    Inspects the SQL actually passed to `self._q`, via the AST. Grepping the
    file would flag the docstring that explains the bug, which is the wrong
    kind of strict.
    """
    import ast

    tree = ast.parse(Path(diagnostics.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    offenders = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and "LIKE '%'" in node.value
    ]
    assert not offenders, (
        "a leading-wildcard LIKE inside a per-row check makes the scan "
        f"O(N*M): {offenders}"
    )


def test_deep_scan_time_grows_sub_quadratically(tmp_path):
    """Four times the data must not cost sixteen times the scan.

    The bound is generous — a shared machine makes tight timing assertions
    flaky — but quadratic growth blows past it by a wide margin, which is the
    only thing this needs to catch.
    """
    def _scan_seconds(n: int) -> float:
        runtime = MemoryOS(str(tmp_path / f"scale_{n}.db"), projection_mode="shadow")
        for i in range(n):
            _observe(runtime, content=f"scaling probe {i}")
        started = time.perf_counter()
        DeepDoctor(runtime.events.conn).run(deep=True)
        elapsed = time.perf_counter() - started
        runtime.close()
        return elapsed

    small = _scan_seconds(250)
    large = _scan_seconds(1000)
    ratio = large / max(small, 1e-4)
    assert ratio < 8, (
        f"4x the data took {ratio:.1f}x the scan time; linear would be 4x and "
        f"quadratic 16x ({small:.3f}s -> {large:.3f}s)"
    )


def test_projection_lookup_matches_ids_exactly_not_as_substrings(os_):
    """The set-membership test is stricter than the LIKE it replaced.

    A substring match would pair an event with a memory that merely contains
    its id inside a longer one, and silently conclude a projection exists.
    """
    event = os_.observe(tenant_id="t1", actor="a", source="unit", content="parent")
    os_.remember(event=event, memory_type=MemoryType.EPISODIC, content="parent")
    worker_for(os_)  # creates projection_ledger

    # A ledger row for an id that is a strict prefix of a real, projected id.
    prefix = event.event_id[:-4]
    os_.memories.conn.execute(
        "INSERT INTO projection_ledger(projection_key, event_id, projection_type,"
        " projection_version, tenant_id, target_id, worker_id, created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (projection_key(prefix, MEMORY), prefix, MEMORY, 1, "t1", None, "w", time.time()),
    )
    os_.memories.conn.commit()

    codes = _codes(_scan(os_))
    assert "LEDGER_WITHOUT_PROJECTION" in codes, (
        "a ledger row whose event id merely prefixes a projected one has no "
        "projection of its own and must be reported"
    )
