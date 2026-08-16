"""A deletion that survives a rebuild.

Reproduced on 2026-08-15, forty lines, no world model and no benchmark:

    remember "Số điện thoại của Hoàng Yến là 0977123456."
    forget_scoped(...)          -> deleted=1, verified_clean=True
    cognitive_memories          -> 0                        gone
    replay + rebuild_version + drain
    cognitive_memories          -> 1                        back, verbatim

Nothing was broken by accident. `forget_derived` runs at derived level and says
so; the event log is append-only by design; the replay engine rebuilds
projections from that log. Each piece was correct alone. Together they meant a
deletion held until the first recovery operation, and `verified_clean=True` was
a true answer to the wrong question — it said the deleter did what it decided
to do, never that the value was gone for good.

Fixed on 2026-08-16 by adding a second append-only fact of equal standing:

    E100  PHONE(A) = 0977123456          the event
    E500  FORGET  target = E100          the tombstone

The log is still the log. It is no longer the *only* input to a rebuild.

Of the two candidate repairs recorded when this file was written as an xfail,
the first was taken — replay consults the tombstones. The second, refusing to
report success while the payload stands, would have made every derived-level
deletion report a failure, which is not what a derived-level deletion is.

The three levels stay three. `erase_history` is still the only one that
touches a payload, still needs a confirmation and a reason, and still breaks
the checksum on purpose. Nothing here made it easier to call by accident.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

SECRET = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "resurrect.db")
    yield memory_os
    memory_os.close()


def _holding(memory_os: MemoryOS) -> int:
    return memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]


def _store_and_forget(memory_os: MemoryOS) -> None:
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=SECRET, workspace_id="w1")
    memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                       content=SECRET, confidence=0.9)
    assert _holding(memory_os) == 1

    result = forget_scoped(memory_os, "Hãy quên số điện thoại của Hoàng Yến.",
                           actor="test")
    assert result.deleted_claims == 1
    assert _holding(memory_os) == 0, "xoá còn chưa chạy được thì test này vô nghĩa"


def test_the_report_says_which_of_the_three_erasures_it_ran(os_):
    """One word doing three jobs is how a caller comes to believe a `forget()`
    reached the event log.

    The report has to say the level, say that it is reversible, and say by
    what — a caller who reads `succeeded: true` and nothing else has been told
    something true and misleading at once.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    event = os_.observe(tenant_id="t1", actor="a", source="u", content=SECRET,
                        workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC, content=SECRET,
                 confidence=0.9)
    report = forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.",
                           actor="test").as_dict()

    assert report["erasure_level"] == "forget_derived"
    assert report["reversible"] is True
    assert report["reversible_via"]
    assert report["erases_event_payload"] is False
    assert report["succeeded"] is True


def test_the_event_log_keeps_the_payload_after_a_derived_delete(os_):
    """The design, not the defect.

    A derived-level deletion leaves the history able to explain what the
    system once believed. `erase_history` is the level that changes that, and
    it is a different call with a different audit.
    """
    _store_and_forget(os_)
    n = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert n == 1


def _rebuild_everything(os_) -> None:
    from bio_agent_os.cognitive.projection_control import drain
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    engine = ProjectionReplayEngine(os_.memories.conn)
    engine.replay(dry_run=False, tenant_id="t1")
    engine.rebuild_version("cognitive_memory", tenant_id="t1", dry_run=False)
    drain(os_, timeout_seconds=30, worker_id="resurrection-test")


def test_a_rebuild_does_not_bring_back_what_was_forgotten(os_):
    _store_and_forget(os_)
    _rebuild_everything(os_)
    assert _holding(os_) == 0, (
        "ký ức đã xoá quay lại sau replay — continuity engine đang hồi sinh "
        "thứ người dùng yêu cầu quên")


def test_it_is_the_tombstone_doing_the_work(os_):
    """The control. A test that passes for the wrong reason is worse than one
    that fails, and "nothing came back" has several innocent explanations —
    the rebuild found nothing to do, the worker never ran, the event was
    already projected.

    Lifting the tombstone must bring the memory back. If it does not, the
    rebuild was never capable of restoring it and the test above proves
    nothing about tombstones.
    """
    from bio_agent_os.cognitive.tombstones import lift

    _store_and_forget(os_)
    _rebuild_everything(os_)
    assert _holding(os_) == 0

    event_id = os_.memories.conn.execute(
        "SELECT event_id FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert lift(os_.memories.conn, event_id, actor="test",
                reason="chứng minh chính bia mộ đang chặn") == 1

    _rebuild_everything(os_)
    assert _holding(os_) == 1, (
        "gỡ bia mộ mà ký ức không quay lại — nghĩa là rebuild vốn không dựng "
        "lại được, và test ở trên không chứng minh gì về bia mộ")


def test_a_tombstone_is_auditable(os_):
    from bio_agent_os.cognitive.tombstones import tombstones

    _store_and_forget(os_)
    rows = tombstones(os_.memories.conn)
    assert rows, "xoá xong mà không có bia mộ nào"
    (row,) = rows
    assert row["scope"] == "derived"
    assert row["actor"], "bia mộ không ghi ai đặt"
    assert row["reason"]
    assert row["at"]
    assert row["lifted"] is False


