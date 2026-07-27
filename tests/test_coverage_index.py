"""
Kiểm chứng Verified-Coverage Multi-Resolution Index (VCMI):
  1. Độ phủ 100% — không sót episode nào (đo được, không phải lời hứa).
  2. Zoom về nguồn — node tóm tắt giải ra ĐÚNG episode thô nguyên văn (reversibility).
  3. Truy hồi cấp cao rồi zoom → tìm lại "cây kim trong đống rơm".
  4. Cập nhật tăng dần vẫn giữ độ phủ 100%.
  5. QUÊN = chỉ hạ hạng chỉ mục; kho thô BẤT TỬ → vẫn zoom lại được nguyên văn.
"""
import time

from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.coverage_index import CoverageIndex

WS = "w-test"
NEEDLE = "Mã dự án bí mật của khách hàng là ZEPHYR-7X, tuyệt đối không được tiết lộ."


def _seed(store: EpisodeStore, n=50, needle_at=23):
    for i in range(n):
        payload = NEEDLE if i == needle_at else f"Ghi chú thường nhật số {i}: công việc diễn ra bình thường."
        store.add(raw_payload=payload, workspace_id=WS, observation_type="chat_turn")


def _build(tmp_path):
    store = EpisodeStore(agent_name="T", storage_dir=str(tmp_path))
    _seed(store)
    idx = CoverageIndex(agent_name="T", storage_dir=str(tmp_path), episodes=store, group_size=6)
    idx.refresh(workspace_id=WS)
    return store, idx


def test_coverage_is_100_percent_lossless(tmp_path):
    _, idx = _build(tmp_path)
    rep = idx.coverage_report(WS)
    assert rep["total_episodes"] == 50
    assert rep["coverage_pct"] == 100.0
    assert rep["orphans"] == []
    assert rep["lossless"] is True
    assert rep["parent_union_ok"] is True


def test_zoom_recovers_exact_source_verbatim(tmp_path):
    _, idx = _build(tmp_path)
    # Tìm node lá phủ cây kim, zoom về, phải ra ĐÚNG nguyên văn.
    found = False
    for node in idx._nodes_at_level(WS, 1):
        raws = [e["raw_payload"] for e in idx.zoom(node["node_id"])]
        if NEEDLE in raws:
            found = True
            break
    assert found, "Zoom phải phục hồi được episode thô nguyên văn (reversibility)."


def test_retrieve_then_zoom_finds_needle(tmp_path):
    _, idx = _build(tmp_path)
    hits = idx.retrieve("mã dự án bí mật ZEPHYR", workspace_id=WS, top_k=3, level=1)
    assert hits, "Phải truy hồi được node liên quan."
    recovered = []
    for h in hits:
        recovered += [e["raw_payload"] for e in idx.zoom(h["node_id"])]
    assert NEEDLE in recovered, "Truy hồi cấp cao rồi zoom phải tìm lại cây kim."


def test_incremental_refresh_keeps_full_coverage(tmp_path):
    store, idx = _build(tmp_path)
    before = idx.coverage_report(WS)["total_episodes"]
    for i in range(7):
        store.add(raw_payload=f"Episode mới thêm {i}", workspace_id=WS)
    idx.refresh(workspace_id=WS)
    rep = idx.coverage_report(WS)
    assert rep["total_episodes"] == before + 7
    assert rep["coverage_pct"] == 100.0
    assert rep["lossless"] is True


def test_forgetting_is_ranking_only_data_immortal(tmp_path):
    """Quên: node cây kim bị 'cũ đi' (retention thấp, xếp hạng tụt) NHƯNG kho thô
    bất tử — zoom vẫn phục hồi nguyên văn. Tách bạch 'ngừng chú ý' ≠ 'mất dữ liệu'."""
    _, idx = _build(tmp_path)
    # Xác định node lá chứa cây kim.
    needle_node = None
    for node in idx._nodes_at_level(WS, 1):
        if NEEDLE in [e["raw_payload"] for e in idx.zoom(node["node_id"])]:
            needle_node = node["node_id"]
            break
    assert needle_node

    # Ép "quên": last_access 120 ngày trước, strength thấp → retention ~ 0.
    old = time.time() - 120 * 86400
    idx._store.execute(
        f"UPDATE {idx._table} SET last_access = ?, strength = 1.0 WHERE node_id = ?",
        [old, needle_node],
    )
    hits = idx.retrieve("ghi chú thường nhật công việc", workspace_id=WS, top_k=10, level=1)
    ranked = {h["node_id"]: h for h in hits}
    # Node bị quên có retention rất thấp (đã tụt hạng).
    if needle_node in ranked:
        assert ranked[needle_node]["retention"] < 0.05

    # NHƯNG dữ liệu vẫn BẤT TỬ: zoom phục hồi nguyên văn + coverage vẫn 100%.
    assert NEEDLE in [e["raw_payload"] for e in idx.zoom(needle_node)]
    assert idx.coverage_report(WS)["coverage_pct"] == 100.0
