"""A dated question is not a search. It is a lookup on a validity interval.

Measured on the first real lifetime run, 15/08/2026:

    29 of 55 failures were HISTORICAL
    22 of those 29 returned nothing at all
    planner route on every one:  temporal -> recall

The classifier was right every time. `_CLASSES_FOR` simply had no entry for
TEMPORAL, so the question fell through to vector search — a wiring bug sitting
between knowing what to do and doing it.

"Vào ngày 2025-01-04, Phạm Vy đang giữ chức vụ gì?" cannot be answered by
similarity. Three memories mention Phạm Vy's job, they were true at different
times, and the one that answers is decided by *when*, not by *how alike*.

FOUR SHAPES, one pipeline:

    point-in-time   "tháng 3/2026 Cường làm ở đâu"
    before / after  "trước khi sang Bình Minh thì Cường làm ở đâu"
    history         "Cường đã đổi công ty những lần nào"
    current         "hiện tại Cường làm ở đâu"

    parse constraint -> resolve subject+aspect -> claim history
    -> filter by interval -> apply lifecycle semantics -> answer + provenance

TWO INVARIANTS, designed in rather than patched on:

**A restatement is not a new interval.** If a fact from tick 100 is repeated at
tick 700, a query about tick 500 must still resolve through the original
validity, not through `last_seen`. Deriving intervals from mention order alone
invents a boundary at 700 that never existed — the same trap `latest_mention`
falls into in the oracle table.

**A correction is not a supersession.** Given "sinh năm 1990" at tick 100 and
"xin lỗi, 1991" at tick 120:

    "hệ tin gì tại tick 110"        -> 1990   (belief history: it did believe that)
    "năm sinh thật là gì"           -> 1991   (world truth: 1990 was never true)

Collapsing the two answers a question about the world with a belief the world
never held, and it looks exactly like working temporal reasoning.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")

T100 = "2026-01-10T00:00:00+00:00"
T200 = "2026-03-10T00:00:00+00:00"
T300 = "2026-06-10T00:00:00+00:00"
T400 = "2026-09-10T00:00:00+00:00"

BETWEEN_100_200 = "2026-02-01T00:00:00+00:00"
BETWEEN_200_300 = "2026-04-01T00:00:00+00:00"
AFTER_300 = "2026-08-01T00:00:00+00:00"


class AspectEmbedder:
    """Topic-aware, and symmetric — which the first version was not.

    It matched by asking whether an aspect phrase appeared *inside* the text,
    so it could embed a full sentence and not a two-word query: `embed("làm
    Bình Minh")` produced a null vector because "công ty" is not a substring of
    it. A fake embedder that only works in one direction is not an embedder,
    and it failed the operator for the fixture's shortcoming.

    Word overlap in both directions instead. Values count too, so "Bình Minh"
    identifies the employment slot the way it does to a reader.
    """

    model = "aspect-fake"
    ASPECTS = {
        "công ty": ("công", "ty", "làm", "việc", "nơi", "locaith",
                    "bình", "minh", "đại", "việt"),
        "chức vụ": ("chức", "vụ", "giữ", "trưởng", "giám", "đốc"),
        "ngày sinh": ("sinh", "ngày", "năm", "1990", "1991"),
        "nơi sống": ("sống", "chuyển", "đến", "quê"),
    }

    def embed(self, text: str) -> list[float]:
        words = {w for w in re.findall(r"\w+", str(text).lower(), re.UNICODE)}
        vector = [float(len(words & set(bucket))) for bucket in self.ASPECTS.values()]
        return vector + [0.0] if any(vector) else [0.0] * len(self.ASPECTS) + [1.0]


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "temporal.db", embedder=AspectEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str, *, at: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1", observed_at=at)
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


@pytest.fixture()
def career(os_):
    """Bùi Cường changes employer twice, and the fact is restated once."""
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    ids = {
        "locaith": _remember(os_, "Bùi Cường làm việc tại công ty Locaith.", at=T100),
        "binhminh": _remember(os_, "Bùi Cường làm việc tại công ty Bình Minh.", at=T200),
        "daiviet": _remember(os_, "Bùi Cường làm việc tại công ty Đại Việt.", at=T300),
        "noise": _remember(os_, "Bùi Cường sinh ngày 12/03/1990.", at=T100),
    }
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)
    return ids


# --------------------------------------------------------------------------
# stage 1 — parsing the constraint
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,kind", [
    ("Vào ngày 2026-02-01, Bùi Cường làm ở công ty nào?", "point_in_time"),
    ("Hồi tháng 3/2026 Bùi Cường làm ở đâu?", "point_in_time"),
    ("Trước khi sang Bình Minh thì Bùi Cường làm ở đâu?", "before"),
    ("Sau khi rời Locaith thì Bùi Cường làm ở đâu?", "after"),
    ("Bùi Cường đã đổi công ty những lần nào?", "history"),
    ("Hiện tại Bùi Cường làm ở đâu?", "current"),
])
def test_the_temporal_constraint_is_parsed(question, kind):
    from bio_agent_os.cognitive.temporal_operator import parse_temporal

    assert parse_temporal(question).kind.value == kind, question


def test_a_date_is_extracted_not_guessed():
    from bio_agent_os.cognitive.temporal_operator import parse_temporal

    intent = parse_temporal("Vào ngày 2026-02-01, Bùi Cường làm ở công ty nào?")
    assert intent.when and intent.when.startswith("2026-02-01")


# --------------------------------------------------------------------------
# stage 2 — claim history for one slot
# --------------------------------------------------------------------------

def test_claim_history_is_ordered_and_scoped_to_the_aspect(os_, career):
    from bio_agent_os.cognitive.temporal_operator import claim_history

    spans = claim_history(os_, subject="Bùi Cường", aspect="công ty", context=CTX)
    assert [s.content for s in spans] == [
        "Bùi Cường làm việc tại công ty Locaith.",
        "Bùi Cường làm việc tại công ty Bình Minh.",
        "Bùi Cường làm việc tại công ty Đại Việt.",
    ], [s.content for s in spans]


def test_each_span_gets_an_interval_ending_where_the_next_begins(os_, career):
    """A gap between two spans is an interval where nothing is true, and the
    honest-looking answer there is "không có ghi nhận" — a fact erased by
    arithmetic."""
    from bio_agent_os.cognitive.temporal_operator import claim_history

    spans = claim_history(os_, subject="Bùi Cường", aspect="công ty", context=CTX)
    assert spans[0].valid_from == T100 and spans[0].valid_to == T200
    assert spans[1].valid_from == T200 and spans[1].valid_to == T300
    assert spans[2].valid_from == T300 and spans[2].valid_to is None


# --------------------------------------------------------------------------
# stage 3 — the four query shapes
# --------------------------------------------------------------------------

def test_point_in_time_returns_what_held_then(os_, career):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Vào ngày 2026-02-01, Bùi Cường làm ở công ty nào?", context=CTX)
    assert "Locaith" in result.answer_text
    assert "Bình Minh" not in result.answer_text
    assert result.executed is True


def test_a_later_point_returns_the_later_claim(os_, career):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Vào ngày 2026-04-01, Bùi Cường làm ở công ty nào?", context=CTX)
    assert "Bình Minh" in result.answer_text


def test_current_returns_the_open_span(os_, career):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(os_, "Hiện tại Bùi Cường làm ở đâu?", context=CTX)
    assert "Đại Việt" in result.answer_text


def test_before_walks_the_lifecycle_not_the_index(os_, career):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Trước khi sang Bình Minh thì Bùi Cường làm ở đâu?", context=CTX)
    assert "Locaith" in result.answer_text


def test_history_returns_the_whole_sequence(os_, career):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Bùi Cường đã đổi công ty những lần nào?", context=CTX)
    for company in ("Locaith", "Bình Minh", "Đại Việt"):
        assert company in result.answer_text, result.answer_text


def test_every_answer_carries_its_provenance(os_, career):
    """"Why do you say that" has to be answerable, or the operator is a
    black box that happens to be right."""
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Vào ngày 2026-02-01, Bùi Cường làm ở công ty nào?", context=CTX)
    assert result.provenance, "không nêu được căn cứ"
    assert career["locaith"] in [p["memory_id"] for p in result.provenance]
    assert result.provenance[0]["valid_from"] == T100


# --------------------------------------------------------------------------
# invariant 1 — a restatement is not a new interval
# --------------------------------------------------------------------------

def test_repeating_a_fact_does_not_move_the_boundary(os_, career):
    """The trap `latest_mention` falls into, in the operator this time.

    Restating the Locaith fact long after Bình Minh took over must not make
    Locaith current again, and must not create an interval boundary at the
    moment of the restatement.
    """
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings
    from bio_agent_os.cognitive.temporal_operator import (
        answer_temporal, claim_history,
    )

    _remember(os_, "Nhắc lại, Bùi Cường làm việc tại công ty Locaith.", at=T400)
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)

    spans = claim_history(os_, subject="Bùi Cường", aspect="công ty", context=CTX)
    assert len(spans) == 3, [s.content for s in spans]
    assert spans[0].confirmations == 1, "lần nhắc lại không được ghi nhận"
    assert spans[-1].valid_to is None, "lần nhắc lại đã đóng khoảng hiện hành"

    now = answer_temporal(os_, "Hiện tại Bùi Cường làm ở đâu?", context=CTX)
    assert "Đại Việt" in now.answer_text, "nhắc lại đã kéo giá trị cũ thành hiện tại"

    then = answer_temporal(
        os_, "Vào ngày 2026-04-01, Bùi Cường làm ở công ty nào?", context=CTX)
    assert "Bình Minh" in then.answer_text, "lần nhắc lại đã cắt khoảng hiệu lực"


# --------------------------------------------------------------------------
# invariant 2 — a correction is not a supersession
# --------------------------------------------------------------------------

@pytest.fixture()
def corrected(os_):
    from bio_agent_os.cognitive.semantic_index import backfill_embeddings

    ids = {
        "wrong": _remember(os_, "Bùi Cường sinh năm 1990.", at=T100),
        "right": _remember(os_,
                           "Đính chính: thông tin trước là sai, Bùi Cường sinh năm 1991.",
                           at=T200),
    }
    backfill_embeddings(os_.memories.conn, os_.retrieval.embedder)
    return ids


def test_world_truth_never_returns_the_corrected_value(os_, corrected):
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    for question in ("Vào ngày 2026-02-01, Bùi Cường sinh năm nào?",
                     "Hiện tại Bùi Cường sinh năm nào?"):
        result = answer_temporal(os_, question, context=CTX)
        assert "1991" in result.answer_text, question
        assert "1990" not in result.answer_text, (
            f"{question!r} trả về một niềm tin chưa bao giờ đúng"
        )


def test_belief_history_can_still_say_what_was_believed(os_, corrected):
    """The distinction the design exists for. "What did you think in February"
    is a question about the system, not about the world, and answering it does
    not make 1990 a historical fact."""
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(
        os_, "Vào ngày 2026-02-01, hệ thống tin Bùi Cường sinh năm nào?",
        context=CTX, mode="belief")
    assert "1990" in result.answer_text
    assert result.mode == "belief"


def test_a_correction_is_marked_as_such_in_the_history(os_, corrected):
    from bio_agent_os.cognitive.temporal_operator import claim_history

    spans = claim_history(os_, subject="Bùi Cường", aspect="ngày sinh",
                          context=CTX)
    kinds = [s.kind for s in spans]
    assert "corrected" in kinds, kinds


# --------------------------------------------------------------------------
# the wiring bug this file exists for
# --------------------------------------------------------------------------

def test_the_planner_actually_executes_the_temporal_operator(os_, career):
    """29 of 55 failures were TEMPORAL questions routed to `recall`. The
    classifier was right; nothing acted on it."""
    from bio_agent_os.cognitive.query_planner import QueryKind, plan

    result = plan(os_, "Vào ngày 2026-02-01, Bùi Cường làm ở công ty nào?",
                  context=CTX)
    assert result.kind is QueryKind.TEMPORAL
    assert result.used == "temporal_operator", (
        f"câu temporal vẫn rơi về {result.used!r}"
    )
    assert any("Locaith" in str(getattr(m, "content", m))
               for m in result.memories)


def test_a_content_question_does_not_go_to_the_temporal_operator(os_, career):
    """CORRECTION, 17/08. This asserted `used == "recall"`.

    It was written to stop the temporal operator swallowing present-tense
    questions — "the retrieval path nine canary runs hardened must not be
    replaced by a newer one that happens to be more interesting". That worry
    is still right and still tested: the assertion below is that TEMPORAL_AT
    does not claim this question.

    What changed is the alternative. A present-tense question now runs
    STATE_AT, and it earned that by measurement rather than by being newer —
    on the same 58 questions, 45 correct under generic recall against 49 under
    the operator, with EXISTS and TEMPORAL_AT both unmoved. Pinning `recall`
    here would now be pinning the weaker path.
    """
    from bio_agent_os.cognitive.query_planner import QueryKind, plan

    result = plan(os_, "Bùi Cường làm ở công ty nào?", context=CTX)
    assert result.kind is QueryKind.CONTENT
    assert result.used != "temporal_operator"
    assert result.used in {"state_at", "recall", "recall_after_state_failed"}


def test_the_operator_reports_which_stage_gave_up(os_):
    """`temporal_execution_accuracy` needs to say *where* it failed, or the
    next investigation is another 29 traces read by hand."""
    from bio_agent_os.cognitive.temporal_operator import answer_temporal

    result = answer_temporal(os_, "Vào ngày 2026-02-01, Mai Anh làm ở đâu?",
                             context=CTX)
    assert result.executed is False
    assert result.stage_failed in {"resolve_subject", "claim_history",
                                   "interval_selection"}
    assert result.note