def test_the_tombstone_names_who_asked_not_the_function_that_ran(os_):
    """Caught by printing the number, not by reading the code.

    The lifetime run placed 68 tombstones and every one of them was signed
    "forget" — `forget_scoped` was calling `forget_derived` without passing
    the actor it had been given. A table that records who deleted something
    and always records the same word answers nothing.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.tombstones import tombstones

    event = os_.observe(tenant_id="t1", actor="a", source="u", content=SECRET,
                        workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC, content=SECRET,
                 confidence=0.9)
    forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.",
                  actor="tuananh")

    (row,) = tombstones(os_.memories.conn)
    assert row["actor"] == "tuananh", row["actor"]


def test_the_scoped_report_says_it_will_survive_a_rebuild(os_):
    """`deleted_claims` says the serving layer is clean now. Only this says it
    stays clean. Until 16/08 the report carried the first and not the second,
    and the two were not the same fact."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    event = os_.observe(tenant_id="t1", actor="a", source="u", content=SECRET,
                        workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC, content=SECRET,
                 confidence=0.9)
    report = forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.",
                           actor="tuananh").as_dict()

    assert report["deleted_claims"] == 1
    assert report["tombstoned"] >= 1
    assert report["survives_rebuild"] is True


def test_lifting_a_tombstone_is_a_write_not_a_delete(os_):
    """"Was this ever deleted?" has to stay answerable."""
    from bio_agent_os.cognitive.tombstones import lift, tombstones

    _store_and_forget(os_)
    event_id = os_.memories.conn.execute(
        "SELECT event_id FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    lift(os_.memories.conn, event_id, actor="tuananh", reason="xoá nhầm")

    (row,) = tombstones(os_.memories.conn)
    assert row["lifted"] is True
    assert row["lifted_by"] == "tuananh"
    assert row["lifted_reason"] == "xoá nhầm"
    assert row["lifted_at"]


def test_a_tombstone_needs_a_name_on_it():
    """Every other write that removes something refuses without an actor."""
    import sqlite3

    from bio_agent_os.cognitive.tombstones import lift, place

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        place(conn, event_ids=["e1"], scope="derived", actor="")
    place(conn, event_ids=["e1"], scope="derived", actor="someone")
    with pytest.raises(ValueError):
        lift(conn, "e1", actor="someone", reason="")
    conn.close()


def test_a_deletion_after_the_job_was_queued_still_holds(os_):
    """The second of the two checks, and the window it covers.

    The replay engine declines to enqueue tombstoned events. That leaves the
    gap between an enqueue and the worker draining it — which is precisely the
    interval a deletion arrives in. Here the job is queued first and the
    deletion lands second.
    """
    from bio_agent_os.cognitive.projection_control import drain
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    event = os_.observe(tenant_id="t1", actor="a", source="u", content=SECRET,
                        workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC, content=SECRET,
                 confidence=0.9)

    engine = ProjectionReplayEngine(os_.memories.conn)
    queued = engine.rebuild_version("cognitive_memory", tenant_id="t1",
                                    dry_run=False)
    assert queued.enqueued >= 1, "không xếp được job nào — test này chưa đo gì"

    from bio_agent_os.cognitive.forget_scope import forget_scoped
    forget_scoped(os_, "Hãy quên số điện thoại của Hoàng Yến.", actor="test")

    drain(os_, timeout_seconds=30, worker_id="late-delete-test")
    assert _holding(os_) == 0, (
        "job đã nằm sẵn trong hàng đợi dựng lại ký ức sau khi lệnh xoá tới")


def test_a_replay_says_how_many_it_declined_to_restore(os_):
    """A recovery that quietly restores nothing looks exactly like one that
    failed. The operator running it needs to be told which."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    _store_and_forget(os_)
    report = ProjectionReplayEngine(os_.memories.conn).rebuild_version(
        "cognitive_memory", tenant_id="t1", dry_run=True)
    assert report.skipped_tombstoned >= 1
    assert "tombstoned" in report.render()


def test_a_dry_run_makes_the_same_decision_as_the_real_one(os_):
    """A preview that disagrees with the act is worse than no preview."""
    from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine

    _store_and_forget(os_)
    engine = ProjectionReplayEngine(os_.memories.conn)
    preview = engine.replay(dry_run=True, tenant_id="t1")
    applied = engine.replay(dry_run=False, tenant_id="t1")
    assert preview.skipped_tombstoned == applied.skipped_tombstoned
    assert applied.enqueued == 0


def test_erase_history_is_still_a_separate_operation(os_):
    """The two must not merge.

    A derived deletion leaves the payload; `erase_history` removes it, needs a
    confirmation, a reason and an actor, and breaks the event checksum on
    purpose so the redaction cannot be silent. Tombstones changed none of that.
    """
    from bio_agent_os.cognitive.forgetting import erase_history

    _store_and_forget(os_)
    conn = os_.memories.conn
    before = conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert before == 1, "tiền đề sai: payload đã không còn trước khi xoá cứng"

    # Refuses loudly rather than returning an empty report: this sits one
    # keyword away from the reversible version.
    with pytest.raises(PermissionError):
        erase_history(os_, needle=NEEDLE, reason="test", actor="tuananh")

    erase_history(os_, needle=NEEDLE, reason="yêu cầu của chủ thể",
                  actor="tuananh", confirm=True)
    after = conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE payload_json LIKE ?",
        (f"%{NEEDLE}%",)).fetchone()[0]
    assert after == 0, "xoá cứng đã xác nhận mà payload vẫn còn"


def test_a_store_that_never_deleted_anything_still_projects(os_):
    """The tombstone table may not exist yet. Absence of deletions is not an
    error, and failing closed here would stop every projection in a fresh
    store."""
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Trần Hà sống ở Hà Nội.", workspace_id="w1")
    os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                 content="Trần Hà sống ở Hà Nội.", confidence=0.9)
    _rebuild_everything(os_)
    n = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        ("%Hà Nội%",)).fetchone()[0]
    assert n >= 1
