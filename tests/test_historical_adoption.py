"""HBF-2 — lễ nhập tịch phải tự chứng minh bằng dao, không bằng lời.

    K1  ADOPT → REPLAY      không duplicate
    K2  ADOPT → FORGET      không resurrection
    K3  ADOPT → RESTART     managed state bền
    K4  ADOPT → REAPPLY     idempotent — lần hai cấp thêm hộ chiếu là máy photocopy
    ADMISSIBILITY           abort trước COMMIT → zero partial state
    M1  MISSING_LEDGER = MISSING_PROJECTION      MUST DIE (+ witness đường nguy hiểm)
    M2  đổi một trường FULL thật sự kiểm, content giữ nguyên   MUST DIE

Store synthetic dựng bằng CHÍNH đường ghi legacy (adapter mode legacy →
event + memory, không outbox, không ledger) — đúng hình dạng lịch sử thật,
không phải INSERT tay mô phỏng.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.forgetting import forget_derived
from bio_agent_os.cognitive.historical_adoption import (
    verify_closure, verify_closure_from_audit,
    ADOPT_CLASSES, AdmissibilityError, InjectedAbort, adopt, adoption_gate,
    adoption_invariants, classify_store, tables_digest)
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.outbox import projection_key
from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.tombstones import place

RUN = "hbf2-test-run"
SNAP = "test-snapshot-sha"
ALL_TABLES = ("cognitive_memories", "memory_source_events",
              "memory_tombstones", "cognitive_events", "projection_outbox",
              "projection_ledger", "projection_adoption_audit")


def _prompt(i: int) -> dict:
    return {"hook_event_name": "UserPromptSubmit", "session_id": "hist",
            "prompt": f"khách hàng số {i} chốt hợp đồng HBF2-SYN-{i}"}


def _build_history(path: Path) -> MemoryOS:
    """3 hook FULL + 1 drift 30ms + 1 event-only + 1 curated + 1 tombstoned."""
    mos = MemoryOS(path, projection_mode="legacy")
    adapter = ClaudeCodeHookAdapter(mos, "t1", "w1")
    for i in range(4):
        adapter.ingest("UserPromptSubmit", _prompt(i))
    adapter.ingest("SessionStart", {"hook_event_name": "SessionStart",
                                    "session_id": "hist"})     # marker-only
    ev = mos.observe(tenant_id="t1", workspace_id="w1", actor="curator",
                     source="session-2026-08-06-claude-code",
                     content="bài học font chữ HBF2-SYN-CURATED",
                     enqueue_projection=False)
    mos.remember(event=ev, memory_type=MemoryType.PROCEDURAL,
                 content="bài học font chữ HBF2-SYN-CURATED",
                 confidence=0.95, importance=0.9,
                 metadata={"kind": "error_lesson", "provenance": "curated"})
    # marker-only NHƯNG CÓ ký ức — hình dạng thật từ 07/08, thời legacy còn
    # materialize cả marker. Nội dung nói "không đáng nhớ", quan hệ nói "đã
    # có projection". Quan hệ mới là thứ quyết định có nợ hay không.
    mk = mos.observe(tenant_id="t1", workspace_id="w1", actor="claude-code",
                     source="claude-code:SessionStart",
                     content="hook=SessionStart", enqueue_projection=False,
                     metadata={"hook": "SessionStart", "session_id": "hist",
                               "tool": None})
    mos.remember(event=mk, memory_type=MemoryType.EPISODIC,
                 content="hook=SessionStart", confidence=0.72, importance=0.35,
                 salience=0.50, utility=0.65,
                 metadata={"hook": "SessionStart", "session_id": "hist",
                           "tool": None, "state": {"mode": "implement"}})
    tomb = adapter.ingest("UserPromptSubmit", {
        "hook_event_name": "UserPromptSubmit", "session_id": "hist",
        "prompt": "hàng này sẽ bị chôn HBF2-SYN-TOMB"})
    place(mos.memories.conn, event_ids=[tomb.event_id], scope="derived",
          actor="test", reason="fixture tombstone")

    # hàng drift: đường ghi cũ đóng dấu đồng hồ riêng, trễ 30ms — tái hiện
    # đúng cơ chế HBF-1.1 bằng cách lùi observed_at của memory khỏi event.
    conn = mos.memories.conn
    row = conn.execute(
        "SELECT m.memory_id, e.observed_at FROM cognitive_memories m "
        "JOIN memory_source_events s ON s.memory_id = m.memory_id "
        "JOIN cognitive_events e ON e.event_id = s.event_id "
        "WHERE m.content LIKE '%HBF2-SYN-0%'").fetchone()
    drifted = (datetime.fromisoformat(str(row[1]))
               + timedelta(milliseconds=30)).isoformat()
    conn.execute("UPDATE cognitive_memories SET observed_at=? "
                 "WHERE memory_id=?", (drifted, row[0]))
    conn.commit()
    return mos


def _slot_fn(mos: MemoryOS):
    return lambda content, source: mos._structured_slot(content, source)


def _classified(mos: MemoryOS):
    return classify_store(mos.memories.conn, slot_fn=_slot_fn(mos))


def _copy_db(mos: MemoryOS, dest: Path) -> None:
    dst = sqlite3.connect(str(dest))
    mos.memories.conn.commit()
    mos.memories.conn.backup(dst)
    dst.close()


@pytest.fixture()
def history(tmp_path):
    mos = _build_history(tmp_path / "hist.db")
    yield mos
    mos.close()


def test_marker_only_with_materialized_memory_is_adopted_not_skipped(history):
    """MARKER-ONLY KHÔNG ĐỦ ĐỂ NÓI "KHÔNG NỢ GÌ".

    Ghi audit `skipped_event_only` cho một event đã CÓ ký ức là khai sai
    provenance, và bỏ ký ức đó lại không ledger — đúng thứ lễ nhập tịch sinh
    ra để ngăn. Bắt được nhờ review đối kháng trước HBF-3, trên 3 hàng
    `hook=SessionStart` thật của store."""
    conn = history.memories.conn
    report = _classified(history)
    marker = conn.execute(
        "SELECT s.event_id FROM memory_source_events s "
        "JOIN cognitive_memories m ON m.memory_id = s.memory_id "
        "WHERE m.content = 'hook=SessionStart'").fetchone()[0]
    d = next(x for x in report.rows if x.event_id == str(marker))
    assert d.cls in ADOPT_CLASSES, (
        f"marker-only CÓ ký ức bị xếp {d.cls} — audit sẽ nói dối và ký ức "
        f"này ở lại không ledger")
    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    assert conn.execute(
        "SELECT COUNT(*) FROM projection_ledger WHERE event_id=?",
        (str(marker),)).fetchone()[0] == 1
    action = conn.execute(
        "SELECT management_action FROM projection_adoption_audit "
        "WHERE event_id=?", (str(marker),)).fetchone()[0]
    assert action == "adopted", action
    assert adoption_invariants(conn)[
        "event_only_claim_with_materialized_memory"] == 0

    # mutant: hạ chính hàng này về "không nợ gì" — bất biến phải đỏ
    conn.execute("UPDATE projection_adoption_audit SET "
                 "management_action='skipped_event_only' WHERE event_id=?",
                 (str(marker),))
    conn.commit()
    assert adoption_invariants(conn)[
        "event_only_claim_with_materialized_memory"] == 1, (
        "khai 'không có projection' về event đang có ký ức mà không bị bắt")


def test_tombstoned_event_may_keep_a_live_pipeline_ledger(history):
    """Bia mộ CÓ ledger cũ là hợp lệ — đó là bằng chứng "đã từng dựng", và
    chính nó chặn replay hồi sinh. Cấm mọi ledger là cấm nhầm lịch sử.

    Hình dạng thật, bắt được ở HBF-3 sau khi install: hai event bia mộ mang
    ledger do `canary-a4` và `hook-22344` ghi từ khi chúng còn sống, rồi mới
    bị quên. Luật đúng: bia mộ không được có ledger DO MIGRATION ghi."""
    conn = history.memories.conn
    report = _classified(history)
    tomb = report.of("TOMBSTONE_EXCLUDE")[0]
    key = projection_key(tomb.event_id, "cognitive_memory", 1)
    from bio_agent_os.cognitive.reconciliation_worker import LEDGER_SCHEMA
    conn.executescript(LEDGER_SCHEMA)      # pipeline song da tao bang tu truoc
    conn.execute(
        "INSERT INTO projection_ledger (projection_key, event_id, "
        "projection_type, projection_version, tenant_id, target_id, "
        "worker_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (key, tomb.event_id, "cognitive_memory", 1, tomb.tenant_id,
         "da-bi-quen", "hook-22344", 0.0))
    conn.commit()

    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    closure = verify_closure_from_audit(conn)
    assert not closure["not_closed"], (
        f"ledger của pipeline sống bị coi là vi phạm: {closure['not_closed']}")

    # mutant: chính MIGRATION cấp ledger cho hàng bia mộ → phải đỏ
    conn.execute("UPDATE projection_ledger SET worker_id=? "
                 "WHERE projection_key=?", ("migration:hbf-adopt", key))
    conn.commit()
    bad = verify_closure_from_audit(conn)["not_closed"]
    assert any(c["action"] == "excluded_tombstoned" for c in bad), (
        "migration cấp hộ chiếu cho hàng đã chôn mà không bị bắt")


def test_classification_matches_population_shape(history):
    report = _classified(history)
    c = report.counts
    assert c["ADOPT_FULL_CONTRACT"] == 4      # 3 hook + 1 marker-có-ký-ức
    assert c["ADOPT_HISTORICAL_PARTIAL"] == 1
    assert c["ADOPT_CURATED_PRESERVED"] == 1
    assert c["EVENT_ONLY_SKIP"] == 1
    assert c["TOMBSTONE_EXCLUDE"] == 1
    assert all(c[k] == 0 for k in ("TRUE_MISSING", "AMBIGUOUS", "DIVERGENT",
                                   "ANOMALY", "UNEXPLAINED"))
    # structured_content đóng được trên chính code sinh ra nó
    for r in report.of("ADOPT_FULL_CONTRACT"):
        assert r.structured_content_status == "reconstructed_equal"
        # FULL nghĩa là FULL: hash stored == hash canonical, cùng skeleton
        assert r.projection_hash == r.canonical_candidate_hash
    partial = report.of("ADOPT_HISTORICAL_PARTIAL")[0]
    assert partial.projection_hash != partial.canonical_candidate_hash
    assert partial.observed_at_delta_ms is not None
    assert 0 < partial.observed_at_delta_ms < 100
    assert partial.proof == "full_contract_except_observed_at_epsilon_v1"
    curated = report.of("ADOPT_CURATED_PRESERVED")[0]
    assert curated.canonical_candidate_hash is None, (
        "hash đẹp nhưng vô nghĩa — curated phải NULL")
    assert curated.structured_content_status == "curated_preserved_as_authored"


def test_adopt_zero_semantic_delta_and_complete_triples(history):
    conn = history.memories.conn
    report = _classified(history)
    before = tables_digest(conn)          # semantic tables
    result = adopt(conn, report, migration_run_id=RUN,
                   source_snapshot_sha256=SNAP)
    assert result.committed
    assert result.adopted == 6      # 4 full (gom marker-co-ky-uc) + 1 partial + 1 curated
    assert result.skipped_event_only == 1
    assert result.excluded_tombstoned == 1
    assert tables_digest(conn) == before, "adoption đã chạm bảng semantic"
    inv = adoption_invariants(conn)
    assert all(v == 0 for k, v in inv.items() if k != "migration_rows_total"), inv
    assert inv["migration_rows_total"] == 7      # 6 completed + 1 skipped
    # replay sau adopt: không còn nợ actionable; bia mộ được tách riêng
    replay = ProjectionReplayEngine(conn).replay(dry_run=False)
    assert replay.enqueued == 0 and replay.reset == 0
    assert replay.skipped_tombstoned == 1


def test_admissibility_abort_before_commit_zero_partial(history):
    conn = history.memories.conn
    report = _classified(history)
    before = tables_digest(conn)
    with pytest.raises(InjectedAbort):
        adopt(conn, report, migration_run_id=RUN,
              source_snapshot_sha256=SNAP, fail_before_commit=True)
    assert tables_digest(conn) == before
    inv = adoption_invariants(conn)
    assert inv["migration_rows_total"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM projection_adoption_audit").fetchone()[0] == 0
    # và sau abort, lễ thật vẫn chạy được nguyên vẹn
    result = adopt(conn, report, migration_run_id=RUN,
                   source_snapshot_sha256=SNAP)
    assert result.adopted == 6 and result.committed


def test_k1_replay_no_duplicates_and_ledger_authority(history, tmp_path):
    conn = history.memories.conn
    adopt(conn, _classified(history), migration_run_id=RUN,
          source_snapshot_sha256=SNAP)
    history.memories.conn.commit()
    _copy_db(history, tmp_path / "k1.db")
    mos2 = MemoryOS(tmp_path / "k1.db", projection_mode="outbox")
    try:
        c2 = mos2.memories.conn
        mem_before = c2.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        ProjectionReplayEngine(c2).replay(dry_run=False)
        worker_for(mos2, lease_seconds=300).run_once(batch_size=50)
        assert c2.execute("SELECT COUNT(*) FROM cognitive_memories"
                          ).fetchone()[0] == mem_before
        # K1b — outbox row mất nhưng LEDGER là authority: replay enqueue lại,
        # worker trả lời ALREADY_BUILT, vẫn không có memory thứ hai.
        victim = c2.execute(
            "SELECT event_id FROM projection_ledger WHERE worker_id=? LIMIT 1",
            ("migration:hbf-adopt",)).fetchone()[0]
        c2.execute("DELETE FROM projection_outbox WHERE event_id=?", (victim,))
        c2.commit()
        replay = ProjectionReplayEngine(c2).replay(dry_run=False)
        assert replay.enqueued == 1
        worker_for(mos2, lease_seconds=300).run_once(batch_size=10)
        assert c2.execute("SELECT COUNT(*) FROM cognitive_memories"
                          ).fetchone()[0] == mem_before
    finally:
        mos2.close()


def test_k2_forget_after_adopt_no_resurrection(history, tmp_path):
    conn = history.memories.conn
    report = _classified(history)
    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    conn.commit()
    _copy_db(history, tmp_path / "k2.db")
    mos2 = MemoryOS(tmp_path / "k2.db", projection_mode="outbox")
    try:
        c2 = mos2.memories.conn
        target = report.of("ADOPT_FULL_CONTRACT")[0].target_id
        rep = forget_derived(mos2, memory_id=target, needle="HBF2-SYN")
        assert rep.scope is not None
        ProjectionReplayEngine(c2).replay(dry_run=False)
        worker_for(mos2, lease_seconds=300).run_once(batch_size=50)
        assert c2.execute(
            "SELECT COUNT(*) FROM cognitive_memories WHERE memory_id=?",
            (target,)).fetchone()[0] == 0, "ký ức đã quên sống lại sau replay"
    finally:
        mos2.close()


def test_k3_restart_managed_state_durable(history, tmp_path):
    conn = history.memories.conn
    result = adopt(conn, _classified(history), migration_run_id=RUN,
                   source_snapshot_sha256=SNAP)
    conn.commit()
    _copy_db(history, tmp_path / "k3.db")
    fresh = sqlite3.connect(str(tmp_path / "k3.db"))
    try:
        inv = adoption_invariants(fresh)
        assert all(v == 0 for k, v in inv.items()
                   if k != "migration_rows_total")
        assert inv["migration_rows_total"] == 7
        assert fresh.execute(
            "SELECT COUNT(*) FROM projection_adoption_audit "
            "WHERE management_action='adopted'").fetchone()[0] == result.adopted
    finally:
        fresh.close()


def test_k4_reapply_is_idempotent(history):
    conn = history.memories.conn
    report = _classified(history)
    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    before = tables_digest(conn, ALL_TABLES)
    # (a) cùng report chạy lại — toàn noop, không hàng mới
    second = adopt(conn, report, migration_run_id=RUN + "-2",
                   source_snapshot_sha256=SNAP)
    assert second.adopted == 0 and second.outbox_inserted == 0 \
        and second.ledger_inserted == 0 and second.audit_inserted == 0
    assert second.noop_reapply == 8       # 6 adopt + 1 event-only + 1 tombstone
    assert tables_digest(conn, ALL_TABLES) == before
    # (b) phân lớp TƯƠI sau adopt — mọi hàng adopt cũ giờ ALREADY_MANAGED
    fresh = classify_store(conn)
    assert all(fresh.counts[k] == 0 for k in ADOPT_CLASSES)
    assert fresh.counts["EVENT_ONLY_SKIP"] == 0
    third = adopt(conn, fresh, migration_run_id=RUN + "-3",
                  source_snapshot_sha256=SNAP)
    assert third.adopted == 0 and tables_digest(conn, ALL_TABLES) == before


def test_m1_missing_ledger_worldview_must_die(history, tmp_path):
    conn = history.memories.conn
    # (a) mutant: thế giới quan "không link = không projection" — xoá tầm nhìn
    # vào memory_source_events → mọi hàng legacy thành TRUE_MISSING → cổng đỏ.
    _copy_db(history, tmp_path / "m1a.db")
    mut = sqlite3.connect(str(tmp_path / "m1a.db"))
    try:
        mut.execute("DELETE FROM memory_source_events")
        mut.commit()
        blind = classify_store(mut)
        assert blind.counts["TRUE_MISSING"] >= 5
        with pytest.raises(AdmissibilityError):
            adoption_gate(blind)
    finally:
        mut.close()
    # (b) witness đường nguy hiểm: adopt xong mà ledger bị coi như không có →
    # replay dựng memory THỨ HAI. Đây là lý do ledger phải được ghi khi adopt.
    report = _classified(history)
    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    conn.commit()
    _copy_db(history, tmp_path / "m1b.db")
    mos2 = MemoryOS(tmp_path / "m1b.db", projection_mode="outbox")
    try:
        c2 = mos2.memories.conn
        victim = report.of("ADOPT_FULL_CONTRACT")[0]
        c2.execute("DELETE FROM projection_ledger WHERE event_id=?",
                   (victim.event_id,))
        c2.execute("DELETE FROM projection_outbox WHERE event_id=?",
                   (victim.event_id,))
        c2.commit()
        # bất biến BẮT vết thương ở cửa sổ phát hiện — TRƯỚC khi đường
        # nguy hiểm chạy và tự "liền da" bằng một bản duplicate
        assert adoption_invariants(c2)["audit_adopted_without_ledger"] == 1
        ProjectionReplayEngine(c2).replay(dry_run=False)
        worker_for(mos2, lease_seconds=300).run_once(batch_size=10)
        dup = c2.execute(
            "SELECT COUNT(*) FROM cognitive_memories WHERE content="
            "(SELECT content FROM cognitive_memories WHERE memory_id=?)",
            (victim.target_id,)).fetchone()[0]
        assert dup == 2, "witness phải chảy máu: mất ledger → duplicate"
    finally:
        mos2.close()


def test_m2_full_field_altered_content_identical_must_die(history, tmp_path):
    _copy_db(history, tmp_path / "m2.db")
    mut = sqlite3.connect(str(tmp_path / "m2.db"))
    try:
        victim = mut.execute(
            "SELECT memory_id FROM cognitive_memories "
            "WHERE content LIKE '%HBF2-SYN-1%'").fetchone()[0]
        mut.execute("UPDATE cognitive_memories SET trust_tier=4 "
                    "WHERE memory_id=?", (victim,))
        mut.commit()
        report = classify_store(mut)
        assert report.counts["DIVERGENT"] == 1
        bad = report.of("DIVERGENT")[0]
        assert any("trust_tier" in r for r in bad.reasons), bad.reasons
        with pytest.raises(AdmissibilityError):
            adoption_gate(report)
    finally:
        mut.close()


def test_audit_provenance_by_class_and_terminal_shape(history):
    """HBF-2.1: terminal row mang đúng hình dạng complete()/skip() —
    locked_by=NULL, locked_at=NULL; audit không điền cột NULL-able chỉ vì
    có sẵn một số nguyên. NOT_APPLICABLE phải phân biệt được với version 1."""
    conn = history.memories.conn
    adopt(conn, _classified(history), migration_run_id=RUN,
          source_snapshot_sha256=SNAP)
    # terminal shape: KHÔNG worker ma trong field lease
    ghosts = conn.execute(
        "SELECT COUNT(*) FROM projection_outbox o JOIN "
        "projection_adoption_audit a ON a.projection_key=o.projection_key "
        "WHERE o.locked_by IS NOT NULL OR o.locked_at IS NOT NULL"
    ).fetchone()[0]
    assert ghosts == 0, f"{ghosts} worker ma sống trong locked_by/locked_at"
    # provenance thật nằm ở ledger, không ở outbox
    assert conn.execute(
        "SELECT COUNT(*) FROM projection_ledger WHERE worker_id=?",
        ("migration:hbf-adopt",)).fetchone()[0] == 6
    rows = conn.execute(
        "SELECT management_action, contract_name, contract_version, "
        "builder_version_checked FROM projection_adoption_audit").fetchall()
    for action, contract, cver, builder in rows:
        if action == "adopted" and contract == "hook_call_site_v1":
            assert cver == 1 and builder == 1     # builder comparison ĐÃ xảy ra
        elif action == "adopted" and contract == "curated_seed_v1":
            assert cver == 1 and builder is None, (
                "curated không có builder comparison — NULL, không phải 1")
        elif action in ("skipped_event_only", "excluded_tombstoned"):
            assert contract is None and cver is None and builder is None, (
                f"{action} claim contract/builder chưa từng được áp")
        else:
            raise AssertionError(f"hàng audit lạ: {action}/{contract}")


def test_p1_mutant_lease_as_provenance_must_die(history):
    """P1: khôi phục locked_by='migration:hbf-adopt' trên terminal rows —
    bất biến terminal_migration_rows_with_lock phải đỏ."""
    conn = history.memories.conn
    adopt(conn, _classified(history), migration_run_id=RUN,
          source_snapshot_sha256=SNAP)
    assert adoption_invariants(conn)["terminal_migration_rows_with_lock"] == 0
    conn.execute(
        "UPDATE projection_outbox SET locked_by='migration:hbf-adopt' "
        "WHERE projection_key IN "
        "(SELECT projection_key FROM projection_adoption_audit)")
    conn.commit()
    caught = adoption_invariants(conn)["terminal_migration_rows_with_lock"]
    assert caught == 7, f"mutant P1 sống sót — chỉ bắt được {caught}/7"


def test_p2_mutant_curated_builder_claim_must_die(history):
    """P2: điền builder_version_checked=1 cho curated — audit sẽ nói dối
    'đã kiểm với builder v1' về một comparison chưa từng xảy ra."""
    conn = history.memories.conn
    adopt(conn, _classified(history), migration_run_id=RUN,
          source_snapshot_sha256=SNAP)
    assert adoption_invariants(conn)[
        "curated_builder_version_checked_nonnull"] == 0
    conn.execute(
        "UPDATE projection_adoption_audit SET builder_version_checked=1 "
        "WHERE contract_name='curated_seed_v1'")
    conn.commit()
    inv = adoption_invariants(conn)
    assert inv["curated_builder_version_checked_nonnull"] == 1, (
        "mutant P2 sống sót — curated claim builder mà không bị bắt")


def test_hbf3_0_closure_law_present_is_not_closed(history):
    """HBF3-0: PRESENT ≠ CLOSED. Ba row đủ mặt nhưng sai ruột KHÔNG được
    tính là reapply no-op — mutant C1 (sai status) và C2 (ledger trỏ sai
    ký ức) phải làm transaction đỏ, không phải im lặng đi qua."""
    conn = history.memories.conn
    report = _classified(history)
    adopt(conn, report, migration_run_id=RUN, source_snapshot_sha256=SNAP)
    closure = verify_closure(conn, report)
    assert not closure["not_closed"] and closure["closed"] == closure["checked"]

    victim = report.of("ADOPT_FULL_CONTRACT")[0]
    key = projection_key(victim.event_id, "cognitive_memory", 1)

    # C1 — triple đủ ba row, nhưng outbox mang status SAI
    conn.execute("UPDATE projection_outbox SET status='pending' "
                 "WHERE event_id=?", (victim.event_id,))
    conn.commit()
    assert any(c["event"] == victim.event_id[:8]
               for c in verify_closure(conn, report)["not_closed"]), \
        "closure law không thấy status sai"
    with pytest.raises(AdmissibilityError, match="closure conflict"):
        adopt(conn, report, migration_run_id=RUN + "-c1",
              source_snapshot_sha256=SNAP)
    conn.execute("UPDATE projection_outbox SET status='completed' "
                 "WHERE event_id=?", (victim.event_id,))
    conn.commit()

    # C2 — ledger đủ mặt nhưng trỏ SAI ký ức: hộ chiếu cấp nhầm người
    other = report.of("ADOPT_FULL_CONTRACT")[1].target_id
    conn.execute("UPDATE projection_ledger SET target_id=? "
                 "WHERE projection_key=?", (other, key))
    conn.commit()
    bad = verify_closure(conn, report)["not_closed"]
    assert any("target_id" in " ".join(c["why"]) for c in bad), bad
    with pytest.raises(AdmissibilityError, match="closure conflict"):
        adopt(conn, report, migration_run_id=RUN + "-c2",
              source_snapshot_sha256=SNAP)


def test_structured_content_closure_downgrades_not_forces(history):
    """Không tái tạo được → proof HẠ, count không ép; chưa đo → cổng ĐÓNG."""
    conn = history.memories.conn
    junk = classify_store(conn, slot_fn=lambda c, s: {"subject": "sai-toàn-tập"})
    downgraded = [r for r in junk.rows
                  if r.structured_content_status == "not_reconstructable_downgraded"]
    assert downgraded, "slot junk mà không hàng nào bị hạ proof"
    for r in downgraded:
        assert "except" in r.proof and "structured_content" in r.proof
        assert r.cls in ADOPT_CLASSES     # vẫn adopt — AS-IS, tên proof yếu đi
    adoption_gate(junk)                   # hạ proof KHÔNG chặn cổng
    unmeasured = classify_store(conn, slot_fn=None)
    with pytest.raises(AdmissibilityError):
        adoption_gate(unmeasured)         # chưa đóng trường → không được adopt
