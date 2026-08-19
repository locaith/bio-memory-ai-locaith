"""RC-0 — REPLAY INTENT CLOSURE. Cái khoá cuối trước chữ CLOSED.

    WRITE-TIME DECISION
            ↓
    must become durable evidence
            ↓
    REPLAY MAY RECOVER IT
            ↓
    REPLAY MAY NOT REINTERPRET IT

Hai luật:

    ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED
    INGEST-TIME NO-PROJECTION DECISION MUST SURVIVE REPLAY AND VERSION REBUILD

Nếu ingest đã quyết "đây không phải memory", replay không được vài tuần sau
nhìn một hàng vắng rồi tự phán "chắc ai quên build". Đấy là khác biệt giữa
recovery và viết lại lịch sử.

Năm dao (R1–R5) + atomicity ở cấp process.
"""
from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive import projection_engine as PE
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.projection_engine import (ProjectionReplayEngine,
                                                      ReplayReason)
from bio_agent_os.cognitive.projection_intent import (NO_PROJECTION,
                                                      MemoryProjectionIntent,
                                                      recorded_decision)
from bio_agent_os.cognitive.reconciliation_worker import worker_for

MARKER = {"hook_event_name": "SessionStart", "session_id": "rc0"}
REAL_PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "rc0",
               "prompt": "khách An Phú chốt hợp đồng RC0-SUBSTANTIVE"}


def _os(tmp_path, name="rc0.db"):
    return MemoryOS(tmp_path / name, projection_mode="outbox")


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _memories(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]


# ---------------------------------------------------------------------------
# nền: quyết định lúc ghi phải trở thành bằng chứng bền
# ---------------------------------------------------------------------------

def test_live_non_substantive_event_gets_an_explicit_durable_skip(tmp_path):
    """Event + QUYẾT ĐỊNH, không phải event + sự vắng mặt."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.events.conn
        ev = _rows(conn, "SELECT event_id, payload_json FROM cognitive_events")
        assert len(ev) == 1
        import json
        assert recorded_decision(json.loads(ev[0]["payload_json"])) == NO_PROJECTION

        jobs = _rows(conn, "SELECT status, last_error FROM projection_outbox")
        assert len(jobs) == 1 and jobs[0]["status"] == "skipped"
        assert jobs[0]["last_error"] == "non_substantive"
        assert _memories(mos.memories.conn) == 0
    finally:
        mos.close()


def test_replay_repair_cannot_materialize_a_non_substantive_event(tmp_path):
    """`replay --repair` chạy hết cỡ vẫn không dựng nổi một marker."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.enqueued == 0
        assert _memories(conn) == 0, "marker đã bị đường phục hồi dựng thành ký ức"
    finally:
        mos.close()


