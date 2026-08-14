"""Code that runs, looks right, and checks nothing.

Four defects found on 2026-08-14 by adversarial review of the erasure design,
then reproduced here before being fixed. All four share one shape: a mechanism
that reports success having performed zero work, so the failure is invisible
from the outside and the tests stay green.

Two of them were in `forgetting.py`, written the same day:

1.  `_probes()` falls back to the longest token matching `\\w{6,}`. Vietnamese
    is monosyllabic, so almost no token reaches six characters. Measured:

        "Anh sống ở Đà Nẵng"          -> no probe
        "Lương tháng 8 là 25 triệu"   -> no probe
        "Giá bán là 6.500.000 VND"    -> no probe
        "He lives in Hanoi"           -> no probe

    No probe means the verification loop never runs, `residue` stays empty and
    `verified_clean` is set True — a clean bill of health with nothing checked.
    The two examples in the original test suite, `88888888` and `0912345678`,
    happen to be the only shapes that *do* produce a probe. Those tests passed
    because of the examples chosen, not because the code worked.

2.  `verify()` matches with SQL `LIKE`, which folds case for ASCII only, while
    `find_by_subject()` matches with Python `.lower()`, which folds Unicode
    correctly. Measured on SQLite:

        LIKE '%đà nẵng%'  ->  ['đà nẵng']            (misses 'Đà Nẵng')
        LIKE '%hanoi%'    ->  ['Hanoi', 'hanoi']     (ASCII folds fine)

    So the deleter finds case variants the verifier cannot, which is precisely
    the asymmetry that yields a false all-clear.

And two in older code:

3.  `_already_consolidated()` reads `SELECT metadata FROM cognitive_memories`.
    The column is `metadata_json`. The resulting OperationalError is caught and
    turned into an empty set, so every consolidation pass believes nothing has
    been consolidated and merges the same cluster again — which is why one
    morning's run produced 45 consolidated memories.

4.  `MemoryStore.supersede()` sets `observed_at` to now. `observed_at` is the
    staleness clock, and staleness is the only memory-specific mechanism with a
    measured win (2/3 against plain RAG's 0/3). A forget/restore round trip
    silently resets the age of every memory it touches.

The rule these enforce: **zero checks performed must never read as clean.**
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

VIETNAMESE = [
    "Anh sống ở Đà Nẵng",
    "Lương tháng 8 là 25 triệu",
    "Giá bán là 6.500.000 VND",
    "He lives in Hanoi",
]


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "silent.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return getattr(stored, "memory_id", None) or ""


# --------------------------------------------------------------------------
# 1. a verification that cannot run must not report success
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", VIETNAMESE)
def test_ordinary_vietnamese_produces_a_usable_probe(text):
    """A store whose content is mostly Vietnamese cannot have a verifier that
    only works on long ASCII tokens."""
    from bio_agent_os.cognitive.forgetting import _probes

    assert _probes([text], None), f"không sinh được probe nào cho: {text!r}"


def test_forget_refuses_to_report_clean_when_it_checked_nothing(os_):
    """The honesty rule, in its most direct form.

    If no probe can be derived, the operation has verified nothing. Reporting
    `verified_clean = True` there is a lie of omission that nobody goes back to
    catch — the same failure the module's docstring exists to prevent.
    """
    from bio_agent_os.cognitive.forgetting import forget_derived

    memory_id = _remember(os_, "Anh sống ở Đà Nẵng")
    report = forget_derived(os_, memory_id=memory_id)

    assert report.memories_deleted == 1
    assert report.checks_run > 0, (
        "xoá xong mà không chạy phép kiểm nào, vẫn báo kết quả"
    )


def test_a_report_that_ran_no_checks_is_not_clean():
    """Direct on the report object, so the rule holds even if `forget` changes."""
    from bio_agent_os.cognitive.forgetting import ForgetReport

    report = ForgetReport(memories_deleted=1, checks_run=0)
    assert report.verified_clean is False, (
        "không kiểm gì mà vẫn 'sạch' — đây chính là lỗi đang vá"
    )


# --------------------------------------------------------------------------
# 2. the deleter and the verifier must agree on what "matches" means
# --------------------------------------------------------------------------

def test_verification_sees_the_case_variants_the_deleter_deletes(os_):
    """`find_by_subject` folds Unicode case, `verify` folded only ASCII. One of
    them finding more than the other is how a leak survives a clean report."""
    from bio_agent_os.cognitive.forgetting import find_by_subject, verify

    _remember(os_, "Khách hàng Đà Nẵng ký hợp đồng.")
    _remember(os_, "Chi nhánh đà nẵng đã nhận hàng.")
    conn = os_.memories.conn

    deleted = find_by_subject(conn, "Đà Nẵng")
    found = verify(conn, "Đà Nẵng")

    assert len(deleted) == 2, "bộ xoá phải thấy cả hai biến thể hoa/thường"
    assert len(found) >= 2, (
        f"bộ kiểm chỉ thấy {len(found)} chỗ trong khi bộ xoá thấy {len(deleted)} — "
        "lệch nhau là cách một bản sao sống sót qua báo cáo sạch"
    )


def test_forget_by_subject_verifies_what_it_removed_in_vietnamese(os_):
    """End to end, in the language this store actually holds."""
    from bio_agent_os.cognitive.forgetting import forget_derived

    _remember(os_, "Khách hàng Đà Nẵng ký hợp đồng ngày 12/03/2026.")
    _remember(os_, "chi nhánh đà nẵng đã nhận hàng.")
    _remember(os_, "Máy chủ OCR chạy ở cổng 8000.")

    report = forget_derived(os_, subject="Đà Nẵng", needle="Đà Nẵng")
    assert report.memories_deleted == 2
    assert report.checks_run > 0
    assert report.verified_clean is True, f"còn sót: {report.residue}"

    remaining = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    assert remaining == 1


# --------------------------------------------------------------------------
# 3. a swallowed OperationalError that turns a lookup into "nothing found"
# --------------------------------------------------------------------------

def test_already_consolidated_reads_the_column_that_exists(os_):
    """Naming a column that does not exist raises OperationalError, which is
    caught and returned as "nothing consolidated yet" — so every pass merges
    the same cluster again."""
    from bio_agent_os.cognitive.consolidation import (
        CONSOLIDATED_MARKER,
        _already_consolidated,
    )
    from bio_agent_os.cognitive.models import CognitiveMemory

    first = _remember(os_, "Doanh thu tháng 6 là 2,4 tỷ.")
    second = _remember(os_, "Doanh thu tháng 7 là 3,1 tỷ.")

    merged = CognitiveMemory(
        tenant_id="t1", workspace_id="w1", memory_type=MemoryType.SEMANTIC,
        content="Tổng hợp doanh thu quý.", source_event_ids=[], confidence=0.7,
        metadata={CONSOLIDATED_MARKER: [first, second]},
    )
    os_.memories.put(merged, commit=True)

    used = _already_consolidated(os_.memories.conn)
    assert used == {first, second}, (
        f"không nhận ra ký ức đã hợp nhất (thấy {used}) — mỗi lần chạy lại "
        "sẽ hợp nhất lại đúng cụm đó"
    )


def test_the_consolidation_lookup_does_not_hide_a_schema_error(os_):
    """Guards the shape of the bug rather than this one instance: a query
    naming a column the table does not have must fail loudly in a test, not be
    swallowed into an empty result."""
    conn = os_.memories.conn
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cognitive_memories)")}
    assert "metadata_json" in columns
    assert "metadata" not in columns, (
        "nếu cột `metadata` tồn tại thì bài test này không còn ý nghĩa"
    )
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT metadata FROM cognitive_memories LIMIT 1").fetchall()


# --------------------------------------------------------------------------
# 4. the staleness clock must not be reset by a row rewrite
# --------------------------------------------------------------------------

def test_supersede_keeps_the_original_observation_time(os_):
    """`observed_at` records when the claim was learned, not when a row was
    rewritten. Resetting it disables the one mechanism with a measured win."""
    from bio_agent_os.cognitive import staleness

    memory_id = _remember(os_, "Giá gói dịch vụ là 6.500.000 VND")
    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET observed_at=? WHERE memory_id=?",
        ("2025-01-01T00:00:00+00:00", memory_id),
    )
    os_.memories.conn.commit()

    before = os_.memories.latest(memory_id, "t1")
    aged = staleness.age_days(before)
    assert aged and aged > 300, "dữ liệu dựng sẵn cho bài test không đúng"

    after = os_.memories.supersede(memory_id, "t1", confidence=0.6)
    assert staleness.age_days(after) == pytest.approx(aged, abs=1.0), (
        "supersede() đã đặt lại đồng hồ tuổi; ký ức 590 ngày thành 0 ngày "
        "và staleness không bao giờ kích hoạt lại được"
    )


def test_supersede_still_accepts_an_explicit_new_observation(os_):
    """A genuinely new observation *should* move the clock. Freezing it
    unconditionally would be the opposite bug."""
    memory_id = _remember(os_, "Giá gói dịch vụ là 6.500.000 VND")
    fresh = "2026-08-14T10:00:00+00:00"
    after = os_.memories.supersede(memory_id, "t1", observed_at=fresh)
    assert after.observed_at == fresh
