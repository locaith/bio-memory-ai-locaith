"""The six relations, their invariants, and the ways of getting them wrong.

`old != new -> supersede` is the rule under test here, in the sense that every
case below is one it fails. It is right often enough to look correct and wrong
in the two places that cost the most: a corrected birth date becomes historical
truth, and a second value for a multi-valued predicate deletes the first.

Measured while building this, on 312 cases worded outside the classifier's
marker tables:

    treating "later and different" as a change   silent 1.000  forced 1.000
    requiring evidence that it *was* a change    silent 0.000  forced 0.000

The cost of the second is real and is in `test_the_price_of_caution_is_stated`:
supersede recall falls from 105/105 to 61/105. Those forty-four become UNKNOWN,
which is a question left open rather than a belief written wrongly.
"""

from __future__ import annotations

import sqlite3

import pytest

from bio_agent_os.cognitive.aspect_resolver import Predicate
from bio_agent_os.cognitive.relations import (
    ALTERNATIVE_SOURCE_MARKERS,
    CHANGE_MARKERS,
    CLASSIFIER_VERSION,
    CORRECTION_MARKERS,
    Cardinality,
    Claim,
    Event,
    Relation,
    SEMANTICS,
    TemporalBehavior,
    classify_relation,
    judgements,
    record_judgement,
    semantics_for,
)


def _c(value: str, predicate: Predicate, at: str = "0100",
       subject: str = "Trần Hà") -> Claim:
    return Claim(value=value, predicate=predicate, subject=subject,
                 observed_at=at, valid_from=at, claim_id=f"c-{value}")


def _e(value: str, text: str, predicate: Predicate, at: str = "0500",
       subject: str = "Trần Hà") -> Event:
    return Event(value=value, text=text, predicate=predicate, subject=subject,
                 observed_at=at, event_id=f"e-{value}")


# --------------------------------------------------------------------------
# REPEAT — reinforcement only
# --------------------------------------------------------------------------

def test_a_repeat_does_not_move_the_truth_window():
    """Being mentioned again is not becoming true again.

    `Relation.changes_truth` is what a caller acts on, and REPEAT must never
    open a new validity interval — that is the `repeat_extends_validity`
    mutant, and it turns a store into one where the most-discussed fact is
    always the most recent one.
    """
    verdict = classify_relation(
        _c("trưởng nhóm", Predicate.ROLE),
        _e("trưởng nhóm", "Nhắc lại, Trần Hà đang giữ chức trưởng nhóm.",
           Predicate.ROLE))
    assert verdict.relation is Relation.REPEAT
    assert verdict.relation.changes_truth is False


def test_a_repeat_is_a_repeat_whatever_words_surround_it():
    """Decided on the value, not on a marker, so no wording can break it."""
    for text in ("Nhắc lại, Trần Hà sống ở Hà Nội.",
                 "Trần Hà sống ở Hà Nội.",
                 "Từ hôm nay, Trần Hà sống ở Hà Nội.",
                 "Theo một nguồn khác, Trần Hà sống ở Hà Nội.",
                 "Đính chính: Trần Hà sống ở Hà Nội."):
        verdict = classify_relation(_c("Hà Nội", Predicate.LOCATION),
                                    _e("Hà Nội", text, Predicate.LOCATION))
        assert verdict.relation is Relation.REPEAT, text


def test_a_repeat_does_not_raise_confidence_above_the_marked_cases():
    """Said twice is not proof. Reinforcement belongs to the claim's own
    count, not to how sure the classifier is about the relation."""
    once = classify_relation(_c("Hà Nội", Predicate.LOCATION),
                             _e("Hà Nội", "Trần Hà sống ở Hà Nội.",
                                Predicate.LOCATION, at="0200"))
    twice = classify_relation(_c("Hà Nội", Predicate.LOCATION),
                              _e("Hà Nội", "Nhắc lại, Trần Hà sống ở Hà Nội.",
                                 Predicate.LOCATION, at="0900"))
    assert once.confidence == twice.confidence


# --------------------------------------------------------------------------
# CORRECT — the old claim was never true
# --------------------------------------------------------------------------

def test_a_corrected_birth_date_is_not_a_supersession():
    """Nobody is born twice.

    Supersession would make 1990 the true answer to "when was he born, as of
    March?" — the value the user explicitly retracted, returned as history.
    This is the case `old != new -> supersede` cannot survive.
    """
    verdict = classify_relation(
        _c("12/03/1990", Predicate.BIRTHDAY),
        _e("12/03/1991",
           "Xin lỗi, thông tin trước là sai, Trần Hà sinh ngày 12/03/1991.",
           Predicate.BIRTHDAY))
    assert verdict.relation is Relation.CORRECT