def test_lost_skip_row_is_reconstructed_as_skip_not_as_memory(tmp_path):
    """Mất hàng terminal → khôi phục QUYẾT ĐỊNH, không khôi phục thành ký ức."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        conn.execute("DELETE FROM projection_outbox")
        conn.commit()

        scan = ProjectionReplayEngine(conn).scan()
        assert [c.reason for c in scan.candidates] == \
            [ReplayReason.SKIP_ROW_LOST.value]
        assert not scan.candidates[0].actionable

        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.skips_reconstructed == 1 and report.enqueued == 0
        jobs = _rows(conn, "SELECT status, last_error FROM projection_outbox")
        assert len(jobs) == 1 and jobs[0]["status"] == "skipped"
        assert jobs[0]["last_error"] == "reconstructed_from_event_decision"
        assert _memories(conn) == 0
    finally:
        mos.close()


def test_version_rebuild_cannot_materialize_a_skipped_event(tmp_path):
    """Bump phiên bản KHÔNG làm quyết định "không chiếu" hết hạn."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        engine = ProjectionReplayEngine(conn)
        report = engine.rebuild_version("cognitive_memory", dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.skipped_no_projection == 1
        assert report.enqueued == 0 and _memories(conn) == 0
    finally:
        mos.close()


def test_unknown_intent_is_reported_but_never_actionable(tmp_path):
    """Không đoán. Event tiền-contract không ghi quyết định nào thì nó là
    UNKNOWN — hiển thị cho người vận hành, không bao giờ tự dựng."""
    mos = _os(tmp_path)
    try:
        mos.observe(tenant_id="t1", actor="a", source="legacy-import",
                    content="một dòng lịch sử không ai ghi lại ý định",
                    workspace_id="w1", enqueue_projection=False)
        conn = mos.memories.conn
        scan = ProjectionReplayEngine(conn).scan()
        assert [c.reason for c in scan.candidates] == \
            [ReplayReason.UNKNOWN_INTENT.value]
        assert not scan.candidates[0].actionable
        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.unknown_intent == 1 and report.enqueued == 0
        assert _memories(conn) == 0
    finally:
        mos.close()


def test_substantive_event_still_owes_and_still_gets_built(tmp_path):
    """Chứng đối: cổng chặt hơn KHÔNG được làm chết đường ghi thật."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("UserPromptSubmit",
                                                      REAL_PROMPT)
        conn = mos.memories.conn
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert _memories(conn) == 1
        jobs = _rows(conn, "SELECT status FROM projection_outbox")
        assert [j["status"] for j in jobs] == ["completed"]
        # và nếu hàng nợ biến mất trong khi intent VẪN được ghi → còn nợ thật
        conn.execute("DELETE FROM projection_outbox")
        conn.execute("DELETE FROM projection_ledger")
        conn.execute("DELETE FROM cognitive_memories")
        conn.execute("DELETE FROM memory_source_events")
        conn.commit()
        scan = ProjectionReplayEngine(conn).scan()
        assert [c.reason for c in scan.candidates] == \
            [ReplayReason.MISSING_OUTBOX.value]
        assert scan.candidates[0].actionable
    finally:
        mos.close()


# ---------------------------------------------------------------------------
# atomicity ở cấp PROCESS — event và quyết định cùng bền, hoặc không cái nào
# ---------------------------------------------------------------------------

def _child_crash_between(db_path: str, point: str) -> None:
    from bio_agent_os.cognitive import fault_points as fp
    from bio_agent_os.cognitive.facade import MemoryOS as M
    from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter as A

    def hook(name: str) -> None:
        if name == point:
            import os
            os._exit(9)                 # chết thật, không unwind, không commit

    fp.install(hook)
    m = M(db_path, projection_mode="outbox")
    A(m, "t1", "w1").ingest("SessionStart",
                            {"hook_event_name": "SessionStart",
                             "session_id": "crash"})
    m.close()


@pytest.mark.parametrize("point", ["after_event_insert", "after_outbox_insert"])
def test_event_and_skip_are_atomic_at_process_level(tmp_path, point):
    """Giết process giữa hai lần ghi: KHÔNG được để lại event không kèm quyết
    định. Chạy bằng process thật vì đó là hình dạng crash thật — một
    `raise` trong cùng process vẫn cho Python cơ hội dọn dẹp."""
    db = tmp_path / f"atomic_{point}.db"
    MemoryOS(db, projection_mode="outbox").close()          # tạo schema trước

    proc = mp.get_context("spawn").Process(
        target=_child_crash_between, args=(str(db), point))
    proc.start()
    proc.join(120)
    assert proc.exitcode == 9, f"child không chết đúng chỗ: {proc.exitcode}"

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        events = conn.execute(
            "SELECT COUNT(*) FROM cognitive_events").fetchone()[0]
        jobs = conn.execute(
            "SELECT COUNT(*) FROM projection_outbox").fetchone()[0]
    finally:
        conn.close()
    assert (events, jobs) in {(0, 0), (1, 1)}, (
        f"crash tại {point} để lại trạng thái lai: {events} event / {jobs} "
        f"hàng quyết định — event mà không kèm quyết định là đúng cái khe "
        f"replay sẽ diễn giải sai")


# ---------------------------------------------------------------------------
# R1–R5 — năm đột biến, mỗi cái phải chết
# ---------------------------------------------------------------------------

def test_r1_dropping_the_durable_skip_row_must_die(tmp_path, monkeypatch):
    """R1: bỏ hàng skip bền (quay về hành vi trước RC-0) — nhưng quyết định
    vẫn nằm trong payload, nên replay phải KHÔI PHỤC nó chứ không dựng ký ức.

    Đây là mutant chứng minh hai lớp bằng chứng độc lập: mất hàng terminal
    KHÔNG đủ để marker sống lại."""
    mos = _os(tmp_path)
    try:
        from bio_agent_os import cognitive
        original = cognitive.event_store.SQLiteEventStore.append

        def no_skip(self, record, *, projection_types=None, skip_types=()):
            return original(self, record, projection_types=projection_types,
                            skip_types=())          # MUTANT: nuốt quyết định

        monkeypatch.setattr(cognitive.event_store.SQLiteEventStore, "append",
                            no_skip)
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        assert _rows(conn, "SELECT 1 FROM projection_outbox") == []

        monkeypatch.undo()
        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.skips_reconstructed == 1, "quyết định không được cứu"
        assert _memories(conn) == 0, (
            "R1 SỐNG SÓT — mất hàng skip là marker bị dựng thành ký ức")
    finally:
        mos.close()


def test_r2_absence_read_as_owed_must_die(tmp_path, monkeypatch):
    """R2: quay lại luật cũ "không có outbox row = còn nợ" — marker sẽ được
    enqueue rồi materialize. Phải chết."""
    mos = _os(tmp_path)
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        conn.execute("DELETE FROM projection_outbox")
        conn.commit()

        # MUTANT: engine mù với mọi quyết định đã ghi
        monkeypatch.setattr(PE.ProjectionReplayEngine, "_recorded_decision",
                            lambda self, event_id: "projection")
        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.enqueued == 1, "mutant chưa chạm được vào đường nguy hiểm"
        assert _memories(conn) == 1, "mutant không gây hại — chưa đo được gì"
    finally:
        mos.close()
    # Ở bản đúng (không mutant), cùng kịch bản cho 0 ký ức — chứng ở
    # test_lost_skip_row_is_reconstructed_as_skip_not_as_memory.


def test_r3_ignoring_skip_during_version_rebuild_must_die(tmp_path,
                                                          monkeypatch):
    """R3: bump phiên bản mà bỏ qua quyết định skip → sinh job cho marker.

    `rebuild_version` có HAI cổng, và test phải nói rõ cổng nào giữ:
      (a) hàng SKIPPED ở BẤT KỲ version nào  → terminal
      (b) quyết định no_projection trong payload bất biến
    Chốt cũ chỉ có (a) và còn buộc đúng version hiện tại — nên v1→v2 là hở.
    """
    # --- R3a: xoá hàng SKIPPED (cổng (a) không thể nổ), bump version.
    #     Cổng (b) phải giữ một mình.
    mos = _os(tmp_path, "r3a.db")
    try:
        ClaudeCodeHookAdapter(mos, "t1", "w1").ingest("SessionStart", MARKER)
        conn = mos.memories.conn
        conn.execute("DELETE FROM projection_outbox")
        conn.commit()
        monkeypatch.setattr(PE, "current_version", lambda ptype: 2)
        report = ProjectionReplayEngine(conn).rebuild_version(
            "cognitive_memory", dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.skipped_no_projection == 1 and report.enqueued == 0, (
            "cổng quyết-định-trong-payload không giữ khi hàng SKIPPED biến mất")
        assert _memories(conn) == 0
    finally:
        mos.close()

    # --- R3b: tắt CẢ HAI → mutant phải chạm được vào lỗi, nếu không thì R3a
    #     ở trên chưa chứng minh gì.
    mos2 = _os(tmp_path, "r3b.db")
    try:
        ClaudeCodeHookAdapter(mos2, "t1", "w1").ingest("SessionStart", MARKER)
        conn2 = mos2.memories.conn
        conn2.execute("DELETE FROM projection_outbox")
        conn2.commit()
        monkeypatch.setattr(PE.ProjectionReplayEngine, "_recorded_decision",
                            lambda self, event_id: None)
        report = ProjectionReplayEngine(conn2).rebuild_version(
            "cognitive_memory", dry_run=False)
        worker_for(mos2, lease_seconds=300).run_once(batch_size=10)
        assert report.enqueued == 1, (
            "R3 không tái hiện được — chưa chứng minh cổng version có tác dụng")
        assert _memories(conn2) == 1, "mutant không gây hại — chưa đo được gì"
    finally:
        mos2.close()

    # --- và bản ĐÚNG với hàng SKIPPED còn nguyên: bump version vẫn im.
    #     GỠ mutant trước — một "bản đúng" đo dưới mutant còn sống thì không
    #     phải bản đúng, đó chỉ là mutant đo lần thứ hai.
    monkeypatch.undo()
    monkeypatch.setattr(PE, "current_version", lambda ptype: 2)
    mos3 = _os(tmp_path, "r3_ok.db")
    try:
        ClaudeCodeHookAdapter(mos3, "t1", "w1").ingest("SessionStart", MARKER)
        report = ProjectionReplayEngine(mos3.memories.conn).rebuild_version(
            "cognitive_memory", dry_run=False)
        worker_for(mos3, lease_seconds=300).run_once(batch_size=10)
        assert report.enqueued == 0 and report.skipped_no_projection == 1
        assert _memories(mos3.memories.conn) == 0, (
            "quyết định không-chiếu hết hạn khi builder lên đời")
    finally:
        mos3.close()


def test_r4_unknown_intent_assumed_owed_must_die(tmp_path, monkeypatch):
    """R4: coi UNKNOWN là "còn nợ" → tự dựng ký ức từ một event chưa ai nói
    gì về nó. Cổng phải ĐỎ, không được đoán."""
    mos = _os(tmp_path)
    try:
        mos.observe(tenant_id="t1", actor="a", source="legacy-import",
                    content="dòng lịch sử không ghi ý định RC0-UNKNOWN",
                    workspace_id="w1", enqueue_projection=False)
        conn = mos.memories.conn
        # MUTANT: UNKNOWN được coi là owed
        monkeypatch.setattr(PE.ProjectionReplayEngine, "_recorded_decision",
                            lambda self, event_id: "projection")
        report = ProjectionReplayEngine(conn).replay(dry_run=False)
        worker_for(mos, lease_seconds=300).run_once(batch_size=10)
        assert report.enqueued == 1 and _memories(conn) == 1, (
            "R4 chưa chạm được vào đường nguy hiểm")
    finally:
        mos.close()


def test_r5_deciding_skip_from_marker_text_alone_must_die(tmp_path):
    """R5: đoán "không chiếu" theo HÌNH DẠNG CHỮ.

    Fixture đúng hình dạng đã đo trên store thật: event trông y hệt marker
    (`hook=SessionStart`) NHƯNG đã có ký ức materialize từ thời legacy. Ai
    quyết theo chữ sẽ dán nhãn "không nợ gì" lên một hàng đang có projection
    thật — và đó là lời khai sai vĩnh viễn về provenance."""
    mos = MemoryOS(tmp_path / "r5.db", projection_mode="legacy")
    try:
        ev = mos.observe(tenant_id="t1", actor="claude-code",
                         source="claude-code:SessionStart",
                         content="hook=SessionStart", workspace_id="w1",
                         enqueue_projection=False)
        mos.remember(event=ev, memory_type=MemoryType.EPISODIC,
                     content="hook=SessionStart", confidence=0.72)
        conn = mos.memories.conn

        # Luật đúng: KHÔNG có quyết định nào được ghi → UNKNOWN, không phải
        # "skipped vì trông như marker".
        scan = ProjectionReplayEngine(conn).scan()
        assert [c.reason for c in scan.candidates] == \
            [ReplayReason.UNKNOWN_INTENT.value], (
            "engine đoán theo chữ: một event CÓ ký ức bị coi là không nợ gì")

        # MUTANT R5, viết tường minh để thấy nó sai ở đâu:
        def by_text(self, event_id):
            row = self.conn.execute(
                "SELECT payload_json FROM cognitive_events WHERE event_id=?",
                (event_id,)).fetchone()
            import json
            content = str(json.loads(row["payload_json"]).get("content", ""))
            return NO_PROJECTION if content.startswith("hook=") \
                and " | " not in content else "projection"

        engine = ProjectionReplayEngine(conn)
        engine._recorded_decision = by_text.__get__(engine)
        mutant = engine.scan()
        assert [c.reason for c in mutant.candidates] == \
            [ReplayReason.SKIP_ROW_LOST.value], (
            "mutant không tái hiện được — R5 chưa chứng minh gì")
        # và đó là kết luận SAI: hàng này có ký ức thật đang sống
        assert _memories(conn) == 1
    finally:
        mos.close()
