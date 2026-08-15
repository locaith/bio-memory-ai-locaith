"""A deletion must not skip what it could not judge.

Six failures in the lifetime run, all `forget_primary_survives`. Reproduced at
tick 440 under the benchmark's real conditions:

    kho lúc xoá: 434 ký ức, 250 có vector (184 CHƯA có)
    preview khớp 1 ký ức
    còn lại    : "Nhắc lại, số điện thoại của Hoàng Yến là 0977123456."  x2

`preview` scopes by subject, then filters by topic similarity. A candidate with
no vector yet cannot be scored, and it was dropped — so "I cannot tell whether
this is about the topic" was rendered as "this is not about the topic". The
same conflation `cosine` makes when it returns 0.0 for mismatched dimensions,
and the same one the constitution names `NOT_EVALUABLE` versus
`NOT_APPLICABLE`.

The benchmark backfills embeddings only at checkpoints, which is not an
artefact — production embeds asynchronously too, so a deletion arriving before
the backfill under-deletes. Silently, and only for the newest memories, which
are the ones a person is most likely to want gone.

`forget_scoped` was honest about it: `status=partial`, `succeeded=False`, and
the two survivors listed as residue. RULE_002 held. What failed is that the
deletion could not do its job, not that it lied about it.

INDEPENDENT READ-BACK. Verification here opens its own SQLite connection to the
file and reads the table directly, rather than asking the repository that just
performed the delete. If the deleter and its verifier share a filter bug they
will agree the record is gone — a common-mode failure that no amount of
checking through one path can catch.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

FIXTURE = Path(__file__).parent / "fixtures" / "forget_primary_survives.json"


def _cases() -> list[dict]:
    """The six cases exactly as they failed, frozen before any fix.

    Regenerating cases after a patch and declaring victory credits the fix for
    a world that changed underneath it.
    """
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class SlotEmbedder:
    """Offline and deterministic, with one axis per attribute."""

    model = "slot-fake"
    SLOTS = {
        "phone": ("điện thoại", "số điện", "0977123456", "0912345678"),
        "job": ("chức", "giữ chức", "trưởng nhóm", "giám đốc"),
        "city": ("sống", "cần thơ", "hà nội", "đà nẵng"),
        "project": ("dự án", "phụ trách", "trang thương mại"),
    }

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [float(sum(1 for w in words if w in low))
                  for words in self.SLOTS.values()]
        return vector + [0.0] if any(vector) else [0.0] * len(self.SLOTS) + [1.0]


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "primary.db", embedder=SlotEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


def _read_back_independently(db_path: Path, needle: str) -> list[str]:
    """A second connection, straight to the file, bypassing the repository.

    The deleter reached the row through `MemoryStore`. If verification asks the
    same object whether the row is gone, a shared filter bug makes both agree
    that it is.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT content FROM cognitive_memories WHERE content LIKE ?",
            (f"%{needle}%",)).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------
# the frozen cases
# --------------------------------------------------------------------------

def test_the_six_cases_are_on_disk_and_unchanged():
    cases = _cases()
    assert len(cases) == 6, f"đóng băng {len(cases)} ca, không phải 6"
    for case in cases:
        assert case["forbidden"], case
        assert case["requests"], case


@pytest.mark.parametrize("case", _cases(),
                         ids=[f"{c['slot']}@{c['checkpoint']}" for c in _cases()])
def test_a_deletion_reaches_memories_that_have_no_vector_yet(tmp_path, case):
    """The defect, reproduced per case: repeats ingested after the last
    backfill were invisible to topic scoping and survived."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    db = tmp_path / f"{case['slot'].replace('/', '_')}.db"
    memory_os = MemoryOS(db, embedder=SlotEmbedder())
    subject, value = case["subject"], case["forbidden"]

    first = _remember(memory_os, case["sentence"])
    backfill_embeddings(memory_os.memories.conn, memory_os.retrieval.embedder)
    # Arriving after the backfill — exactly the benchmark's shape.
    _remember(memory_os, f"Nhắc lại, {case['sentence'][0].lower()}{case['sentence'][1:]}")
    _remember(memory_os, f"Nhắc lại, {case['sentence'][0].lower()}{case['sentence'][1:]}")

    before = _read_back_independently(db, value)
    assert len(before) == 3, before

    result = forget_scoped(memory_os, case["requests"][0], actor="test")
    memory_os.close()

    after = _read_back_independently(db, value)
    assert after == [], (
        f"{case['slot']}: {len(after)} ký ức của chính chủ thể vẫn còn sau khi "
        f"xoá — {after[:2]}")
    assert result.succeeded is True, result.as_dict()
    assert result.verified_clean is True


@pytest.mark.parametrize("case", _cases(),
                         ids=[f"{c['slot']}@{c['checkpoint']}" for c in _cases()])
def test_success_is_refused_while_the_primary_row_survives(tmp_path, case):
    """The hard invariant: a live primary row makes the whole thing a failure,
    however clean the derived stores are."""
    from bio_agent_os.cognitive.forget_scope import ForgetStatus, forget_scoped

    db = tmp_path / f"guard_{case['slot'].replace('/', '_')}.db"
    memory_os = MemoryOS(db, embedder=SlotEmbedder())
    _remember(memory_os, case["sentence"])
    _remember(memory_os, f"Nhắc lại, {case['sentence'][0].lower()}{case['sentence'][1:]}")

    result = forget_scoped(memory_os, case["requests"][0], actor="test")
    survivors = _read_back_independently(db, case["forbidden"])
    memory_os.close()

    if survivors:
        assert result.succeeded is False, (
            "dòng chính còn sống mà vẫn báo thành công")
        assert result.status is not ForgetStatus.DELETED
    else:
        assert result.succeeded is True


def test_the_verifier_does_not_ask_the_deleter(tmp_path):
    """Common-mode failure. If both halves share one filter, both agree.

    Written against the source: the check must not be the repository the
    deletion went through.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    db = tmp_path / "independent.db"
    memory_os = MemoryOS(db, embedder=SlotEmbedder())
    _remember(memory_os, "Số điện thoại của Hoàng Yến là 0977123456.")
    forget_scoped(memory_os, "Hãy quên số điện thoại của Hoàng Yến.",
                  actor="test")
    memory_os.close()

    # A path the deletion never touched: a fresh read-only connection.
    assert _read_back_independently(db, "0977123456") == []


def test_a_candidate_without_a_vector_is_not_treated_as_off_topic(tmp_path):
    """The root cause, stated on its own.

    "I cannot judge this" and "this is not about the topic" must not be the
    same outcome — the distinction the constitution calls NOT_EVALUABLE versus
    NOT_APPLICABLE.
    """
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    memory_os = MemoryOS(tmp_path / "novec.db", embedder=SlotEmbedder())
    _remember(memory_os, "Số điện thoại của Hoàng Yến là 0977123456.")
    _remember(memory_os, "Nhắc lại, số điện thoại của Hoàng Yến là 0977123456.")
    # No backfill at all: nothing has a vector. The table itself is created
    # lazily, so "absent" and "empty" mean the same thing here.
    try:
        embedded = memory_os.memories.conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        embedded = 0
    assert embedded == 0

    scope = resolve("Hãy quên số điện thoại của Hoàng Yến.")
    matched = preview(memory_os, scope)
    memory_os.close()

    assert len(matched) == 2, (
        f"chỉ khớp {len(matched)} — ký ức chưa có vector bị coi là lạc chủ đề")