def test_a_correction_outranks_immutability():
    """An immutable predicate whose values disagree is a CONFLICT — unless
    somebody said which one was wrong, which is the whole point of saying it."""
    unmarked = classify_relation(
        _c("12/03/1990", Predicate.BIRTHDAY),
        _e("05/07/1988", "Trần Hà sinh ngày 05/07/1988.", Predicate.BIRTHDAY))
    assert unmarked.relation is Relation.CONFLICT

    marked = classify_relation(
        _c("12/03/1990", Predicate.BIRTHDAY),
        _e("05/07/1988", "Đính chính: Trần Hà sinh ngày 05/07/1988.",
           Predicate.BIRTHDAY))
    assert marked.relation is Relation.CORRECT


def test_a_backdated_correction_is_still_a_correction():
    """Later in belief time, earlier in world time.

    Ordering on `valid_from` reads this as a conflict. Ordering on
    `observed_at` reads it as what it is. The same confusion produced two
    rounds of unanswerable benchmark questions on this project.
    """
    verdict = classify_relation(
        Claim(value="Locaith", predicate=Predicate.EMPLOYER, subject="Trần Hà",
              observed_at="0500", valid_from="0500"),
        Event(value="An Phát",
              text="Thực ra hồi tháng 1 Trần Hà đã làm ở An Phát rồi.",
              predicate=Predicate.EMPLOYER, subject="Trần Hà",
              observed_at="0800", valid_from="0100"))
    assert verdict.relation is Relation.CORRECT


# --------------------------------------------------------------------------
# SUPERSEDE — the old claim was true, and stopped being
# --------------------------------------------------------------------------

def test_a_marked_change_supersedes():
    verdict = classify_relation(
        _c("nhân viên kinh doanh", Predicate.ROLE),
        _e("trưởng nhóm", "Từ hôm nay, Trần Hà đang giữ chức trưởng nhóm.",
           Predicate.ROLE))
    assert verdict.relation is Relation.SUPERSEDE
    assert verdict.relation.changes_truth is True


def test_a_change_cannot_precede_what_it_changes():
    """Ordered and not later means the two disagree, not that one replaced
    the other."""
    verdict = classify_relation(
        _c("Huế", Predicate.LOCATION, at="0700"),
        _e("Hà Nội", "Từ hôm nay, Trần Hà sống ở Hà Nội.", Predicate.LOCATION,
           at="0200"))
    assert verdict.relation is Relation.CONFLICT


def test_an_immutable_predicate_never_supersedes():
    """Not a threshold and not a heuristic: one row in `SEMANTICS` says the
    world-truth cannot change, and every rule downstream reads it."""
    verdict = classify_relation(
        _c("12/03/1990", Predicate.BIRTHDAY),
        _e("05/07/1988", "Từ hôm nay, Trần Hà sinh ngày 05/07/1988.",
           Predicate.BIRTHDAY))
    assert verdict.relation is not Relation.SUPERSEDE


# --------------------------------------------------------------------------
# COEXIST — both true at once
# --------------------------------------------------------------------------

def test_two_preferences_coexist():
    """Liking Rust does not stop you liking Python. A classifier that has only
    met single-valued predicates deletes one of them."""
    verdict = classify_relation(
        _c("Python", Predicate.PREFERENCE),
        _e("Rust", "Trần Hà thích Rust.", Predicate.PREFERENCE))
    assert verdict.relation is Relation.COEXIST
    assert verdict.relation.changes_truth is False


def test_a_change_marker_does_not_collapse_a_multi_valued_predicate():
    """"Bây giờ thích Go" says something new; it retracts nothing."""
    verdict = classify_relation(
        _c("Python", Predicate.PREFERENCE),
        _e("Go", "Bây giờ Trần Hà thích Go.", Predicate.PREFERENCE))
    assert verdict.relation is Relation.COEXIST


