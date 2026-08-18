"""Projection crash and recovery matrix — F01..F22.

Every case kills at a *named* point rather than after a sleep, so a passing
test says where the process was, not merely that it died somewhere. The cases
listed in section 7 of the plan run in real subprocesses under `spawn`; the
rest are pure transaction-boundary tests where an in-process fault point is
exactly as precise.

All process-level cases use a real SQLite file. `:memory:` would not survive
the process it was created in, which is the whole thing being tested.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive import fault_points as fault
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.fault_points import ProjectionFaultPoint as FP
from bio_agent_os.cognitive.models import EventRecord
from bio_agent_os.cognitive.outbox import JobStatus, ProjectionJob
from bio_agent_os.cognitive.projection_registry import DependencyState, ProjectionType
from bio_agent_os.cognitive.reconciliation_worker import (
    ReconciliationWorker,
    build_default_builders,
    worker_for,
)

sys.path.insert(0, str(Path(__file__).parent))
import fault_children
from lease_time import past_expiry  # noqa: E402

MEMORY = ProjectionType.COGNITIVE_MEMORY.value
CONTEXT = ProjectionType.CONTEXT_BLOCK.value
CHECKPOINT = ProjectionType.CHECKPOINT_REFERENCE.value

CTX = mp.get_context("spawn")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_hook_leaks():
    """A hook left armed would silently crash later tests."""
    fault.clear()
    yield
    fault.clear()


@pytest.fixture()
def db(tmp_path) -> str:
    return str(tmp_path / "fault.db")


def _append(os_: MemoryOS, event_id="evt-1", tenant="t1", content="remember me",
            types=(MEMORY,)) -> EventRecord:
    return os_.events.append(
        EventRecord(tenant_id=tenant, actor="t", source="fault",
                    payload={"content": content}, event_id=event_id),
        projection_types=types,
    )


def _arm_in_process(point: FP, action="raise"):
    def hook(fired: str) -> None:
        if fired == point.value:
            raise RuntimeError(f"injected fault at {fired}")

    fault.install(hook)


class State:
    """Everything the invariants are checked against, read from a fresh
    connection so nothing in a cache can flatter the result."""

    def __init__(self, db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        self.conn = conn

    def _count(self, table: str, where: str = "", params=()) -> int:
        try:
            sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
            return int(self.conn.execute(sql, params).fetchone()[0])
        except sqlite3.OperationalError:
            return 0

    @property
    def events(self) -> int:
        return self._count("cognitive_events")

    @property
    def jobs(self) -> int:
        return self._count("projection_outbox")

    @property
    def projections(self) -> int:
        return self._count("cognitive_memories")

    @property
    def ledger(self) -> int:
        return self._count("projection_ledger")

    def job(self, event_id: str, ptype: str = MEMORY):
        return self.conn.execute(
            "SELECT * FROM projection_outbox WHERE event_id=? AND projection_type=?",
            (event_id, ptype),
        ).fetchone()

    def integrity(self) -> str:
        return self.conn.execute("PRAGMA integrity_check").fetchone()[0]

    def close(self) -> None:
        self.conn.close()


def _reopened(db_path: str) -> State:
    """Durability check: close everything, open the file again."""
    return State(db_path)


def _run_child(target, *args, timeout=60, **kwargs):
    p = CTX.Process(target=target, args=args, kwargs=kwargs)
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join(5)
        pytest.fail("child process did not finish")
    return p


def _crash_child_at(db_path: str, point: FP, target=None, **kwargs):
    """Start a child, wait for it to signal the fault point, confirm it died."""
    reached = CTX.Event()
    target = target or fault_children.crash_worker_at
    p = CTX.Process(target=target, args=(db_path, point.value, reached), kwargs=kwargs)
    p.start()
    assert reached.wait(60), f"child never reached {point.value}"
    p.join(30)
    assert not p.is_alive()
    return p


# ==========================================================================
# A. Event and outbox — one transaction
# ==========================================================================


def test_F01_crash_before_the_event_transaction(db):
    """Nothing was attempted, so nothing exists."""
    os_ = MemoryOS(db)
    _arm_in_process(FP.BEFORE_EVENT_TRANSACTION)
    with pytest.raises(RuntimeError):
        _append(os_)

    s = _reopened(db)
    assert (s.events, s.jobs, s.projections) == (0, 0, 0)
    assert s.integrity() == "ok"
    s.close()


def test_F02_failure_after_event_insert_rolls_the_event_back(db):
    """The event is inserted but the outbox row is not. Both must vanish."""
    os_ = MemoryOS(db)
    _arm_in_process(FP.AFTER_EVENT_INSERT)
    with pytest.raises(RuntimeError):
        _append(os_)
    os_.events.conn.rollback()

    s = _reopened(db)
    assert s.events == 0, "an event without its debt must not survive"
    assert s.jobs == 0
    s.close()


def test_F03_failure_after_outbox_insert_before_commit_rolls_both_back(db):
    os_ = MemoryOS(db)
    _arm_in_process(FP.AFTER_OUTBOX_INSERT)
    with pytest.raises(RuntimeError):
        _append(os_)
    os_.events.conn.rollback()

    s = _reopened(db)
    assert (s.events, s.jobs) == (0, 0)
    s.close()


def test_F04_crash_after_the_shared_commit_leaves_recoverable_debt(db):
    """PROCESS-LEVEL. The event and its debt are durable; nothing else is."""
    _crash_child_at(db, FP.AFTER_EVENT_COMMIT, target=fault_children.crash_append_at,
                    event_id="evt-1", tenant="t1")

    s = _reopened(db)
    assert s.events == 1
    assert s.jobs == 1
    assert s.projections == 0
    assert s.job("evt-1")["status"] == JobStatus.PENDING.value
    s.close()

    # A fresh worker must be able to finish the job.
    _run_child(fault_children.run_worker, db)

    after = _reopened(db)
    assert after.projections == 1
    assert after.job("evt-1")["status"] == JobStatus.COMPLETED.value
    assert after.integrity() == "ok"
    after.close()


# ==========================================================================
# B. Claim and lease
# ==========================================================================


def test_F05_crash_right_after_claim_holds_then_releases_the_lease(db):
    """PROCESS-LEVEL."""
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    _crash_child_at(db, FP.AFTER_CLAIM, lease_seconds=300)

    s = _reopened(db)
    job = s.job("evt-1")
    assert job["status"] == JobStatus.IN_PROGRESS.value
    assert job["locked_by"] is not None
    assert s.projections == 0
    s.close()

    # While the lease is valid nobody else may take it.
    live = MemoryOS(db)
    assert live.events.outbox.claim("other", lease_seconds=300) == []

    # Once it expires, exactly one worker recovers it. Hết hạn là chuyện của
    # ĐỒNG HỒ, không phải của người đọc: đẩy `now` qua hạn thay vì đọc bằng
    # lease 0 (nay bị từ chối).
    recovered = live.events.outbox.claim(
        "other", lease_seconds=300, now=past_expiry(live.events, 300))
    assert len(recovered) == 1
    assert recovered[0].attempts == 2


def test_F06_two_processes_claiming_the_same_job(db):
    """PROCESS-LEVEL. Exactly one winner, exactly one projection."""
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    gate = CTX.Event()
    results = CTX.Queue()
    procs = [
        CTX.Process(target=fault_children.run_worker, args=(db, results),
                    kwargs={"worker_id": f"race-{i}", "start_gate": gate})
        for i in range(2)
    ]
    for p in procs:
        p.start()
    gate.set()
    for p in procs:
        p.join(60)
        assert not p.is_alive()

    metrics = [results.get(timeout=5) for _ in range(2)]
    assert sum(m["completed"] for m in metrics) == 1, "only one worker may complete it"
    assert sum(m["claimed"] for m in metrics) == 1

    s = _reopened(db)
    assert s.projections == 1
    assert s.ledger == 1
    assert s.job("evt-1")["status"] == JobStatus.COMPLETED.value
    s.close()


def test_F07_restart_with_a_stale_lease_recovers_exactly_once(db):
    """PROCESS-LEVEL."""
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    _crash_child_at(db, FP.AFTER_CLAIM, lease_seconds=300)

    live = MemoryOS(db)
    first = worker_for(live, worker_id="r1", lease_seconds=300)
    first.run_once(claim_now=past_expiry(live, 300))
    second = worker_for(live, worker_id="r2", lease_seconds=300)
    # r2 cũng nhìn từ SAU hạn: điều ca này khẳng định là một job đã completed
    # không thể thu hồi lại kể cả khi thời gian trôi qua hạn.
    second.run_once(claim_now=past_expiry(live, 300))

    assert first.metrics.completed == 1
    assert second.metrics.claimed == 0, "a completed job is not reclaimable"

    s = _reopened(db)
    assert s.projections == 1
    s.close()


def test_F08_lease_expiry_at_the_exact_boundary(db):
    """Regression guard: the Windows clock ticks at ~15 ms, so a claim and a
    check inside one tick give an elapsed of exactly 0.0. With `>` a
    zero-length lease never expired."""
    os_ = MemoryOS(db)
    _append(os_)
    held = os_.events.outbox.claim("holder", lease_seconds=300)
    locked_at = held[0].locked_at
    # ĐÚNG biên: elapsed == lease. Trước đây ca này ép biên bằng lease 0 — một
    # cấu hình nay bị từ chối. Cùng một câu hỏi, hỏi bằng đồng hồ: tại
    # `now = locked_at + lease`, vị từ `locked_at <= now - lease` phải ĐÚNG.
    reclaimed = os_.events.outbox.claim(
        "taker", lease_seconds=300, now=locked_at + 300)
    assert len(reclaimed) == 1, "elapsed >= lease must expire, not elapsed > lease"


# ==========================================================================
# C. Projection transaction
# ==========================================================================


def test_F09_crash_before_the_projection_write_leaves_nothing_behind(db):
    os_ = MemoryOS(db)
    _append(os_)
    worker = worker_for(os_, worker_id="w")
    _arm_in_process(FP.BEFORE_PROJECTION_WRITE)
    worker.run_once()

    s = _reopened(db)
    assert s.projections == 0
    assert s.ledger == 0, "the ledger insert must roll back with the build"
    assert s.job("evt-1")["status"] == JobStatus.PENDING.value
    s.close()

    # Clear the fault and let the backoff window elapse, then retry.
    fault.clear()
    os_.events.conn.execute(
        "UPDATE projection_outbox SET available_at=? WHERE event_id=?",
        (time.time() - 1, "evt-1"),
    )
    os_.events.conn.commit()
    worker2 = worker_for(os_, worker_id="w2", lease_seconds=300)
    worker2.run_once()
    after = _reopened(db)
    assert after.projections == 1
    after.close()


def test_F10_crash_between_projection_insert_and_commit(db):
    """PROCESS-LEVEL. Nothing durable, and a retry works normally.

    The production builder calls `put()`, which inserts and commits in one
    call, so there is no instruction between them for a fault point to sit on.
    The child uses a builder that performs the insert on the same connection
    and stops before committing — the identical intermediate state, reached
    honestly rather than asserted about.
    """
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    reached = CTX.Event()
    p = CTX.Process(target=fault_children.crash_uncommitted_projection, args=(db, reached))
    p.start()
    assert reached.wait(60), "child never reached the uncommitted-projection point"
    p.join(30)
    assert not p.is_alive()

    s = _reopened(db)
    assert s.projections == 0, "an uncommitted projection must not survive"
    assert s.ledger == 0
    s.close()

    live = MemoryOS(db)
    worker_for(live, worker_id="retry", lease_seconds=300).run_once(
        claim_now=past_expiry(live, 300))

    after = _reopened(db)
    assert after.projections == 1
    assert after.ledger == 1
    assert after.job("evt-1")["status"] == JobStatus.COMPLETED.value
    after.close()


def test_F11_ledger_and_projection_roll_back_together(db):
    """The ledger must never outlive the projection it vouches for."""
    os_ = MemoryOS(db)
    _append(os_)
    worker = worker_for(os_, worker_id="w")
    _arm_in_process(FP.AFTER_LEDGER_INSERT)
    worker.run_once()

    s = _reopened(db)
    assert s.ledger == 0
    assert s.projections == 0
    s.close()


def test_F12_crash_after_projection_commit_before_outbox_complete(db):
    """PROCESS-LEVEL. The most important case in the matrix.

    The projection is durable but the job is not marked done. A retry must
    recognise the completed work rather than repeat it.
    """
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    _crash_child_at(db, FP.AFTER_PROJECTION_COMMIT)

    mid = _reopened(db)
    assert mid.projections == 1, "the projection committed before the crash"
    assert mid.ledger == 1
    assert mid.job("evt-1")["status"] == JobStatus.IN_PROGRESS.value
    mid.close()

    live = MemoryOS(db)
    survivor = worker_for(live, worker_id="survivor", lease_seconds=300)
    survivor.run_once(claim_now=past_expiry(live, 300))

    assert survivor.metrics.already_built == 1, "the retry must recognise prior work"

    after = _reopened(db)
    assert after.projections == 1, "no duplicate projection"
    assert after.ledger == 1
    assert after.job("evt-1")["status"] == JobStatus.COMPLETED.value
    assert after.integrity() == "ok"
    after.close()


def test_F13_the_same_job_processed_ten_times(db):
    os_ = MemoryOS(db)
    _append(os_)
    for i in range(10):
        os_.events.conn.execute(
            "UPDATE projection_outbox SET status=?, locked_at=NULL, available_at=? "
            "WHERE event_id=?",
            (JobStatus.PENDING.value, time.time() - 1, "evt-1"),
        )
        os_.events.conn.commit()
        worker_for(os_, worker_id=f"w{i}", lease_seconds=300).run_once()

    s = _reopened(db)
    assert s.projections == 1
    assert s.ledger == 1
    assert s.job("evt-1")["status"] == JobStatus.COMPLETED.value
    s.close()


# ==========================================================================
# D. Target-local ledger across the six stores
# ==========================================================================


def test_F14_ledger_and_cognitive_memory_share_one_transaction(db):
    os_ = MemoryOS(db)
    _append(os_)
    worker = worker_for(os_, worker_id="w")
    assert worker.projection_conn is os_.memories.conn, (
        "the ledger must live on the connection that writes the projection"
    )
    worker.run_once()
    s = _reopened(db)
    assert s.ledger == 1 and s.projections == 1
    s.close()


@pytest.mark.parametrize(
    "ptype",
    [
        ProjectionType.SELF_MODEL_UPDATE.value,
        ProjectionType.PROSPECTIVE_MEMORY.value,
    ],
)
def test_F15_F16_stores_without_a_builder_dead_letter_rather_than_pretend(db, ptype):
    """Exposes the gap instead of mocking past it.

    self_model and prospective have no builder yet, so their ledger cannot be
    target-local — there is no target transaction. A job for one of them must
    dead-letter with that stated, not silently complete.
    """
    os_ = MemoryOS(db)
    _append(os_, types=(ptype,))
    worker_for(os_, worker_id="w").run_once()

    s = _reopened(db)
    job = s.job("evt-1", ptype)
    assert job["status"] == JobStatus.DEAD_LETTER.value
    assert "no builder registered" in job["last_error"]
    assert s.ledger == 0, "no ledger row without a real projection"
    s.close()


def test_F17_context_block_waits_for_its_parent_memory(db):
    os_ = MemoryOS(db)
    _append(os_, types=(MEMORY, CONTEXT))
    worker = worker_for(os_, worker_id="w")
    ctx_job = [j for j in os_.events.outbox.by_event("evt-1")
               if j.projection_type == CONTEXT][0]

    assert worker.process(ctx_job) == DependencyState.PENDING_DEPENDENCY.value
    s = _reopened(db)
    assert s.job("evt-1", CONTEXT)["status"] == JobStatus.PENDING.value
    s.close()


def test_F18_checkpoint_reference_is_never_silently_rebuilt(db):
    os_ = MemoryOS(db)
    _append(os_, types=(CHECKPOINT,))
    os_.events.outbox.enqueue(
        ProjectionJob(event_id="evt-1", projection_type=MEMORY, tenant_id="t1"), commit=True
    )
    parent = [j for j in os_.events.outbox.by_event("evt-1")
              if j.projection_type == MEMORY][0]
    os_.events.outbox.complete(parent.job_id)

    worker = worker_for(os_, worker_id="w")
    job = [j for j in os_.events.outbox.by_event("evt-1")
           if j.projection_type == CHECKPOINT][0]
    job.attempts = 2
    assert worker.process(job) == JobStatus.DEAD_LETTER.value

    s = _reopened(db)
    assert s.job("evt-1", CHECKPOINT)["status"] == JobStatus.DEAD_LETTER.value
    assert s.projections == 0, "no substitute projection may be invented"
    s.close()


# ==========================================================================
# E. Locks, failure and determinism
# ==========================================================================


def test_F19_a_temporary_write_lock_is_retried_not_dead_lettered(db):
    """PROCESS-LEVEL. Another process holds a real write lock."""
    os_ = MemoryOS(db)
    _append(os_)
    os_.events.conn.close()

    holding, release = CTX.Event(), CTX.Event()
    locker = CTX.Process(target=fault_children.hold_write_lock, args=(db, holding, release))
    locker.start()
    assert holding.wait(30), "lock holder never acquired"

    live = sqlite3.connect(db, timeout=0.2)
    live.execute("PRAGMA busy_timeout=200")
    with pytest.raises(sqlite3.OperationalError):
        live.execute("BEGIN IMMEDIATE")
    live.close()

    release.set()
    locker.join(30)
    assert not locker.is_alive()

    after = MemoryOS(db)
    worker_for(after, worker_id="post-lock").run_once()
    s = _reopened(db)
    assert s.projections == 1
    assert s.job("evt-1")["status"] == JobStatus.COMPLETED.value
    assert s.integrity() == "ok"
    s.close()


class _AlwaysFails:
    def build(self, event, job, conn):
        raise RuntimeError("permanent builder failure")


def test_F20_repeated_failure_reaches_dead_letter_with_its_history(db):
    os_ = MemoryOS(db)
    _append(os_)
    w = ReconciliationWorker(
        os_.events.conn, projection_conn=os_.memories.conn, outbox=os_.events.outbox,
        builders={MEMORY: _AlwaysFails()}, worker_id="w", max_attempts=2, lease_seconds=300,
    )
    for _ in range(4):
        os_.events.conn.execute(
            "UPDATE projection_outbox SET available_at=? WHERE event_id=?",
            (time.time() - 1, "evt-1"),
        )
        os_.events.conn.commit()
        w.run_once()

    s = _reopened(db)
    job = s.job("evt-1")
    assert job["status"] == JobStatus.DEAD_LETTER.value
    assert job["attempts"] >= 2
    assert "permanent builder failure" in job["last_error"]
    assert s.ledger == 0
    s.close()


def test_F21_a_dead_parent_blocks_the_child_instead_of_retrying_forever(db):
    os_ = MemoryOS(db)
    _append(os_, types=(MEMORY, CONTEXT))
    jobs = {j.projection_type: j for j in os_.events.outbox.by_event("evt-1")}
    os_.events.outbox.fail(jobs[MEMORY].job_id, "fatal", max_attempts=0)

    worker = worker_for(os_, worker_id="w")
    assert worker.process(jobs[CONTEXT]) == JobStatus.DEAD_LETTER.value
    assert worker.metrics.blocked == 1

    s = _reopened(db)
    assert s.job("evt-1", CONTEXT)["status"] == JobStatus.DEAD_LETTER.value
    s.close()


class _NonDeterministic:
    """Returns different content for the same event each time."""

    def __init__(self, memory_store):
        self.memories = memory_store
        self.calls = 0

    def build(self, event, job, conn):
        from bio_agent_os.cognitive.models import BeliefState, CognitiveMemory, MemoryType
        from bio_agent_os.cognitive.reconciliation_worker import BuildOutcome, BuildResult

        self.calls += 1
        memory = CognitiveMemory(
            tenant_id=event.tenant_id, memory_type=MemoryType.EPISODIC,
            content=f"nondeterministic-{self.calls}",
            source_event_ids=[event.event_id],
            lifecycle_state=BeliefState.PROPOSED,
            metadata={"projection_key": job.key},
        )
        stored = self.memories.put(memory)
        return BuildResult(BuildOutcome.BUILT.value, target_id=stored.memory_id)


def test_F22_a_second_build_of_the_same_key_is_refused(db):
    """The ledger is what stops non-determinism becoming silent divergence."""
    os_ = MemoryOS(db)
    _append(os_)
    builder = _NonDeterministic(os_.memories)
    w = ReconciliationWorker(
        os_.events.conn, projection_conn=os_.memories.conn, outbox=os_.events.outbox,
        builders={MEMORY: builder}, worker_id="w", lease_seconds=300,
    )
    w.run_once()
    assert builder.calls == 1

    os_.events.conn.execute(
        "UPDATE projection_outbox SET status=?, locked_at=NULL, available_at=? WHERE event_id=?",
        (JobStatus.PENDING.value, time.time() - 1, "evt-1"),
    )
    os_.events.conn.commit()
    w.run_once()

    assert builder.calls == 1, "the ledger must prevent a second, different build"
    s = _reopened(db)
    assert s.projections == 1
    content = s.conn.execute("SELECT content FROM cognitive_memories").fetchone()[0]
    assert content == "nondeterministic-1", "the first result must not be overwritten"
    s.close()


# ==========================================================================
# tenant isolation under crash
# ==========================================================================


def test_tenant_isolation_survives_a_crash(db):
    """PROCESS-LEVEL. Killing a worker mid-tenant must not touch the other."""
    os_ = MemoryOS(db)
    _append(os_, "evt-a", tenant="tenant-a")
    _append(os_, "evt-b", tenant="tenant-b")
    os_.events.conn.close()

    _crash_child_at(db, FP.AFTER_PROJECTION_COMMIT)

    live = MemoryOS(db)
    worker_for(live, worker_id="recover", lease_seconds=300).run_once(
        batch_size=10, claim_now=past_expiry(live, 300))

    s = _reopened(db)
    rows = s.conn.execute(
        "SELECT tenant_id, source_event_ids_json FROM cognitive_memories"
    ).fetchall()
    for row in rows:
        expected = "evt-a" if row["tenant_id"] == "tenant-a" else "evt-b"
        assert expected in row["source_event_ids_json"], "cross-tenant projection"

    ledger = s.conn.execute(
        "SELECT l.tenant_id, e.tenant_id AS event_tenant FROM projection_ledger l "
        "JOIN cognitive_events e ON e.event_id = l.event_id"
    ).fetchall()
    for row in ledger:
        assert row["tenant_id"] == row["event_tenant"], "ledger references another tenant"

    assert s.integrity() == "ok"
    s.close()


# ==========================================================================
# the safety of the mechanism itself
# ==========================================================================


def test_fault_points_are_inert_unless_a_test_arms_them(db):
    assert not fault.armed()
    os_ = MemoryOS(db)
    _append(os_)
    worker_for(os_, worker_id="w").run_once()
    s = _reopened(db)
    assert s.projections == 1
    s.close()


def test_arming_and_clearing_is_symmetric():
    assert not fault.armed()
    fault.install(lambda point: None)
    assert fault.armed()
    fault.clear()
    assert not fault.armed()
