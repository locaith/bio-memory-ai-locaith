"""A question names a predicate, not a keyword.

Measured on the lifetime run of 15/08/2026: 14 of 34 failures — 41%, the
largest group by a wide margin — were the right person answered from the wrong
slot.

    hỏi   : "Vào ngày 2024-11-21, Phạm Vy đang sống ở đâu?"
    cần   : 'Hà Nội'
    trả về: "Phạm Vy đang giữ chức nhân viên kinh doanh."

The aspect extracted from that question was the single word "sống". One common
token cannot separate `LOCATION` from `ROLE`, so both slots stayed in the
candidate set and the interval logic picked whichever came first.

The failure that matters is not "nothing found". It is **right subject, wrong
predicate, answered confidently** — a memory system that does that is worse
than one that says it does not know, because nobody checks a confident answer.

So the resolver returns a *normalised predicate*, never a surface word:

    "đang làm ở công ty nào"   ->  EMPLOYER
    "nơi công tác"             ->  EMPLOYER
    "employer hiện tại"        ->  EMPLOYER

and signals are tried in order of how much they actually prove:

    1. an explicit alias phrase from the ontology
    2. a multi-token phrase match
    3. a predicate the store already uses
    4. semantic similarity within the finite predicate set
    5. UNKNOWN

UNKNOWN is a result, not a failure. Guessing beats it only when the guess is
right, and `wrong_slot_rate` is the metric that says how often it was not.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# 1. paraphrase — the same predicate said several ways
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Bùi Cường đang làm ở đâu?",
    "Bùi Cường làm ở công ty nào?",
    "Nơi công tác của Bùi Cường là gì?",
    "Bùi Cường đang làm việc tại đơn vị nào?",
    "Where does Bùi Cường work?",
])
def test_every_way_of_asking_about_work_lands_on_employer(question):
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    assert resolve_aspect(question).predicate is Predicate.EMPLOYER, question


@pytest.mark.parametrize("question,expected", [
    ("Bùi Cường đang giữ chức vụ gì?", "ROLE"),
    ("Vị trí của Bùi Cường là gì?", "ROLE"),
    ("Bùi Cường đang sống ở đâu?", "LOCATION"),
    ("Địa chỉ của Bùi Cường?", "LOCATION"),
    ("Số điện thoại của Bùi Cường là gì?", "PHONE"),
    ("Bùi Cường sinh ngày nào?", "BIRTHDAY"),
    ("Lương của Bùi Cường là bao nhiêu?", "SALARY"),
    ("Bùi Cường đang phụ trách dự án nào?", "PROJECT"),
])
def test_each_predicate_is_reachable(question, expected):
    from bio_agent_os.cognitive.aspect_resolver import resolve_aspect

    assert resolve_aspect(question).predicate.name == expected, question


def test_the_result_is_a_predicate_not_a_surface_word():
    """"làm" is what the question says. EMPLOYER is what it means, and only
    the second can be compared with a stored claim."""
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    for question in ("nơi công tác của Bùi Cường",
                     "Bùi Cường đang làm cho ai"):
        result = resolve_aspect(question)
        assert result.predicate is Predicate.EMPLOYER
        assert result.evidence, "không nêu được căn cứ nào"


# --------------------------------------------------------------------------
# 2. near neighbours — the pairs that actually get confused
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected,not_expected", [
    ("Bùi Cường làm ở công ty nào?", "EMPLOYER", "ROLE"),
    ("Bùi Cường giữ chức vụ gì?", "ROLE", "EMPLOYER"),
    ("Bùi Cường sống ở thành phố nào?", "LOCATION", "EMPLOYER"),
    ("Địa chỉ nhà của Bùi Cường?", "LOCATION", "PHONE"),
    ("Bùi Cường sinh ngày nào?", "BIRTHDAY", "LOCATION"),
])
def test_near_neighbours_stay_apart(question, expected, not_expected):
    """`employer` and `role` share "làm", "công", "việc"; `city` and `address`
    share almost everything. These are the pairs 14 failures came from."""
    from bio_agent_os.cognitive.aspect_resolver import resolve_aspect

    result = resolve_aspect(question)
    assert result.predicate.name == expected, f"{question} -> {result}"
    assert result.predicate.name != not_expected


# --------------------------------------------------------------------------
# 3. single-token ambiguity — the actual defect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["sống", "làm", "ở", "của", "gì"])
def test_a_bare_common_word_resolves_to_unknown(question):
    """The invariant. One common token must not decide a slot.

    "sống" alone resolved to LOCATION by luck and to ROLE by accident, and the
    accident is what produced 14 confident wrong answers.
    """
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    result = resolve_aspect(question)
    assert result.predicate is Predicate.UNKNOWN, f"{question!r} -> {result}"
    assert result.confidence < 0.5


def test_a_single_token_still_works_when_the_ontology_says_it_is_enough():
    """Not every short word is ambiguous. "lương" names exactly one thing, and
    refusing it would be a different kind of wrong."""
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    assert resolve_aspect("lương").predicate is Predicate.SALARY
    assert resolve_aspect("sinh nhật").predicate is Predicate.BIRTHDAY


def test_unknown_is_reported_as_a_result_not_an_error():
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    result = resolve_aspect("Bùi Cường thế nào rồi?")
    assert result.predicate is Predicate.UNKNOWN
    assert result.note, "trả UNKNOWN mà không nói vì sao"


# --------------------------------------------------------------------------
# 4. temporal anchors must not eat the aspect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Vào ngày 2024-11-21, Phạm Vy đang sống ở đâu?", "LOCATION"),
    ("Tháng 3/2025 Bùi Cường làm ở đâu?", "EMPLOYER"),
    ("Hồi tháng 1 Trần Hà giữ chức vụ gì?", "ROLE"),
    ("Trước khi sang Bình Minh thì Bùi Cường làm ở đâu?", "EMPLOYER"),
])
def test_a_date_or_anchor_does_not_swallow_the_predicate(question, expected):
    """The exact shape of the 14 failures: all historical, all dated."""
    from bio_agent_os.cognitive.aspect_resolver import resolve_aspect

    assert resolve_aspect(question).predicate.name == expected, question


def test_a_company_name_in_the_question_is_not_read_as_the_aspect():
    """"Trước khi sang Bình Minh" once left an aspect of "bình minh làm",
    which matched a birthday memory."""
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_aspect

    result = resolve_aspect(
        "Trước khi sang Bình Minh thì Bùi Cường làm ở đâu?")
    assert result.predicate is Predicate.EMPLOYER
    assert "bình minh" not in " ".join(result.evidence).lower()


# --------------------------------------------------------------------------
# 5. what the resolver is for — a query frame, not a keyword
# --------------------------------------------------------------------------

def test_the_resolver_produces_a_frame_the_store_can_be_asked_with():
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_frame

    frame = resolve_frame("Vào ngày 2024-11-21, Phạm Vy đang sống ở đâu?")
    assert frame.subject == "Phạm Vy"
    assert frame.predicate is Predicate.LOCATION
    assert frame.when and frame.when.startswith("2024-11-21")
    assert frame.query_type == "historical"


def test_a_frame_with_no_predicate_says_so_rather_than_guessing(  ):
    from bio_agent_os.cognitive.aspect_resolver import Predicate, resolve_frame

    frame = resolve_frame("Phạm Vy thế nào rồi?")
    assert frame.predicate is Predicate.UNKNOWN
    assert frame.subject == "Phạm Vy"


def test_predicates_map_onto_the_stores_own_attributes():
    """A predicate that names nothing the store keeps cannot be looked up.

    The world writes `city`, `employer`, `job_title`…; the resolver has to land
    on those exact keys or the frame is unusable.
    """
    from bio_agent_os.cognitive.aspect_resolver import Predicate
    from bio_agent_os.evals.lifetime_world import ATTRIBUTES

    keys = {a.key for a in ATTRIBUTES}
    mapped = {p.attribute for p in Predicate if p.attribute}
    assert keys <= mapped, f"thuộc tính không có vị từ tương ứng: {keys - mapped}"


def test_the_resolver_reads_no_model():
    """Deterministic and free. A model call per query costs money on every read
    and answers differently next Tuesday."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "bio_agent_os" /
              "cognitive" / "aspect_resolver.py").read_text(encoding="utf-8")
    for forbidden in ("llm_engine", "LLMEngine", "openai", "Embedder"):
        assert f"import {forbidden}" not in source
        assert f"from bio_agent_os.{forbidden}" not in source