def test_cardinality_is_what_decides_coexistence_not_the_values():
    """The same two-different-values shape, opposite verdicts, and the only
    difference is one row in the table."""
    single = classify_relation(
        _c("Locaith", Predicate.EMPLOYER),
        _e("An Phát", "Từ hôm nay Trần Hà làm ở An Phát.", Predicate.EMPLOYER))
    many = classify_relation(
        _c("Locaith", Predicate.PREFERENCE),
        _e("An Phát", "Từ hôm nay Trần Hà thích An Phát.",
           Predicate.PREFERENCE))
    assert single.relation is Relation.SUPERSEDE
    assert many.relation is Relation.COEXIST
    assert semantics_for(Predicate.EMPLOYER).cardinality is Cardinality.ONE
    assert semantics_for(Predicate.PREFERENCE).cardinality is Cardinality.MANY


# --------------------------------------------------------------------------
# CONFLICT — incompatible, undecided
# --------------------------------------------------------------------------

def test_a_second_source_is_a_conflict_not_an_update():
    verdict = classify_relation(
        _c("trưởng nhóm", Predicate.ROLE),
        _e("giám đốc kỹ thuật",
           "Theo một nguồn khác, Trần Hà đang giữ chức giám đốc kỹ thuật.",
           Predicate.ROLE))
    assert verdict.relation is Relation.CONFLICT
    assert verdict.relation.resolves is False


def test_a_rumour_about_a_change_is_not_a_change():
    """The hedge has to beat the change marker, because a sentence can carry
    both and the hedge is the one that says how much to believe it."""
    verdict = classify_relation(
        _c("Locaith", Predicate.EMPLOYER),
        _e("Đại Việt",
           "Nghe nói bây giờ Trần Hà làm việc tại công ty Đại Việt.",
           Predicate.EMPLOYER))
    assert verdict.relation is Relation.CONFLICT


def test_simultaneous_disagreement_is_a_conflict():
    verdict = classify_relation(
        _c("Hà Nội", Predicate.LOCATION, at="0300"),
        _e("Đà Nẵng", "Trần Hà sống ở Đà Nẵng.", Predicate.LOCATION, at="0300"))
    assert verdict.relation is Relation.CONFLICT


# --------------------------------------------------------------------------
# UNKNOWN — first-class
# --------------------------------------------------------------------------

def test_unknown_is_not_a_transition():
    for verdict in (
        classify_relation(_c("Hà Nội", Predicate.LOCATION),
                          _e("trưởng nhóm", "Trần Hà giữ chức trưởng nhóm.",
                             Predicate.ROLE)),
        classify_relation(_c("Hà Nội", Predicate.LOCATION, subject="Trần Hà"),
                          _e("Huế", "Trần Thảo sống ở Huế.",
                             Predicate.LOCATION, subject="Trần Thảo")),
        classify_relation(
            Claim(value="Hà Nội", predicate=Predicate.LOCATION),
            Event(value="Huế", text="Trần Hà sống ở Huế.",
                  predicate=Predicate.LOCATION)),
    ):
        assert verdict.relation is Relation.UNKNOWN
        assert verdict.relation.changes_truth is False
        assert verdict.confidence == 0.0


def test_an_untimed_pair_is_unknown_rather_than_conflict():
    """Two values with no order might be one fact stated twice. Calling that a
    conflict asserts an incompatibility nobody demonstrated."""
    verdict = classify_relation(
        Claim(value="Hà Nội", predicate=Predicate.LOCATION, subject="Trần Hà"),
        Event(value="Huế", text="Trần Hà sống ở Huế.",
              predicate=Predicate.LOCATION, subject="Trần Hà"))
    assert verdict.relation is Relation.UNKNOWN
    assert verdict.reason == "no_observation_times"


def test_an_undeclared_predicate_declines_rather_than_borrowing_a_default():
    """A predicate nobody described might hold many values, in which case
    there is no disagreement to report."""
    verdict = classify_relation(
        Claim(value="A", predicate=Predicate.UNKNOWN, subject="X",
              observed_at="0100"),
        Event(value="B", text="X thế nào đó.", predicate=Predicate.UNKNOWN,
              subject="X", observed_at="0300"))
    assert verdict.relation is Relation.UNKNOWN
    assert verdict.reason == "undeclared_predicate"


def test_every_predicate_in_the_enum_is_declared():
    """The guard on the rule above. An undeclared predicate produces UNKNOWN
    verdicts, which is visible but useless — this fails first."""
    missing = [p.name for p in Predicate
               if p.attribute and p not in SEMANTICS]
    assert not missing, f"chưa khai báo ngữ nghĩa cho: {missing}"


# --------------------------------------------------------------------------
# the rule that was replaced
# --------------------------------------------------------------------------

