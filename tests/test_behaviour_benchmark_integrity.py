"""The forgetting cases passed without ever deleting anything.

`scripts/behaviour_benchmark.py` filtered the data a `[XOÁ]` request names out
of the ingest list, then asked the question:

    kept = [(ws, t) for ws, t in facts if not is_deleted(t)]

So the store never held "88888888" at all. The query naturally returned nothing
containing it, `must_not` passed, and `forget-001/002/003` reported success
having never called `forgetting.forget()`. Three cases whose whole purpose is
to prove deletion works, proving nothing.

It is the same shape as every other defect found this week: a mechanism that
reports success having performed zero work. It is worse than the others because
the number it produced was published.

What a deletion case has to do, in order:

    1. ingest the fact
    2. confirm it can be retrieved      <- the step whose absence hid the bug
    3. call forget()
    4. confirm it can no longer be retrieved

Step 2 is the one that matters. Without it, "not found" after a delete is
indistinguishable from "never there", and that ambiguity is exactly what the
old code turned into a passing score.

These tests check the harness, not the memory. A benchmark is a measuring
instrument, and an instrument nobody calibrates reports whatever it likes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

from behaviour_benchmark import matches_delete_request  # noqa: E402

FIXTURES = _REPO / "tests" / "fixtures" / "memory_behaviours.jsonl"
SCRIPT = _REPO / "scripts" / "behaviour_benchmark.py"


def _cases() -> list[dict]:
    return [json.loads(line) for line in
            FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip()]


def _forgetting_cases() -> list[dict]:
    return [c for c in _cases() if c.get("group") == "forgetting"]


def test_the_fixture_still_has_forgetting_cases():
    cases = _forgetting_cases()
    assert len(cases) >= 3, "không còn ca nào về việc xoá để mà kiểm"
    for case in cases:
        assert any(str(line).strip().startswith("[XOÁ]")
                   for line in case.get("setup", [])), case["id"]


def test_the_harness_no_longer_skips_ingesting_what_it_will_delete():
    """Guards the exact line that caused it.

    Checked on the source rather than by running, because the failure was
    invisible at runtime: the benchmark produced a plausible score either way.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "if not is_deleted(t)" not in source, (
        "harness vẫn lọc bỏ dữ liệu cần xoá trước khi nạp — ca forgetting sẽ "
        "lại xanh vì dữ liệu chưa từng tồn tại"
    )
    assert "forget_derived" in source or "forget(" in source, (
        "harness không gọi hàm xoá nào — ca forgetting không kiểm việc xoá"
    )


def test_the_harness_confirms_the_fact_was_there_before_deleting_it():
    """The missing step. Without it "not found" proves nothing."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "retrievable_before" in source, (
        "harness không xác nhận dữ liệu truy hồi được TRƯỚC khi xoá, nên "
        "'không tìm thấy' sau khi xoá không chứng minh điều gì"
    )


# --------------------------------------------------------------------------
# the deletion path itself, end to end, on a real store
# --------------------------------------------------------------------------

class FakeEmbedder:
    """Topic-aware, because one of these cases is a topic-scoped deletion.

    A bag-of-words embedder over a fixed vocabulary cannot place "sức khoẻ"
    near "tiền sử bệnh tim" — the phrase is not in its vocabulary, so it
    produces a null vector and `forget-002` fails here while passing against
    the real multilingual model (3/3, measured 15/08/2026). A fixture too weak
    to express the thing under test fails the product for the fixture's
    shortcoming.
    """

    model = "fake-topic"
    TOPICS = {
        "sức khoẻ": ("bệnh", "tim", "huyết áp", "thuốc", "sức khoẻ", "khám"),
        "liên hệ": ("điện thoại", "số", "0912345678", "email", "liên hệ"),
        "bảo mật": ("mật khẩu", "wifi", "88888888", "khẩu", "mật"),
        "hạ tầng": ("máy chủ", "cổng", "ocr", "server"),
    }

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [float(sum(1 for w in words if w in low))
                  for words in self.TOPICS.values()]
        return vector + [0.0] if any(vector) else [0.0] * len(self.TOPICS) + [1.0]


def _params():
    """Cases needing a capability the product does not have are marked xfail,
    not quietly skipped and not made to pass.

    `forget-002` asks for "all *health* information about Mr Hùng" — a topic
    plus a subject. The memory "Anh Hùng có tiền sử bệnh tim" never says "sức
    khoẻ", so lexical overlap reaches 1 of 3 words against a 0.5 bar. Lowering
    the bar would turn a missing capability into a passing score, which is the
    thing this file exists to stop. `strict=True`: when topic-scoped deletion
    lands, this test failing to fail is itself a failure, so nobody has to
    remember to come back and re-enable it.
    """
    out = []
    for case in _forgetting_cases():
        marks = []
        if case.get("requires_capability"):
            marks.append(pytest.mark.xfail(
                strict=True,
                reason=f"chưa có năng lực {case['requires_capability']}: "
                       f"{case.get('gap_note', '')}"))
        out.append(pytest.param(case, id=case["id"], marks=marks))
    return out


@pytest.mark.parametrize("case", _params())
def test_each_forgetting_case_really_deletes_something(tmp_path, case):
    """Runs the four steps against a real store, per case.

    This is what the harness should have been doing. If any case can reach the
    end without step 2 passing, the case is untestable as written and should be
    fixed rather than counted.
    """
    from bio_agent_os.cognitive.facade import MemoryOS
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.models import AccessContext, MemoryType
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    memory_os = MemoryOS(tmp_path / f"{case['id']}.db", embedder=FakeEmbedder())
    ctx = AccessContext(tenant_id="bench", workspace_id="w")

    facts = [str(line) for line in case["setup"]
             if not str(line).strip().startswith("[XOÁ]")]
    requests = [str(line).replace("[XOÁ]", "").strip()
                for line in case["setup"]
                if str(line).strip().startswith("[XOÁ]")]
    assert facts and requests, case["id"]

    # 1. ingest
    for text in facts:
        event = memory_os.observe(tenant_id="bench", actor="a", source="test",
                                  content=text, workspace_id="w")
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=text, confidence=0.85)
    backfill_embeddings(memory_os.memories.conn, memory_os.retrieval.embedder)

    # 2. confirm it is really there — the step whose absence hid the bug
    secret = str(case["must_not"][0])
    stored = memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{secret}%",),
    ).fetchone()[0]
    assert stored >= 1, (
        f"{case['id']}: {secret!r} chưa từng được nạp — ca này không kiểm "
        f"việc xoá, nó chỉ kiểm một kho rỗng"
    )

    # 3. delete, through the same scope resolver the harness uses. A test that
    #    brings its own better matcher proves the product works under
    #    conditions the product never sees.
    reports = [forget_scoped(memory_os, request, actor="test")
               for request in requests]

    assert any(r.deleted_claims for r in reports), (
        f"{case['id']}: không xoá được ký ức nào cho yêu cầu {requests}"
    )
    for report in reports:
        if report.deleted_claims:
            assert report.checks_run > 0, "xoá xong mà không chạy phép kiểm nào"
            assert report.succeeded is True

    # 4. confirm it is gone
    left = memory_os.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        (f"%{secret}%",),
    ).fetchone()[0]
    assert left == 0, f"{case['id']}: {secret!r} vẫn còn trong kho"

    hits = memory_os.recall(query=str(case["query"]), context=ctx, limit=10) or []
    leaked = [h.memory.content for h in hits if secret in h.memory.content]
    assert leaked == [], f"{case['id']}: truy xuất vẫn trả lại: {leaked}"
    memory_os.close()
