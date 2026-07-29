"""
Kiểm chứng: KHÔNG còn "recall cliff".

Trước đây bộ ứng viên chỉ lấy ~500 bản ghi MỚI NHẤT, nên ký ức cũ tuy vẫn được
lưu (kho thô bất tử) nhưng LẤY RA KHÔNG ĐƯỢC — mâu thuẫn với tuyên bố
"không sót thông tin". Test này chôn một dữ kiện ở vị trí CŨ NHẤT rồi nhồi
hơn 600 bản ghi nhiễu lên trên và đòi lấy lại.
"""
import time

from bio_agent_os.memory.episodes import EpisodeStore

WS = "w-deep"
NEEDLE = "Mã hợp đồng khách hàng Vinaconex là VNX-2024-DELTA-77."
NOISE_COUNT = 620


def _seed(tmp_path):
    store = EpisodeStore(agent_name="D", storage_dir=str(tmp_path))
    base = time.time() - 90 * 86400  # cây kim: 90 ngày trước, cũ nhất
    store.add(raw_payload=NEEDLE, workspace_id=WS, observation_type="chat_turn")
    # Ép timestamp của cây kim về quá khứ để nó nằm ngoài cửa sổ recency.
    store._store.execute(
        f"UPDATE {store._table} SET timestamp = ? WHERE raw_payload = ?",
        [base, NEEDLE],
    )
    for i in range(NOISE_COUNT):
        store.add(
            raw_payload=f"Trao đổi thường ngày số {i}: mọi việc vẫn tiến triển bình thường.",
            workspace_id=WS,
            observation_type="chat_turn",
        )
    return store


def test_old_memory_still_retrievable_beyond_recency_window(tmp_path):
    store = _seed(tmp_path)
    assert store.count == NOISE_COUNT + 1

    hits = store.search_text("mã hợp đồng Vinaconex VNX", workspace_id=WS, limit=5)
    found = [h for h in hits if h["raw_payload"] == NEEDLE]
    assert found, (
        "Ký ức cũ nằm ngoài cửa sổ recency vẫn phải truy hồi được — "
        "nếu không, 'không sót thông tin' chỉ đúng ở khâu lưu, sai ở khâu lấy."
    )


def test_recall_is_workspace_isolated(tmp_path):
    store = _seed(tmp_path)
    store.add(raw_payload=NEEDLE, workspace_id="w-khac", observation_type="chat_turn")
    hits = store.search_text("mã hợp đồng Vinaconex VNX", workspace_id="w-khac", limit=5)
    assert all(h["workspace_id"] == "w-khac" for h in hits)


def test_normalized_column_backfills_for_legacy_rows(tmp_path):
    """Bản ghi cũ (chưa có norm_text) vẫn phải quét được sau khi nạp lại store."""
    store = _seed(tmp_path)
    store._store.execute(f"UPDATE {store._table} SET norm_text = NULL")
    reopened = EpisodeStore(agent_name="D", storage_dir=str(tmp_path))
    hits = reopened.search_text("mã hợp đồng Vinaconex VNX", workspace_id=WS, limit=5)
    assert any(h["raw_payload"] == NEEDLE for h in hits)