def test_an_unmarked_later_value_is_not_assumed_to_be_a_change():
    """The measured reason this is not SUPERSEDE.

    "Có tin đồn là 40 triệu", "Lúc nãy tôi ghi lộn: 40 triệu" and "Từ hôm nay
    40 triệu" all arrive as a later, different value. Reading a change into
    all three scored `silent_conflict_rate` 1.000 and `forced_transition_rate`
    1.000 on 312 held-out-wording cases.
    """
    verdict = classify_relation(
        _c("32 triệu", Predicate.SALARY),
        _e("40 triệu", "Lương của Trần Hà là 40 triệu.", Predicate.SALARY))
    assert verdict.relation is Relation.UNKNOWN
    assert verdict.reason == "unmarked_value_change"


def test_a_caller_may_opt_into_treating_it_as_a_change():
    """A policy about a domain, not a fact about a sentence — so it is opt-in
    and it says so in the reason."""
    verdict = classify_relation(
        _c("32 triệu", Predicate.SALARY),
        _e("40 triệu", "Lương của Trần Hà là 40 triệu.", Predicate.SALARY),
        {"unmarked_change": "supersede"})
    assert verdict.relation is Relation.SUPERSEDE
    assert verdict.reason == "revisable_single_valued"
    assert any("chính sách" in e for e in verdict.evidence)


# --------------------------------------------------------------------------
# the corpus and the classifier must not share a vocabulary
# --------------------------------------------------------------------------

def test_held_out_wording_is_actually_held_out():
    """The repair that proved nothing, prevented from coming back.

    The first attempt at paraphrasing the benchmark corpus drew its phrases
    from the classifier's own marker lists. It scored 1.000 again, and it was
    typing the same strings twice. This fails if any held-out phrase ever
    leaks into the tables it is meant to sit outside.
    """
    from bio_agent_os.evals.relation_cases import HELDOUT_PARAPHRASES

    known = tuple(m.lower() for m in
                  CORRECTION_MARKERS + CHANGE_MARKERS + ALTERNATIVE_SOURCE_MARKERS)
    leaked = [phrase for alternatives in HELDOUT_PARAPHRASES.values()
              for phrase in alternatives
              if any(marker in phrase.lower() for marker in known)]
    assert not leaked, f"cách nói lẽ ra phải lạ nhưng có trong bảng marker: {leaked}"


def test_unseen_wording_fails_safely_rather_than_confidently():
    """The property that matters when the vocabulary misses.

    Getting these right is not the claim — the classifier has never seen the
    words. What it must not do is write a belief on the strength of them.
    """
    unseen = (
        ("Lúc nãy tôi ghi lộn: Trần Hà sống ở Huế.", Predicate.LOCATION, "Huế"),
        ("Có tin đồn là Trần Hà làm ở Đại Việt.", Predicate.EMPLOYER, "Đại Việt"),
        ("Cập nhật tình hình: lương Trần Hà là 55 triệu.", Predicate.SALARY,
         "55 triệu"),
    )
    for text, predicate, value in unseen:
        old = _c({"Huế": "Hà Nội", "Đại Việt": "Locaith",
                  "55 triệu": "32 triệu"}[value], predicate)
        verdict = classify_relation(old, _e(value, text, predicate))
        assert not verdict.relation.changes_truth, (
            f"viết niềm tin dựa trên câu chữ chưa từng thấy: {text!r} "
            f"-> {verdict.relation.value}")


# --------------------------------------------------------------------------
# mutants
# --------------------------------------------------------------------------

