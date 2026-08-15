"""Some questions do not name a topic. They name a kind of memory.

Measured on the live learning store on 2026-08-15:

    "hallucination của LLM là gì"         cao nhất 0.8013   vượt sàn 5/413
    "tôi từng làm sai câu nào"            cao nhất 0.2795   vượt sàn 0/413
    "điểm yếu của tôi là gì cần ôn lại"   cao nhất 0.2918   vượt sàn 0/413

Both self-referential questions return nothing at all, against a calibrated
floor of 0.3547. The obvious reading is "the floor is too high", and it is
wrong. The hit at 0.2918 was the right memory, so the *ranking* worked — but
lowering the floor to admit it would also admit 400 topically-similar memories,
turning an honest empty result into a confident wrong one.

The real reason, measured before anything was built:

    413 memories, all of memory_type `semantic`
    metadata empty except on the 45 consolidated rows
    24 "Làm sai câu" / "Điểm yếu tự ghi nhận" memories
    0 of 24 carry any class label

The question asks for a *class* of memory and no class was ever recorded. No
similarity threshold can fix that, because similarity is not what is being
asked. "Tôi từng làm sai gì" is not about a topic; it is about a relationship
between the user and a group of memories.

So: classify the question, and answer class-seeking questions with a structured
selection rather than a vector search. Structured queries need structured data,
which is why the ingest now records what each memory *is*.

The classifier is rule-based and deterministic on purpose. A model call per
query costs money on every read and returns a different answer on Tuesday.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "planner.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, memory_class: str | None = None):
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(
        event=event, memory_type=MemoryType.SEMANTIC, content=text,
        confidence=0.9,
        metadata={"memory_class": memory_class} if memory_class else {})


# --------------------------------------------------------------------------
# classifying the question
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "hallucination của LLM là gì",
    "prompt engineering có những kỹ thuật nào",
    "cổng của máy chủ OCR là bao nhiêu",
    "what is a foundation model",
])
def test_a_question_about_a_topic_is_content(question):
    from bio_agent_os.cognitive.query_planner import QueryKind, classify

    assert classify(question) is QueryKind.CONTENT


@pytest.mark.parametrize("question", [
    "tôi từng làm sai câu nào",
    "điểm yếu của tôi là gì cần ôn lại",
    "tôi hay sai ở chỗ nào",
    "những lỗi tôi lặp lại nhiều lần",
    "what did I get wrong",
])
def test_a_question_about_oneself_is_self_history(question):
    from bio_agent_os.cognitive.query_planner import QueryKind, classify

    assert classify(question) is QueryKind.SELF_HISTORY, question


@pytest.mark.parametrize("question", [
    "hồi tháng 1 giá là bao nhiêu",
    "trước đây ai là CTO",
    "vào ngày 05/07/2026 tôi đang làm gì",
    "who was the CTO in January",
])
def test_a_question_about_a_past_moment_is_temporal(question):
    from bio_agent_os.cognitive.query_planner import QueryKind, classify

    assert classify(question) is QueryKind.TEMPORAL, question


@pytest.mark.parametrize("question", [
    "thông tin nào về tôi đã cũ",
    "em biết những gì về anh",
    "anh đã đổi ý về chuyện đó mấy lần",
])
def test_a_question_about_the_memory_itself_is_meta(question):
    from bio_agent_os.cognitive.query_planner import QueryKind, classify

    assert classify(question) is QueryKind.META, question


def test_classification_is_deterministic():
    """A planner that answers differently on the second call cannot be
    benchmarked, and a benchmark that cannot be repeated is an anecdote."""
    from bio_agent_os.cognitive.query_planner import classify

    question = "tôi từng làm sai câu nào"
    first = classify(question)
    assert all(classify(question) is first for _ in range(50))


def test_the_planner_reads_no_model_and_no_embedder():
    """Checked on the source rather than by importing: an import that happens
    to succeed today proves nothing about what it dragged in."""
    source = (Path(__file__).resolve().parents[1] / "bio_agent_os" /
              "cognitive" / "query_planner.py").read_text(encoding="utf-8")
    for forbidden in ("llm_engine", "LLMEngine", "openai", "Embedder",
                      "background_jobs"):
        assert f"import {forbidden}" not in source
        assert f"from bio_agent_os.{forbidden}" not in source


# --------------------------------------------------------------------------
# answering a class-seeking question
# --------------------------------------------------------------------------

def test_selecting_by_class_finds_what_similarity_could_not(os_):
    """The end the whole module exists for."""
    from bio_agent_os.cognitive.query_planner import MemoryClass, select_by_class

    _remember(os_, "Làm sai câu: cross_val_predict trả về gì.",
              memory_class=MemoryClass.MISTAKE.value)
    _remember(os_, "Điểm yếu tự ghi nhận: Foundation Models cần xem lại.",
              memory_class=MemoryClass.WEAKNESS.value)
    _remember(os_, "Hallucination là đầu ra lệch khỏi sự thật.",
              memory_class=MemoryClass.LESSON_FACT.value)

    found = select_by_class(os_, [MemoryClass.MISTAKE, MemoryClass.WEAKNESS],
                            context=CTX, limit=10)
    contents = [m.content for m in found]
    assert len(contents) == 2, contents
    assert not any("Hallucination" in c for c in contents), (
        "chọn theo lớp mà vẫn kéo về ký ức khác lớp"
    )


def test_selection_by_class_ignores_topical_similarity_entirely(os_):
    """A class question must not be answered by whatever happens to look
    similar. The failure mode being replaced is a plausible wrong answer, and
    it is worse than the empty result it replaces."""
    from bio_agent_os.cognitive.query_planner import MemoryClass, select_by_class

    for index in range(20):
        _remember(os_, f"Bài học số {index} về máy học và dữ liệu.",
                  memory_class=MemoryClass.LESSON_FACT.value)
    _remember(os_, "Làm sai câu: quên chuẩn hoá dữ liệu trước khi train.",
              memory_class=MemoryClass.MISTAKE.value)

    found = select_by_class(os_, [MemoryClass.MISTAKE], context=CTX, limit=10)
    assert len(found) == 1
    assert "quên chuẩn hoá" in found[0].content


def test_an_unlabelled_store_says_so_rather_than_guessing(os_):
    """The state the real store was in.

    Returning nothing is right; returning topically-similar memories would be a
    confident wrong answer. What was missing is that nobody was told why.
    """
    from bio_agent_os.cognitive.query_planner import MemoryClass, plan

    _remember(os_, "Hallucination là đầu ra lệch khỏi sự thật.")
    result = plan(os_, "tôi từng làm sai câu nào", context=CTX)

    assert result.memories == []
    assert result.unanswerable_reason, "trả rỗng mà không nói vì sao"
    assert "lớp" in result.unanswerable_reason.lower() or \
           "class" in result.unanswerable_reason.lower()


def test_a_content_question_still_goes_through_ordinary_recall(os_):
    """The planner must not become a second retrieval path that quietly
    replaces the one nine canary runs hardened."""
    from bio_agent_os.cognitive.query_planner import QueryKind, plan

    _remember(os_, "Máy chủ OCR chạy ở cổng 8000.",
              memory_class="lesson_fact")
    result = plan(os_, "máy chủ OCR chạy ở cổng nào", context=CTX)

    assert result.kind is QueryKind.CONTENT
    assert result.used == "recall"


def test_the_plan_says_which_route_it_took(os_):
    """A router nobody can inspect is a router nobody can debug."""
    from bio_agent_os.cognitive.query_planner import MemoryClass, plan

    _remember(os_, "Làm sai câu: quên chuẩn hoá dữ liệu.",
              memory_class=MemoryClass.MISTAKE.value)
    result = plan(os_, "tôi từng làm sai câu nào", context=CTX)

    assert result.used == "select_by_class"
    assert MemoryClass.MISTAKE in result.classes
    assert result.memories, "đi đúng đường nhưng không trả về gì"


# --------------------------------------------------------------------------
# the ingest has to record what it is storing
# --------------------------------------------------------------------------

def test_the_learning_ingest_labels_every_memory_it_writes():
    """No class recorded, no class-seeking question answerable. Measured on the
    live store: 0 of 24 mistake memories carried a label."""
    import sys

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts"))
    from behaviour_benchmark import matches_delete_request  # noqa: F401  (path check)
    from ingest_learning_inbox import memory_items

    assessment = {
        "schema": "locaith.learning.v1", "type": "quiz",
        "course": "Test Course", "lesson_id": "x1", "item_title": "Quiz 1",
        "attempts": [{
            "attempt_no": 1, "score_percent": 80, "passed": True,
            "weak_topics": ["Foundation models cần xem lại"],
            "wrong_or_partial": [{"question": "What is X?",
                                  "correct_answer": "Y",
                                  "explanation": "vì Z"}],
        }],
    }
    items = memory_items(assessment)
    classes = {c for _, c in items}
    assert "mistake" in classes, classes
    assert "weakness" in classes, classes
    assert "score" in classes, classes
    assert all(c for _, c in items), "có mẩu ký ức không mang nhãn lớp nào"
