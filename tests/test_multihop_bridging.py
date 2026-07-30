"""
Truy hồi bắc cầu cho câu hỏi nhiều bước.

Một chuỗi multi-hop cần hai dữ kiện, và dữ kiện thứ hai thường không chia sẻ
từ nào với câu hỏi — nó chỉ nối với dữ kiện thứ nhất qua một thực thể chung.
Truy hồi một vòng theo câu hỏi vì thế bỏ sót nó.
"""
from bio_agent_os.memory.episodes import EpisodeStore

WS = "ws-hop"

# Chuỗi: câu hỏi -> "ai phụ trách Metub?" -> HOP1 nói Metub do Minh phụ trách
# -> HOP2 nói Minh làm ở Đà Nẵng. Câu hỏi "văn phòng nào phụ trách Metub" cần CẢ HAI,
# và HOP2 không chứa chữ "Metub" nào.
HOP1 = "Hợp đồng với đối tác Metub do anh Minh phụ trách."
HOP2 = "Anh Minh làm việc tại văn phòng Đà Nẵng."
NOISE = [f"Ghi chú nội bộ số {i}: tiến độ bình thường." for i in range(40)]


def _seed(tmp_path):
    store = EpisodeStore(agent_name="H", storage_dir=str(tmp_path))
    store.add(raw_payload=HOP1, workspace_id=WS)
    store.add(raw_payload=HOP2, workspace_id=WS)
    for text in NOISE:
        store.add(raw_payload=text, workspace_id=WS)
    return store


def test_single_hop_retrieval_misses_the_bridging_fact(tmp_path):
    """Ghi lại hành vi CŨ: một vòng chỉ thấy dữ kiện có chứa từ của câu hỏi."""
    store = _seed(tmp_path)
    results = store.search_text("đối tác Metub", limit=6, workspace_id=WS)
    payloads = [record["raw_payload"] for record in results]
    assert HOP1 in payloads, "Dữ kiện trực tiếp phải tìm được"
    # HOP2 không chứa 'Metub' nên một vòng không có lý do gì tìm ra nó.
    assert HOP2 not in payloads


def test_expanded_retrieval_reaches_the_bridging_fact(tmp_path):
    store = _seed(tmp_path)
    results = store.search_text_expanded("đối tác Metub", limit=6, workspace_id=WS)
    payloads = [record["raw_payload"] for record in results]
    assert HOP1 in payloads, "Dữ kiện trực tiếp vẫn phải có"
    assert HOP2 in payloads, (
        "Vòng 2 phải bắc cầu qua thực thể 'Minh' để tới dữ kiện thứ hai — "
        "nếu không, câu hỏi nhiều bước không thể trả lời được."
    )


def test_expansion_respects_the_limit_and_workspace(tmp_path):
    store = _seed(tmp_path)
    results = store.search_text_expanded("đối tác Metub", limit=6, workspace_id=WS)
    assert len(results) <= 6
    assert all(record["workspace_id"] == WS for record in results)
    assert store.search_text_expanded("đối tác Metub", limit=6, workspace_id="ws-khac") == []