def test_every_mutant_is_caught_by_a_named_measurement():
    """Not by one blended threshold. A mutant that destroys CORRECT while
    CORRECT is 10% of the corpus moves overall accuracy by 0.09 and slips a
    0.15 gate — which measures the corpus, not the mutant."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from relation_benchmark import MUTANTS, recall_gaps, score  # noqa: E402

    from bio_agent_os.evals.lifetime_world import generate
    from bio_agent_os.evals.relation_cases import all_cases

    events, ledger, people = generate(ticks=400, subjects=12, seed=20260814)
    cases = all_cases(events, ledger, people)
    ours = score(cases, classify_relation)

    for name, mutant in MUTANTS.items():
        theirs = score(cases, mutant)
        caught = (
            ours["lifecycle_relation_accuracy"]
            - theirs["lifecycle_relation_accuracy"] >= 0.15
            or (theirs["silent_conflict_rate"] or 0) > 0
            or (theirs["forced_transition_rate"] or 0) > 0
            or bool(recall_gaps(cases, mutant))
        )
        assert caught, f"mutant {name} không bị số đo nào bắt"


def test_the_price_of_caution_is_stated():
    """Requiring evidence of a change costs supersede recall. Both halves are
    asserted so neither can quietly drift."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from relation_benchmark import recall_for, score  # noqa: E402

    from bio_agent_os.evals.lifetime_world import generate
    from bio_agent_os.evals.relation_cases import all_cases

    events, ledger, people = generate(ticks=400, subjects=12, seed=20260814)
    cases = all_cases(events, ledger, people)

    def lenient(old, new, ctx=None):
        return classify_relation(old, new, {"unmarked_change": "supersede"})

    strict_result = score(cases, classify_relation)
    lenient_result = score(cases, lenient)
    strict_hit, total = recall_for(cases, classify_relation, Relation.SUPERSEDE)
    lenient_hit, _ = recall_for(cases, lenient, Relation.SUPERSEDE)

    assert strict_result["silent_conflict_rate"] == 0.0
    assert strict_result["forced_transition_rate"] == 0.0
    assert strict_result["unsafe_failures"] == 0
    assert lenient_hit > strict_hit, "chính sách nới lỏng không bắt thêm gì"
    assert lenient_result["forced_transition_rate"] > 0, \
        "nới lỏng mà không tốn gì thì so sánh này vô nghĩa"


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_a_judgement_can_be_argued_with_a_year_later(conn):
    """Everything needed to re-run the verdict, and nothing a deletion would
    have to chase down."""
    old = _c("nhân viên kinh doanh", Predicate.ROLE)
    new = _e("trưởng nhóm", "Từ hôm nay, Trần Hà đang giữ chức trưởng nhóm.",
             Predicate.ROLE)
    verdict = classify_relation(old, new)
    record_judgement(conn, verdict, old_claim=old, new_event=new,
                     tenant_id="t1", valid_to="0500")

    (row,) = judgements(conn)
    assert row["relation"] == "supersede"
    assert row["claim_a"] == old.claim_id and row["claim_b"] == new.event_id
    assert row["predicate"] == "job_title"
    assert row["reason"] == "explicit_change"
    assert row["evidence"], "không ghi bằng chứng nào"
    assert row["semantics"]["cardinality"] == "one"
    assert row["classifier_version"] == CLASSIFIER_VERSION
    assert row["confidence"] > 0
    assert row["observed_at"] == "0500"
    assert row["valid_from"] and row["valid_to"] == "0500"
    assert row["constitution_version"]
    assert row["recorded_at"]


def test_the_undecided_verdicts_are_recorded_too(conn):
    """A conflict nobody wrote down is a conflict nobody will resolve."""
    old = _c("trưởng nhóm", Predicate.ROLE)
    new = _e("giám đốc kỹ thuật",
             "Theo một nguồn khác, Trần Hà đang giữ chức giám đốc kỹ thuật.",
             Predicate.ROLE)
    record_judgement(conn, classify_relation(old, new), old_claim=old,
                     new_event=new)
    (row,) = judgements(conn)
    assert row["relation"] == "conflict"
    assert row["confidence"] == pytest.approx(0.85)


def test_the_semantics_row_travels_with_the_verdict(conn):
    """Changing `SEMANTICS` changes verdicts. An audit that records the verdict
    without the row it was based on cannot explain a verdict that later
    stopped reproducing."""
    old = _c("Python", Predicate.PREFERENCE)
    new = _e("Rust", "Trần Hà thích Rust.", Predicate.PREFERENCE)
    record_judgement(conn, classify_relation(old, new), old_claim=old,
                     new_event=new)
    (row,) = judgements(conn)
    assert row["semantics"] == {
        "cardinality": "many", "temporal_behavior": "revisable",
        "coexistence_policy": "allow", "correction_policy": "marked_only",
        "volatility": "slow"}


def test_temporal_behaviour_is_declared_not_guessed():
    """One row per predicate, and BIRTHDAY is the one that is different."""
    assert semantics_for(Predicate.BIRTHDAY).temporal_behavior is \
        TemporalBehavior.IMMUTABLE
    revisable = [p for p in SEMANTICS
                 if SEMANTICS[p].temporal_behavior is TemporalBehavior.REVISABLE]
    assert Predicate.BIRTHDAY not in revisable
    assert len(revisable) >= 6
