"""What does "quên chuyện đó đi" actually cover?

Measured on the first real lifetime run, 15/08/2026:

    yêu cầu xoá đã THỰC SỰ xoá được gì: 3/18
    forgotten (truy xuất): 0.000 ở mọi mốc
    18/18 câu vẫn thấy giá trị đã xoá trong kết quả

Fifteen of eighteen deletion requests matched nothing at all. The cause is not
a threshold: `forget_derived(subject=...)` matches a literal substring, and
"Hãy quên thông tin về city của Trần An" is not a substring of "Trần An sống ở
Hà Nội". The request describes a *scope*; the deleter was looking for a string.

So scope becomes a thing with a shape:

    memory_id           this exact memory
    subject             everything about a person
    subject + topic     one aspect of a person
    topic               everything on a subject matter
    time_range          everything from a period

and the request is resolved into one before anything is deleted.

**No match is not success.** The single most important rule here, and the one
that keeps this honest: a request that matched nothing must report
`NO_MATCH`, not a clean bill of health. `verified_clean` answers "is the
content gone"; `status` answers "did the deletion do anything". Collapsing them
is how "we found nothing to delete" comes to read as "we deleted it" — which
is exactly the shape of the harness bug that made this project's forgetting
scores meaningless for a day.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")


class TopicEmbedder:
    """Offline, deterministic, and topic-aware enough to tell health from work.

    The real store uses a multilingual sentence model. What is under test is
    whether scope resolution *uses* semantic similarity, not how good the
    vectors are.
    """

    model = "topic-fake"
    TOPICS = {
        "sức khoẻ": ("bệnh", "tim", "huyết áp", "khám", "thuốc", "sức khoẻ", "ốm"),
        "công việc": ("công ty", "chức", "làm việc", "dự án", "lương", "công việc"),
        "nơi ở": ("sống", "địa chỉ", "nhà", "quê", "chuyển đến", "nơi ở"),
        "liên hệ": ("điện thoại", "email", "số", "liên hệ", "gọi"),
    }

    def embed(self, text: str) -> list[float]:
        low = str(text).lower()
        vector = [
            float(sum(1 for w in words if w in low))
            for words in self.TOPICS.values()
        ]
        return vector + [0.0] if any(vector) else [0.0] * len(self.TOPICS) + [1.0]


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "scope.db", embedder=TopicEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, at: str | None = None) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1",
                              observed_at=at)
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


@pytest.fixture()
def peopled(os_):
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    ids = {
        "hung_heart": _remember(os_, "Anh Hùng có tiền sử bệnh tim."),
        "hung_bp": _remember(os_, "Anh Hùng bị huyết áp cao, đang uống thuốc."),
        "hung_job": _remember(os_, "Anh Hùng làm việc tại công ty Locaith."),
        "an_phone": _remember(os_, "Khách hàng An Phát có số điện thoại 0912345678."),
        "an_job": _remember(os_, "An Phát làm việc tại công ty Bình Minh."),
        "server": _remember(os_, "Máy chủ OCR chạy ở cổng 8000."),
    }
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)
    return ids


# --------------------------------------------------------------------------
# resolving a request into a scope
# --------------------------------------------------------------------------

@pytest.mark.parametrize("request_text,expect_subject,expect_topic", [
    ("Hãy quên thông tin về sức khoẻ của anh Hùng", "hùng", True),
    ("quên hết dữ liệu khách hàng An Phát", "an phát", False),
    ("xoá mọi thứ liên quan tới anh Hùng", "hùng", False),
    ("quên số điện thoại của An Phát đi", "an phát", True),
])
def test_a_request_resolves_into_a_subject_and_maybe_a_topic(
        request_text, expect_subject, expect_topic):
    from bio_agent_os.cognitive.forget_scope import resolve

    scope = resolve(request_text)
    assert scope.subject and expect_subject in scope.subject.lower(), scope
    assert bool(scope.topic) is expect_topic, scope


def test_boilerplate_never_becomes_the_scope():
    """"quên", "thông tin", "hãy" appear in every request and in no memory.

    Measured: treating them as content words is what put the word-overlap
    matcher below its own threshold on 15 of 18 requests.
    """
    from bio_agent_os.cognitive.forget_scope import resolve

    scope = resolve("Hãy quên thông tin về sức khoẻ của anh Hùng")
    blob = f"{scope.subject} {scope.topic}".lower()
    for word in ("quên", "thông tin", "hãy", "xoá", "dữ liệu"):
        assert word not in blob, f"{word!r} lọt vào phạm vi xoá"


# --------------------------------------------------------------------------
# preview before deleting
# --------------------------------------------------------------------------

def test_preview_shows_what_would_go_without_deleting_it(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    scope = resolve("xoá mọi thứ liên quan tới anh Hùng")
    matched = preview(os_, scope)

    assert {m.memory_id for m in matched} == {
        peopled["hung_heart"], peopled["hung_bp"], peopled["hung_job"]}
    assert os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0] == 6


def test_a_topic_scope_takes_only_that_aspect(os_, peopled):
    """The capability `forget-002` has been xfailed on since this morning.

    "Quên thông tin sức khoẻ của anh Hùng" must take the heart condition and
    the blood pressure and leave the job — the memory never says "sức khoẻ",
    so this cannot be done by matching words.
    """
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    scope = resolve("Hãy quên thông tin về sức khoẻ của anh Hùng")
    matched = {m.memory_id for m in preview(os_, scope)}

    assert peopled["hung_heart"] in matched
    assert peopled["hung_bp"] in matched
    assert peopled["hung_job"] not in matched, "xoá lan sang chuyện công việc"
    assert peopled["server"] not in matched


def test_a_scope_that_matches_nothing_says_so(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    assert preview(os_, resolve("quên hết về chị Mai Anh")) == []


# --------------------------------------------------------------------------
# no match is not success — the rule this file exists for
# --------------------------------------------------------------------------

def test_matching_nothing_reports_no_match_not_a_clean_bill(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import ForgetStatus, forget_scoped

    result = forget_scoped(os_, "quên hết về chị Mai Anh", actor="tuananh")

    assert result.status is ForgetStatus.NO_MATCH
    assert result.matched_claims == 0
    assert result.deleted_claims == 0
    assert result.unmatched_reason, "không khớp gì mà không nói vì sao"
    assert result.succeeded is False, (
        "không tìm thấy gì để xoá KHÔNG PHẢI là đã xoá xong"
    )


def test_verified_clean_and_succeeded_are_different_questions(os_, peopled):
    """`verified_clean` asks whether the content is gone. `succeeded` asks
    whether this request did anything. A request that matched nothing leaves a
    store that is trivially "clean" of what it never found."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    nothing = forget_scoped(os_, "quên hết về chị Mai Anh", actor="tuananh")
    assert nothing.succeeded is False

    real = forget_scoped(os_, "xoá mọi thứ liên quan tới anh Hùng",
                         actor="tuananh")
    assert real.succeeded is True
    assert real.verified_clean is True
    assert real.deleted_claims == 3


