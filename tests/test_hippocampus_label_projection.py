"""The write-path half of the join, and the boundaries it must not cross.

Section 2.3 of the join plan sets three hard boundaries. Two of them are
testable here and are tested here, because a boundary nobody checks is a
comment. The third — no customer data to a cloud model — is a property of the
upgrader, which is not on this path at all.

Phase 1 is why these exist: the model-driven labeller measured p50 = 20.4 s per
event, so anything model-shaped on the write path is a write that can take
twenty seconds.
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.hippocampus_label import (
    CHEAP,
    HippocampusLabelBuilder,
    cheap_label,
    ensure_schema,
    pending_count,
)
from bio_agent_os.cognitive.projection_capability import capability, is_supported
from bio_agent_os.cognitive.projection_registry import (
    ProjectionType,
    default_projection_types,
    dependencies_of,
    detect_cycles,
)

HIPPO = ProjectionType.HIPPOCAMPUS_LABEL.value


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "join.db"


# --------------------------------------------------------------------------
# boundary 1: the write path must not be able to reach a model
# --------------------------------------------------------------------------

def test_the_write_path_module_imports_no_model():
    """`cognitive/hippocampus_label.py` must keep working with no engine, no
    Qdrant and no `background_jobs`. Checked on the source rather than by
    importing, because an import that happens to succeed today proves nothing
    about what it dragged in."""
    source = (
        Path(__file__).resolve().parents[1]
        / "bio_agent_os" / "cognitive" / "hippocampus_label.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("background_jobs", "llm_engine", "LLMEngine", "qdrant", "openai"):
        assert f"import {forbidden}" not in source
        assert f"from bio_agent_os.{forbidden}" not in source
        assert f"from .{forbidden}" not in source


def test_the_cheap_label_is_deterministic_and_fast():
    """A builder that is not deterministic makes replay unable to reproduce
    what it replayed. A builder that is not fast is a write transaction held
    open for as long as it takes."""
    text = "Hợp đồng ký ngày 12/03/2026, giá trị 84.000.000 đồng."
    first = cheap_label(text)
    for _ in range(200):
        assert cheap_label(text) == first

    started = time.perf_counter()
    for _ in range(1_000):
        cheap_label(text)
    per_call_ms = (time.perf_counter() - started) * 1000 / 1_000
    assert per_call_ms < 1.0, f"{per_call_ms:.3f} ms per label is not a cheap label"


def test_junk_and_fact_land_on_opposite_sides():
    assert cheap_label("oke em").is_junk_or_transient is True
    fact = cheap_label("Doanh thu tháng này 9.960.000 đồng")
    assert fact.is_junk_or_transient is False
    assert fact.importance_score > cheap_label("oke em").importance_score


def test_the_two_cheap_label_paths_are_one_implementation():
    """`Hippocampus._cheap_label` and the builder must agree on what counts as
    junk. Two copies would drift, and a memory would then be labelled one way at
    write time and another at consolidation for no visible reason."""
    from bio_agent_os.background_jobs.hippocampus import Hippocampus

    class _P:
        name = "t"

        def __init__(self):
            self.storage_dir = tempfile.mkdtemp(prefix="cheap-label-parity-")

    hippo = Hippocampus(engine=None, l1=None, persona=_P())
    for text in ["oke em", "Hợp đồng 84.000.000 đồng", "cảm ơn", "SQLite WAL"]:
        assert hippo._cheap_label(text) == cheap_label(text).as_dict()


# --------------------------------------------------------------------------
# boundary 2: a label failure must never block a memory
# --------------------------------------------------------------------------

def test_the_label_projection_depends_on_nothing():
    """Declaring a dependency on cognitive_memory would let a label failure
    block a memory. Enrichment, never a prerequisite."""
    assert dependencies_of(ProjectionType.HIPPOCAMPUS_LABEL) == ()
    assert detect_cycles() == []


def test_labels_are_opt_in_and_not_enqueued_by_default():
    """An ordinary event owes only its memory. Turning this on for everything
    without asking would change queue lag for every existing deployment."""
    assert HIPPO not in default_projection_types()
    assert default_projection_types() == (ProjectionType.COGNITIVE_MEMORY.value,)


def test_the_capability_does_not_claim_a_shadow_comparison():
    """There has never been a legacy hippocampus label, so there is nothing to
    compare a projection against. Reporting `shadow_supported=True` would
    fabricate a comparison that cannot happen."""
    cap = capability(HIPPO)
    assert is_supported(HIPPO)
    assert cap.shadow_supported is False
    assert cap.target_store == "hippocampus_labels"


# --------------------------------------------------------------------------
# the builder itself
# --------------------------------------------------------------------------

def _event_and_job(content: str, key: str = "k1"):
    class _E:
        event_id = "e1"
        tenant_id = "t1"
        workspace_id = "w1"
        payload = {"content": content}
        observed_at = "2026-08-10T00:00:00+00:00"

    class _J:
        projection_type = HIPPO
        projection_version = 1

    job = _J()
    job.key = key
    return _E(), job


def test_builder_writes_a_pending_row_and_does_not_commit(db: Path):
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    conn.commit()

    event, job = _event_and_job("Hợp đồng 84.000.000 đồng")
    result = HippocampusLabelBuilder().build(event, job, conn)
    assert result.outcome == "built"
    assert result.target_id == "k1"

    # Still inside the transaction the worker owns.
    assert pending_count(conn) == 1
    conn.rollback()
    assert pending_count(conn) == 0, (
        "the builder committed on its own, which splits the ledger row and the "
        "projection into two transactions"
    )


def test_an_empty_event_is_skipped_not_failed(db: Path):
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    event, job = _event_and_job("   ")
    result = HippocampusLabelBuilder().build(event, job, conn)
    assert result.outcome == "skipped"
    assert pending_count(conn) == 0


def test_replay_does_not_clobber_a_label_the_model_already_upgraded(db: Path):
    """Replay must be able to rebuild the row. It must not undo the expensive
    work by writing the cheap label back over a real one — that would be a
    twenty-second model call thrown away by a routine repair."""
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    event, job = _event_and_job("Hợp đồng 84.000.000 đồng")
    HippocampusLabelBuilder().build(event, job, conn)
    conn.commit()

    conn.execute(
        "UPDATE hippocampus_labels SET topic=?, importance_score=?, "
        "label_source='model', label_pending=0 WHERE projection_key='k1'",
        ("Contract with Binh Minh Company", 9),
    )
    conn.commit()

    HippocampusLabelBuilder().build(event, job, conn)   # replay
    conn.commit()

    row = conn.execute(
        "SELECT topic, importance_score, label_source, label_pending "
        "FROM hippocampus_labels WHERE projection_key='k1'"
    ).fetchone()
    assert row == ("Contract with Binh Minh Company", 9, "model", 0)


def test_replay_of_a_cheap_row_is_idempotent(db: Path):
    conn = sqlite3.connect(str(db))
    ensure_schema(conn)
    event, job = _event_and_job("Hợp đồng 84.000.000 đồng")
    for _ in range(3):
        HippocampusLabelBuilder().build(event, job, conn)
    conn.commit()
    rows = conn.execute("SELECT COUNT(*) FROM hippocampus_labels").fetchone()[0]
    assert rows == 1, "replay created duplicate labels"


def test_pending_count_treats_a_missing_table_as_absent_not_broken(tmp_path: Path):
    """A database that has not enabled this capability has no such table. That
    is a missing capability, not damage — the same distinction the projection
    capability registry exists to make."""
    conn = sqlite3.connect(str(tmp_path / "bare.db"))
    assert pending_count(conn) == 0


# --------------------------------------------------------------------------
# nothing reads these labels yet
# --------------------------------------------------------------------------

def test_labels_influence_ranking_only_behind_a_switch():
    """Was `test_retrieval_does_not_read_the_label_table`.

    That test held the boundary until Phase 3 answered whether labels help. It
    ran on 2026-08-14 and the answer was **no** — 148/194 with the labels on and
    148/194 with them off, not one question different. So the labels are wired
    into ranking but switched off, and this test now guards the switch instead
    of the boundary: reading the table is allowed, reading it *by default* is
    not.

    `forgetting.py` also names the table, and must: a delete that leaves the
    label behind leaves a description of what was deleted.
    """
    from bio_agent_os.cognitive.retrieval import USE_HIPPOCAMPUS_LABELS

    assert USE_HIPPOCAMPUS_LABELS is False, (
        "labels are on by default, and Phase 3 measured no benefit from them"
    )

    root = Path(__file__).resolve().parents[1] / "bio_agent_os" / "cognitive"
    allowed = {"hippocampus_label.py", "reconciliation_worker.py",
               "diagnostics.py", "projection_capability.py",
               "projection_registry.py",
               "retrieval.py",      # Phase 3, behind USE_HIPPOCAMPUS_LABELS
               "forgetting.py",     # must delete labels along with the memory
               # Names every persistent store, by design. A closed-world
               # privacy model that skipped a table to keep this guard quiet
               # would be the exact hole it exists to close — and naming a
               # table in a declaration is not reading it.
               "privacy_registry.py"}
    readers = [p.name for p in root.glob("*.py")
               if p.name not in allowed
               and "hippocampus_labels" in p.read_text(encoding="utf-8")]
    assert readers == [], f"unexpected readers of the label table: {readers}"

    # The exemption above is for a declaration, so check it stays one: a
    # module allowed to *name* the table must not start querying it.
    from pathlib import Path as _Path

    declaration = (root / "privacy_registry.py").read_text(encoding="utf-8")
    for querying in ("SELECT", "select(", "execute("):
        assert querying not in declaration or "hippocampus_labels" not in \
            declaration.split(querying, 1)[1][:200], (
                "privacy_registry.py bắt đầu truy vấn bảng label — nó chỉ "
                "được phép khai báo")


def test_writing_a_memory_still_works_with_the_new_type_registered(db: Path):
    """The whole point of enrichment: everything that worked before must still
    work, unchanged, whether or not a label was ever produced."""
    os_ = MemoryOS(db)
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Hợp đồng 84.000.000 đồng", workspace_id="w1")
    assert event is not None
    os_.close()
