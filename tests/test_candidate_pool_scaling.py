"""The candidate pool must not scan the memory table once per FTS match.

`candidate_pool` joins the FTS index to `cognitive_memories` on a computed
key, `f.memory_key = (m.memory_id || ':' || m.version)`. A computed join
predicate cannot use an ordinary index, so SQLite planned it as

    SCAN f VIRTUAL TABLE INDEX 0:M4
    SCAN m                              <-- 53,066 rows, once per FTS match

which is a nested-loop cross product. Measured on the 24-hour staging
canary database (53,066 memories, 2026-08-05): a recall whose terms matched
the caller's tenant took 23.0 s, while one that matched nothing returned in
0.005 s. Successful searches were the slow ones, so the corpus size, not the
query, set the cost.

These tests pin the shape rather than the wall clock: the plan must show a
SEARCH against `cognitive_memories`, never a SCAN. A timing assertion alone
would pass on a fast machine with the defect still present.
"""
from __future__ import annotations

import pathlib
import sqlite3
import time

import pytest

from bio_agent_os.cognitive.memory_store import SQLiteMemoryStore
from bio_agent_os.cognitive.models import CognitiveMemory, MemoryType

#: The join `candidate_pool` issues, verbatim.
FTS_JOIN = """SELECT m.* FROM cognitive_memory_fts f
   JOIN cognitive_memories m ON f.memory_key=(m.memory_id || ':' || m.version)
   WHERE cognitive_memory_fts MATCH ? AND f.tenant_id=?
     AND m.superseded_at IS NULL
     AND (? IS NULL OR m.workspace_id=? OR m.workspace_id IS NULL)
   ORDER BY bm25(cognitive_memory_fts) LIMIT ?"""


def _store(tmp_path, rows: int):
    tmp_path = pathlib.Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = SQLiteMemoryStore(str(tmp_path / "scaling.db"))
    if not store.fts_available:
        pytest.skip("FTS5 is not available in this SQLite build")
    for i in range(rows):
        store.put(
            CognitiveMemory(
                memory_id=f"mem_{i:06d}",
                tenant_id="tenant_00",
                workspace_id="ws_00",
                content=f"quy trinh duyet don hang so {i} approval queue item",
                memory_type=MemoryType.EPISODIC,
                source_event_ids=[f"evt_{i:06d}"],
            ),
            commit=False,
        )
    store.conn.commit()
    return store


def _plan(conn: sqlite3.Connection) -> list[str]:
    return [r[-1] for r in conn.execute(
        "EXPLAIN QUERY PLAN " + FTS_JOIN,
        ('"quy"', "tenant_00", None, None, 250))]


def test_fts_join_searches_the_memory_table_instead_of_scanning_it(tmp_path):
    """The regression. A SCAN here is the cross product that cost 23 seconds."""
    store = _store(tmp_path, 400)
    try:
        plan = _plan(store.conn)
    finally:
        store.conn.close()

    scans = [step for step in plan
             if step.startswith("SCAN m") or step == "SCAN cognitive_memories"]
    assert not scans, (
        "candidate_pool joins the FTS index to cognitive_memories once per\n"
        "match. The planner chose a full scan, which makes recall cost\n"
        "O(matches x table size):\n  " + "\n  ".join(plan))

    assert any("SEARCH m" in step for step in plan), (
        "expected an indexed lookup into cognitive_memories:\n  "
        + "\n  ".join(plan))


def test_recall_cost_does_not_track_corpus_size(tmp_path):
    """Ten times the corpus must not cost ten times the query.

    Deliberately loose. The defect was a factor of 1,234, so an assertion at
    4x separates a real regression from an unlucky machine.
    """
    small = _store(tmp_path / "small", 200)
    large = _store(tmp_path / "large", 2000)
    try:
        for store in (small, large):  # warm both, exclude first-call effects
            store.candidate_pool("tenant_00", "quy trinh duyet", limit=50)

        def timed(store) -> float:
            best = float("inf")
            for _ in range(3):
                t = time.perf_counter()
                store.candidate_pool("tenant_00", "quy trinh duyet", limit=50)
                best = min(best, time.perf_counter() - t)
            return best

        small_s, large_s = timed(small), timed(large)
    finally:
        small.conn.close()
        large.conn.close()

    assert large_s < max(small_s * 4, 0.05), (
        f"10x the corpus cost {large_s / small_s:.1f}x the query "
        f"({small_s * 1000:.1f} ms at 200 rows, {large_s * 1000:.1f} ms at "
        f"2,000 rows). candidate_pool is scaling with the table, not the "
        f"result set.")


def test_the_expression_index_exists_on_a_fresh_database(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "fresh.db"))
    try:
        names = {r[0] for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        store.conn.close()
    assert "idx_memory_fts_key" in names, sorted(names)


def test_the_index_is_added_to_a_database_that_predates_it(tmp_path):
    """An existing deployment must get the index without a manual step."""
    store = _store(tmp_path, 50)
    db = store.path
    store.conn.execute("DROP INDEX IF EXISTS idx_memory_fts_key")
    store.conn.commit()
    store.conn.close()

    reopened = SQLiteMemoryStore(db)
    try:
        names = {r[0] for r in reopened.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        plan = _plan(reopened.conn)
    finally:
        reopened.conn.close()

    assert "idx_memory_fts_key" in names, sorted(names)
    assert not [s for s in plan if s.startswith("SCAN m")], plan
