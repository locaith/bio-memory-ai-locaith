"""SP-1 — semantic parity gate: hai writer, MỘT hợp đồng, MỘT constructor.

    CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT
    EXACTLY-ONCE EXECUTION != SEMANTIC PARITY

Probe cùng-MỘT-event: SP-0 bản đầu so hai clone riêng nên `observed_at` và
`source_event_ids` lệch vì population, không vì đường ghi — vị từ đúng đặt
lên sai tập hợp, lần nữa. Ở đây một event duy nhất được materialize bằng CẢ
HAI đường trong cùng một store, nên mọi khác biệt còn lại là của ĐƯỜNG GHI.

Overlay được chấp nhận, có hồ sơ: builder outbox thêm đúng MỘT namespace
`metadata["projection"]` (key/type/version/source_event_id) — provenance hạ
tầng, tách khỏi semantic metadata của người ghi. Ngoài nó ra: mọi field phải
trùng.
"""
from __future__ import annotations

import json
from pathlib import Path

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.projection_intent import intent_from_payload
from bio_agent_os.cognitive.reconciliation_worker import worker_for

PROMPT = {"hook_event_name": "UserPromptSubmit", "session_id": "sp1",
          "prompt": "SP1 parity: khách hàng An Phú chốt hợp đồng 120 triệu "
                    "tháng 9, mã SP1-PARITY."}
SKIP = {"memory_id", "created_at", "updated_at", "last_accessed_at",
        "retrieval_count"}


def _rows_pair(memory_os):
    conn = memory_os.memories.conn
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM cognitive_memories WHERE content LIKE '%SP1-PARITY%'")]
    assert len(rows) == 2, f"cần đúng 2 bản (legacy + outbox): {len(rows)}"
    return rows


def _semantic_meta(raw: str) -> dict:
    meta = json.loads(raw or "{}")
    meta.pop("projection", None)          # overlay được chấp nhận, có hồ sơ
    return meta


def test_same_event_two_writers_produce_identical_semantics(tmp_path):
    memory_os = MemoryOS(tmp_path / "p.db", projection_mode="outbox")
    try:
        adapter = ClaudeCodeHookAdapter(memory_os, "t1", "w1")
        result = adapter.ingest("UserPromptSubmit", PROMPT)
        # đường 1: worker (outbox)
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        # đường 2: remember() trên CÙNG event, từ CÙNG intent trong payload
        payload = json.loads(memory_os.events.conn.execute(
            "SELECT payload_json FROM cognitive_events WHERE event_id=?",
            (result.event_id,)).fetchone()[0])
        intent = intent_from_payload(payload)
        assert intent is not None, "event mới phải mang intent trong payload"
        from bio_agent_os.cognitive.models import EventRecord, TrustTier, \
            SecurityLabel
        ev_row = dict(memory_os.events.conn.execute(
            "SELECT * FROM cognitive_events WHERE event_id=?",
            (result.event_id,)).fetchone())
        # remember() cần EventRecord — dựng lại từ hàng bền (đường legacy thật
        # đi qua cùng constructor nên tương đương; admissibility: cùng event_id)
        # Dựng lại PHẢI mang đủ nguồn gốc — bản đầu quên trust_tier/security
        # và parity "vỡ" ở đúng field probe tự đánh rơi. Vị từ đúng, tập sai.
        event = EventRecord(
            event_id=ev_row["event_id"], tenant_id=ev_row["tenant_id"],
            workspace_id=ev_row["workspace_id"], actor=ev_row["actor"],
            source=ev_row["source"], payload=payload,
            trust_tier=TrustTier(ev_row["trust_tier"]),
            security_label=SecurityLabel(ev_row["security_label"]),
            observed_at=ev_row.get("observed_at"),
            valid_from=ev_row.get("valid_from"), valid_to=ev_row.get("valid_to"))
        memory_os.remember(event=event, memory_type=MemoryType.EPISODIC,
                           content=str(payload.get("content", "")),
                           confidence=intent.confidence,
                           importance=intent.importance,
                           salience=intent.salience, utility=intent.utility,
                           metadata=intent.semantic_metadata)

        a, b = _rows_pair(memory_os)
        diffs = {}
        for k in sorted(set(a) | set(b)):
            if k in SKIP:
                continue
            va, vb = a.get(k), b.get(k)
            if k == "metadata_json":
                va, vb = _semantic_meta(va), _semantic_meta(vb)
            if va != vb:
                diffs[k] = (str(va)[:60], str(vb)[:60])
        assert not diffs, f"semantic parity vỡ: {diffs}"
    finally:
        memory_os.close()


def test_retrieval_scores_identical_across_writers(tmp_path):
    """Cùng nội dung, hai đường ghi ở hai store — điểm phải bằng nhau."""
    scores = {}
    for mode in ("legacy", "outbox"):
        memory_os = MemoryOS(tmp_path / f"{mode}.db", projection_mode=mode)
        try:
            ClaudeCodeHookAdapter(memory_os, "t1", "w1").ingest(
                "UserPromptSubmit", PROMPT)
            if mode == "outbox":
                worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
            got = memory_os.recall("khách hàng An Phú hợp đồng",
                                   context=AccessContext(tenant_id="t1"),
                                   limit=3)
            scores[mode] = getattr(got[0], "score", None) if got else None
        finally:
            memory_os.close()
    assert scores["legacy"] == scores["outbox"], scores


def test_mutant_dropping_intent_breaks_parity(tmp_path, monkeypatch):
    """INTENT_DROPPED — builder quay về default model: parity phải vỡ.

    Đây chính là regression SP-0 (0.72→0.5). Nếu mutant này không làm gate đỏ
    thì gate đang xanh vì lý do khác, không vì hợp đồng được mang theo."""
    from bio_agent_os.cognitive import projection_intent as pi

    memory_os = MemoryOS(tmp_path / "m.db", projection_mode="outbox")
    try:
        monkeypatch.setattr(pi, "intent_from_payload", lambda payload: None)
        # builder import cục bộ trong hàm — patch tại module gốc là đủ vì nó
        # import lại mỗi lần build
        adapter = ClaudeCodeHookAdapter(memory_os, "t1", "w1")
        adapter.ingest("UserPromptSubmit", PROMPT)
        worker_for(memory_os, lease_seconds=300).run_once(batch_size=5)
        row = memory_os.memories.conn.execute(
            "SELECT confidence FROM cognitive_memories "
            "WHERE content LIKE '%SP1-PARITY%'").fetchone()
        assert row is not None and abs(row[0] - 0.5) < 1e-9, (
            f"mutant bỏ intent mà confidence={row[0]} != 0.5 — gate xanh vì "
            f"lý do khác, không vì intent được mang theo")
    finally:
        memory_os.close()
