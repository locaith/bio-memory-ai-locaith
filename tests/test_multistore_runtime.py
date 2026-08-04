"""The six stores of one runtime must share one database.

Plain ``:memory:`` gives every SQLite connection a private database. With six
stores that produced six databases which could not see each other, so a
consistency test could pass while proving nothing — see
docs/v082/WRITE_PATH_ANALYSIS.md section 2.
"""

from __future__ import annotations

import sqlite3

import pytest

from bio_agent_os.cognitive import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.sqlite_utils import (
    connect_sqlite,
    is_plain_memory,
    release_anchor,
    resolve_runtime_path,
    shared_memory_uri,
)

STORES = ("events", "memories", "self_model", "prospective", "context_blocks", "checkpoints")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


# -- the underlying problem -------------------------------------------------


def test_plain_memory_really_does_isolate_connections():
    """The behaviour this whole fix exists for."""
    a, b = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    a.execute("CREATE TABLE t(x)")
    a.commit()
    with pytest.raises(sqlite3.OperationalError):
        b.execute("SELECT * FROM t")
    a.close()
    b.close()


def test_shared_uri_connections_see_each_other():
    uri = shared_memory_uri("unit-test-shared")
    a = connect_sqlite(uri)
    b = connect_sqlite(uri)
    a.execute("CREATE TABLE t(x)")
    a.execute("INSERT INTO t VALUES(1)")
    a.commit()
    assert b.execute("SELECT x FROM t").fetchone()[0] == 1
    a.close()
    b.close()
    release_anchor(uri)


def test_resolve_rewrites_plain_memory_only():
    assert resolve_runtime_path(":memory:").startswith("file:bio_agent_os_")
    assert resolve_runtime_path("/tmp/x.db") == "/tmp/x.db"
    assert resolve_runtime_path("file:already?mode=memory") == "file:already?mode=memory"


def test_two_runtimes_do_not_share_a_database():
    first, second = resolve_runtime_path(":memory:"), resolve_runtime_path(":memory:")
    assert first != second, "separate runtimes must stay isolated"


def test_is_plain_memory():
    assert is_plain_memory(":memory:")
    assert is_plain_memory("  :memory:  ")
    assert not is_plain_memory("file:x?mode=memory&cache=shared")
    assert not is_plain_memory("/tmp/x.db")


# -- the runtime ------------------------------------------------------------


def test_memory_os_no_longer_uses_plain_memory():
    os_ = MemoryOS(":memory:")
    assert os_.db_path != ":memory:"
    assert "mode=memory" in os_.db_path and "cache=shared" in os_.db_path


def test_all_six_stores_see_one_database():
    """The assertion that could not previously be made."""
    os_ = MemoryOS(":memory:")
    connections = [getattr(os_, name).conn for name in STORES]
    assert len(connections) == 6

    tables = [_table_names(c) for c in connections]
    union = set().union(*tables)
    for name, seen in zip(STORES, tables):
        assert seen == union, f"{name} sees a different schema — separate databases"

    assert "cognitive_events" in union
    assert "cognitive_memories" in union
    assert "projection_outbox" in union


def test_a_write_through_one_store_is_visible_from_another():
    os_ = MemoryOS(":memory:")
    event = os_.observe(tenant_id="t1", actor="a", source="unit", content="hello")

    seen = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events WHERE event_id=?", (event.event_id,)
    ).fetchone()[0]
    assert seen == 1, "the projection store must see the event store's write"


def test_orphan_detection_is_now_possible_in_one_query():
    """Previously impossible: the two tables lived in different databases."""
    os_ = MemoryOS(":memory:")
    event = os_.observe(tenant_id="t1", actor="a", source="unit", content="orphan")

    orphans = os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events e "
        "WHERE NOT EXISTS (SELECT 1 FROM cognitive_memories m "
        "                  WHERE m.source_event_ids_json LIKE '%' || e.event_id || '%')"
    ).fetchone()[0]
    assert orphans == 1

    os_.remember(event=event, memory_type=MemoryType.EPISODIC, content="orphan")
    orphans_after = os_.events.conn.execute(
        "SELECT COUNT(*) FROM cognitive_events e "
        "WHERE NOT EXISTS (SELECT 1 FROM cognitive_memories m "
        "                  WHERE m.source_event_ids_json LIKE '%' || e.event_id || '%')"
    ).fetchone()[0]
    assert orphans_after == 0


def test_file_backed_runtime_is_unaffected(tmp_path):
    path = tmp_path / "runtime.db"
    os_ = MemoryOS(path)
    assert os_.db_path == str(path)
    os_.observe(tenant_id="t1", actor="a", source="unit", content="on disk")
    assert path.exists()


def test_two_runtimes_do_not_leak_into_each_other():
    a, b = MemoryOS(":memory:"), MemoryOS(":memory:")
    a.observe(tenant_id="t1", actor="a", source="unit", content="only in a")
    assert a.events.conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 1
    assert b.events.conn.execute("SELECT COUNT(*) FROM cognitive_events").fetchone()[0] == 0