def test_a_deletion_reports_what_it_touched(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    result = forget_scoped(os_, "quên số điện thoại của An Phát đi",
                           actor="tuananh")
    payload = result.as_dict()
    for key in ("status", "matched_claims", "deleted_claims",
                "derived_invalidated", "unmatched_reason", "verified_clean"):
        assert key in payload, key
    assert result.derived_invalidated >= 1, "vector của ký ức đã xoá vẫn còn"


def test_a_deletion_needs_an_actor(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    with pytest.raises(ValueError):
        forget_scoped(os_, "xoá mọi thứ liên quan tới anh Hùng", actor="")


# --------------------------------------------------------------------------
# the acceptance criteria
# --------------------------------------------------------------------------

def test_the_old_value_is_not_retrievable_afterwards(os_, peopled):
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    forget_scoped(os_, "quên số điện thoại của An Phát đi", actor="tuananh")
    for question in ("số điện thoại An Phát", "liên hệ của An Phát thế nào",
                     "0912345678"):
        hits = os_.recall(query=question, context=CTX, limit=10) or []
        assert not any("0912345678" in h.memory.content for h in hits), question


def test_a_paraphrase_does_not_bring_it_back(os_, peopled):
    """Asking sideways is the obvious next attempt after a deletion."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    forget_scoped(os_, "Hãy quên thông tin về sức khoẻ của anh Hùng",
                  actor="tuananh")
    for question in ("anh Hùng có bệnh gì", "tình trạng sức khoẻ anh Hùng",
                     "anh Hùng đang uống thuốc gì", "tiền sử bệnh"):
        hits = os_.recall(query=question, context=CTX, limit=10) or []
        leaked = [h.memory.content for h in hits
                  if "tim" in h.memory.content or "huyết áp" in h.memory.content]
        assert leaked == [], f"{question!r} kéo lại: {leaked}"


def test_new_information_on_the_same_topic_is_still_learnable(os_, peopled):
    """A deletion removes what was known, not the ability to know it. If
    forgetting a topic also blocked learning about it, the system would be
    unusable after its first delete."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    forget_scoped(os_, "Hãy quên thông tin về sức khoẻ của anh Hùng",
                  actor="tuananh")
    _remember(os_, "Anh Hùng vừa khám sức khoẻ định kỳ, kết quả bình thường.")

    hits = os_.recall(query="anh Hùng khám sức khoẻ", context=CTX, limit=10) or []
    assert any("bình thường" in h.memory.content for h in hits), (
        "sau khi quên một chủ đề thì không học lại được chủ đề đó nữa"
    )


def test_a_consolidated_memory_cannot_resurrect_the_deleted_value(os_, peopled):
    from bio_agent_os.cognitive.consolidation import CONSOLIDATED_MARKER
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.models import CognitiveMemory

    merged = CognitiveMemory(
        tenant_id="t1", workspace_id="w1", memory_type=MemoryType.SEMANTIC,
        content="Tổng hợp: Anh Hùng có tiền sử bệnh tim và làm ở Locaith.",
        source_event_ids=[], confidence=0.7,
        metadata={CONSOLIDATED_MARKER: [peopled["hung_heart"],
                                        peopled["hung_job"]]},
    )
    os_.memories.put(merged, commit=True)

    result = forget_scoped(os_, "Hãy quên thông tin về sức khoẻ của anh Hùng",
                           actor="tuananh")
    left = os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories WHERE content LIKE ?",
        ("%tim%",)).fetchone()[0]
    assert left == 0, "bản hợp nhất vẫn giữ nội dung đã xoá"
    assert result.verified_clean is True
