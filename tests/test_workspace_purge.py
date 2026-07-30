"""
Xoá có kiểm chứng theo workspace (quyền được lãng quên).

"Kho thô bất tử" nghĩa là thuật toán QUÊN không xoá dữ liệu — không có nghĩa
chủ dữ liệu không xoá được. Test này đảm bảo khi đã xoá thì thực sự hết, và
workspace khác không bị ảnh hưởng.
"""
from bio_agent_os.memory.coverage_index import CoverageIndex
from bio_agent_os.memory.episodes import EpisodeStore

MINE = "ws-mine"
OTHER = "ws-other"


def _seed(tmp_path):
    store = EpisodeStore(agent_name="P", storage_dir=str(tmp_path))
    for i in range(6):
        store.add(raw_payload=f"Bí mật của tôi số {i}.", workspace_id=MINE)
    for i in range(4):
        store.add(raw_payload=f"Dữ liệu người khác số {i}.", workspace_id=OTHER)
    index = CoverageIndex(
        agent_name="P", storage_dir=str(tmp_path), episodes=store, group_size=3
    )
    index.refresh(workspace_id=MINE)
    index.refresh(workspace_id=OTHER)
    return store, index


def test_purge_removes_everything_for_that_workspace(tmp_path):
    store, index = _seed(tmp_path)
    assert store.workspace_count(MINE) == 6

    deleted = store.purge_workspace(MINE)
    nodes = index.purge_workspace(MINE)

    assert deleted == 6
    assert nodes > 0
    # Kiểm chứng: đếm lại phải bằng 0, không chỉ tin vào giá trị trả về.
    assert store.workspace_count(MINE) == 0
    assert store.search_text("bí mật của tôi", workspace_id=MINE, limit=5) == []


def test_purge_does_not_touch_other_workspaces(tmp_path):
    store, index = _seed(tmp_path)
    store.purge_workspace(MINE)
    index.purge_workspace(MINE)

    assert store.workspace_count(OTHER) == 4
    report = index.coverage_report(OTHER)
    assert report["coverage_pct"] == 100.0
    assert report["lossless"] is True


def test_purge_is_idempotent_and_safe_on_empty(tmp_path):
    store, _ = _seed(tmp_path)
    assert store.purge_workspace(MINE) == 6
    assert store.purge_workspace(MINE) == 0
    assert store.purge_workspace("") == 0
