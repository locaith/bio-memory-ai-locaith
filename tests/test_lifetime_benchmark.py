"""Can this benchmark fail a system? Prove it before trusting a single number.

A benchmark that everything passes measures nothing, and this project has
already shipped two of them: a cross-course holdout that scored eight correct
answers as fabrications, and a stale case that scored an honest refusal as a
failure. Both looked like working measurements and both were pointed the wrong
way round.

So the discrimination test comes first, and it is the reason this file exists.
Five fake systems are run against the same questions:

    perfect          answers from the ledger                -> must score high
    first_value      always the earliest value ever seen    -> must fail CURRENT
    latest_mention   the most recently *mentioned* value    -> must fail on REPEAT
    never_declines   always answers, never admits ignorance -> must fail FORGOTTEN
    always_declines  always "I don't know"                  -> must fail everything
                                                               except FORGOTTEN

`latest_mention` is the sharpest of the five. A REPEAT event restates a value
without changing it, so a store that ranks by recency of mention reads a repeat
as a change and reports a retired fact as current. That is the exact line
between a memory with a lifecycle and a vector database with timestamps, and if
this benchmark cannot separate `perfect` from `latest_mention` then it is not
testing the thing the product claims.

`always_declines` guards the opposite failure. FORGOTTEN questions want a
refusal, so a benchmark made only of deletions would hand a perfect score to a
system that knows nothing at all.
"""

from __future__ import annotations

import random

import pytest

from bio_agent_os.evals.lifetime_questions import (
    Expect,
    Family,
    Question,
    questions_at,
    score,
)
from bio_agent_os.evals.lifetime_world import (
    EventKind,
    Subject,
    TruthLedger,
    generate,
    summarise,
    tick_to_iso,
)

SEED = 20260814
CHECKPOINTS = (100, 250, 500, 750, 1000)


@pytest.fixture(scope="module")
def world():
    return generate(ticks=1000, subjects=20, seed=SEED)


@pytest.fixture(scope="module")
def asked(world):
    events, ledger, people = world
    rng = random.Random(SEED)
    out: list[Question] = []
    for tick in CHECKPOINTS:
        out.extend(questions_at(ledger, people, tick, rng=rng, per_family=6))
    return out


# --------------------------------------------------------------------------
# the world itself
# --------------------------------------------------------------------------

def test_the_world_is_deterministic():
    """A benchmark whose input drifts cannot compare two runs, and a seeded
    world is the only version of this that survives being re-run next year."""
    first = generate(ticks=200, subjects=8, seed=7)[0]
    second = generate(ticks=200, subjects=8, seed=7)[0]
    assert [(e.tick, e.kind, e.text) for e in first] == \
           [(e.tick, e.kind, e.text) for e in second]


def test_world_time_comes_from_ticks_not_the_wall_clock():
    """Reading `datetime.now()` anywhere on this path would make every age
    depend on when the benchmark ran."""
    assert tick_to_iso(1) < tick_to_iso(500) < tick_to_iso(1000)
    assert tick_to_iso(1)[:4] == "2024"
    assert tick_to_iso(1) == tick_to_iso(1)


def test_every_event_kind_actually_occurs(world):
    """A distinction that never appears in the stream is a distinction the
    benchmark does not test, however well it is described."""
    counts = summarise(world[0])
    for kind in (EventKind.ASSERT, EventKind.SUPERSEDE, EventKind.CORRECT,
                 EventKind.REPEAT, EventKind.CONTRADICT, EventKind.FORGET):
        assert counts.get(kind.value, 0) >= 10, f"{kind.value}: {counts.get(kind.value, 0)}"


# --------------------------------------------------------------------------
# the three ways a fact can stop being current, which are not the same
# --------------------------------------------------------------------------

def test_supersede_keeps_the_old_value_true_in_the_past():
    ledger = TruthLedger()
    ledger.assert_("S1", "job_title", "trưởng nhóm", 10)
    ledger.supersede("S1", "job_title", "trưởng phòng", 40)

    assert ledger.current("S1", "job_title", 50).value == "trưởng phòng"
    assert ledger.at("S1", "job_title", 20, 50).value == "trưởng nhóm"
    assert ledger.ever("S1", "job_title", "trưởng nhóm", 50) is True


def test_correct_removes_the_old_value_from_history_entirely():
    """A correction says the old value was never true. Treating it like a
    supersession would answer "who was CTO in January?" with a name that was
    never CTO — and it would look exactly like working temporal reasoning."""
    ledger = TruthLedger()
    ledger.assert_("S1", "birthday", "12/03/1990", 10)
    ledger.correct("S1", "birthday", "05/07/1988", 40)

    assert ledger.current("S1", "birthday", 50).value == "05/07/1988"
    assert ledger.at("S1", "birthday", 20, 50).value == "05/07/1988"
    assert ledger.ever("S1", "birthday", "12/03/1990", 50) is False


def test_repeat_changes_nothing_but_freshness():
    """The trap. A restated value is not a new value, and a store that ranks by
    most-recent-mention reads it as one."""
    ledger = TruthLedger()
    ledger.assert_("S1", "city", "Hà Nội", 10)
    ledger.supersede("S1", "city", "Đà Nẵng", 40)
    ledger.repeat("S1", "city", 80)

    assert ledger.current("S1", "city", 90).value == "Đà Nẵng"
    assert ledger.last_confirmed("S1", "city", 90) == 80


def test_forget_takes_the_history_with_it():
    """"Forget my old address" that keeps answering "what was your address in
    March" is not a deletion."""
    ledger = TruthLedger()
    ledger.assert_("S1", "city", "Hà Nội", 10)
    ledger.supersede("S1", "city", "Đà Nẵng", 40)
    ledger.forget("S1", "city", 60)

    assert ledger.current("S1", "city", 70) is None
    assert ledger.at("S1", "city", 20, 70) is None
    assert ledger.ever("S1", "city", "Hà Nội", 70) is False
    assert ledger.is_forgotten("S1", "city", 70) is True
    # And before the deletion, history is intact.
    assert ledger.at("S1", "city", 20, 50).value == "Hà Nội"


# --------------------------------------------------------------------------
# the questions
# --------------------------------------------------------------------------

def test_every_family_is_represented(asked):
    present = {q.family for q in asked}
    assert present == set(Family), f"thiếu: {set(Family) - present}"


def test_no_slot_is_asked_as_both_current_and_forgotten(asked):
    """The same fact required in one question and forbidden in another makes a
    perfect score impossible, and the benchmark would read as a hard problem
    rather than a broken one."""
    forgotten = {(q.subject_id, q.attribute, q.tick) for q in asked
                 if q.family is Family.FORGOTTEN}
    current = {(q.subject_id, q.attribute, q.tick) for q in asked
               if q.family is Family.CURRENT}
    assert not (forgotten & current), forgotten & current


def test_the_answer_key_is_reachable(world, asked):
    """Every VALUE question must have its answer somewhere in the ledger.

    Asking something the world never established punishes every system equally
    for a gap in the benchmark. That mistake has already been made here once —
    13 of 24 questions in the embedding comparison were unanswerable.
    """
    _, ledger, _ = world
    for question in asked:
        if question.expect is not Expect.VALUE:
            continue
        assert any(c.value == question.value
                   and c.subject_id == question.subject_id
                   and c.attribute == question.attribute
                   for c in ledger.claims), question


# --------------------------------------------------------------------------
# THE DISCRIMINATION TEST
# --------------------------------------------------------------------------

def _perfect(ledger):
    def answer(q: Question) -> str:
        if q.family is Family.FORGOTTEN:
            # New information after a delete is answerable; the deleted value
            # is not. Both shapes appear in this family.
            if q.expect is Expect.VALUE:
                return q.value
            return "Tôi không có thông tin này, đã được yêu cầu xoá."
        if q.family is Family.EVER:
            if ledger.ever(q.subject_id, q.attribute, q.value, q.tick):
                return "Có, từng như vậy."
            return "Không, chưa từng."
        if q.family is Family.HISTORICAL:
            return q.value            # the key is the ledger's own answer
        claim = ledger.current(q.subject_id, q.attribute, q.tick)
        return claim.value if claim else "Không rõ."
    return answer


def _plausible_wrong(ledger):
    """Answers a *valid value for the attribute* that is not the right one.

    This is the control for a grader that is merely permissive. `perfect`
    scoring exactly 1.000 proves the rubric accepts the ledger's own strings;
    it does not prove the rubric can tell a wrong answer from a right one. A
    system fluently naming plausible job titles must fail, or every number in
    this benchmark is measuring fluency.
    """
    from bio_agent_os.evals.lifetime_world import ATTRIBUTES

    by_key = {a.key: a for a in ATTRIBUTES}

    def answer(q: Question) -> str:
        if q.family is Family.FORGOTTEN:
            return "Tôi không có thông tin này."
        if q.family is Family.EVER:
            return "Có, từng như vậy."
        options = [v for v in by_key[q.attribute].values if v != q.value]
        return options[0] if options else "Không rõ."
    return answer


def _first_value(ledger):
    """Remembers only what it heard first. Should lose CURRENT badly."""
    def answer(q: Question) -> str:
        history = [c for c in ledger.claims
                   if c.subject_id == q.subject_id and c.attribute == q.attribute]
        if not history:
            return "Không rõ."
        return min(history, key=lambda c: c.since).value
    return answer


def _latest_mention(events):
    """Most recently *mentioned* value wins — so a REPEAT of an old value looks
    like a change. This is what a recency-ranked vector store does."""
    def answer(q: Question) -> str:
        seen = [e for e in events
                if e.subject_id == q.subject_id and e.attribute == q.attribute
                and e.tick <= q.tick and e.value]
        return seen[-1].value if seen else "Không rõ."
    return answer


def _never_declines(ledger):
    """Confidently answers everything, and never acted on a delete request.

    Deliberately prefers a removed value where one exists — that is the failure
    mode, and a fake that quietly returns the replacement instead would leak
    nothing and prove nothing.
    """
    def answer(q: Question) -> str:
        history = [c for c in ledger.claims
                   if c.subject_id == q.subject_id and c.attribute == q.attribute]
        if q.family is Family.EVER:
            return "Có."
        if not history:
            return "Có thể là như vậy."
        removed = [c for c in history if c.forgotten_at is not None]
        pool = removed or history
        return max(pool, key=lambda c: c.since).value
    return answer


def _always_declines(_):
    def answer(q: Question) -> str:
        return "Tôi không biết."
    return answer


def test_the_key_never_expects_something_nobody_had_been_told(world, asked):
    """answer_information_cutoff <= observation_tick.

    A benchmark must not grade a system on information it could not have had.
    Measured on 2026-08-15, before this invariant existed: `correct()`
    backdated a corrected value's `since` to the first claim in the slot, so a
    correction made at tick ~400 became the expected answer at checkpoint 10 —
    while the world's only event so far said something else. The system
    answered from what it had been told and was marked wrong.

        S07/employer   since=1  'Bình Minh'  (asserted at ~400)
        world by t=10  T1 assert 'Hoà Bình'
        key at t=10    'Bình Minh'

    Third time this project has scored an unanswerable question. The invariant
    is cheap; not having it cost two rounds of chasing a phantom regression.
    """
    _, ledger, _ = world
    for question in asked:
        if question.expect is not Expect.VALUE:
            continue
        supporting = [c for c in ledger.claims
                      if c.subject_id == question.subject_id
                      and c.attribute == question.attribute
                      and c.value == question.value]
        assert supporting, question
        assert any(c.asserted_at <= question.tick for c in supporting), (
            f"đáp án {question.value!r} cho mốc {question.tick} chỉ được nói ra "
            f"ở tick {min(c.asserted_at for c in supporting)} — chấm hệ bằng "
            f"thông tin nó chưa thể có"
        )


def test_an_oracle_using_future_corrections_is_caught(world):
    """The mutant that must fail, kept for good.

    A grader built on the pre-fix ledger semantics — validity backdated,
    knowability ignored — expects a corrected value before the correction
    happened. If this ever stops failing, the invariant above has been undone
    and every historical number is measuring the wrong thing again.
    """
    events, ledger, people = world

    leaks = []
    for claim in ledger.claims:
        if claim.asserted_at <= claim.since:
            continue                     # not backdated; nothing to leak
        # The old behaviour: `held_at` with no knowability check.
        for tick in (10, 50, 100):
            if claim.since <= tick < (claim.until or 10 ** 9) \
                    and claim.asserted_at > tick:
                leaks.append((claim.subject_id, claim.attribute, tick))
    assert leaks, (
        "thế giới này không còn claim nào bị lùi ngày — oracle đột biến không "
        "chứng minh được gì, phải dựng lại ca thử"
    )

    # And the fixed ledger must refuse every one of them.
    for subject_id, attribute, tick in leaks:
        current = ledger.current(subject_id, attribute, tick)
        assert current is None or current.asserted_at <= tick, (
            f"ledger vẫn trả về một claim chỉ được nói ra sau mốc {tick}"
        )


def test_a_perfect_answerer_scores_high(world, asked):
    """If the ledger's own answers cannot pass, the rubric is unpassable and no
    other number from it means anything.

    Necessary but nowhere near sufficient — this only shows the grader accepts
    the key's own strings. `test_a_fluent_wrong_answer_fails` is the half that
    shows it can tell right from wrong.
    """
    _, ledger, _ = world
    card = score(asked, _perfect(ledger))
    assert card.accuracy >= 0.95, card.as_dict()


def test_a_fluent_wrong_answer_fails(world, asked):
    """The grader must reject a plausible wrong value, not merely accept the
    right one. Without this, a benchmark rewards sounding like an answer."""
    _, ledger, _ = world
    card = score(asked, _plausible_wrong(ledger))
    assert card.family(Family.CURRENT) < 0.15, card.as_dict()
    assert card.family(Family.HISTORICAL) < 0.15, card.as_dict()


def test_enough_deletion_questions_to_mean_something(asked):
    """`forgotten_leak_rate` over four samples is not a rate.

    Measured before the fix: 4 FORGOTTEN questions in 94, because deleted slots
    are rare and the single-pass loop filled the other families first. The
    claim this product rests hardest on had the smallest sample in its own
    benchmark.
    """
    n = sum(1 for q in asked if q.family is Family.FORGOTTEN)
    assert n >= 12, f"chỉ có {n} câu về việc đã xoá — quá ít để nói tỷ lệ rò rỉ"


def test_the_ever_family_is_not_answerable_by_agreeing(asked):
    """When every EVER question expected YES, a fake answering "Có" to
    everything scored 30/30 on the family. Measured, before this."""
    yes = sum(1 for q in asked
              if q.family is Family.EVER and q.expect is Expect.YES)
    no = sum(1 for q in asked
             if q.family is Family.EVER and q.expect is Expect.NO)
    assert no >= 4, f"chỉ {no} câu kỳ vọng KHÔNG — gật bừa vẫn qua được"
    assert yes >= 4, f"chỉ {yes} câu kỳ vọng CÓ — chối bừa vẫn qua được"


def test_remembering_only_the_first_value_fails_on_current_truth(world, asked):
    _, ledger, _ = world
    perfect = score(asked, _perfect(ledger))
    broken = score(asked, _first_value(ledger))
    assert broken.family(Family.CURRENT) < perfect.family(Family.CURRENT) - 0.3, \
        f"perfect={perfect.as_dict()} broken={broken.as_dict()}"


def test_ranking_by_latest_mention_is_caught(world, asked):
    """The line between a lifecycle and a timestamped vector store.

    A REPEAT restates an old value; recency ranking reads that as a change.
    A benchmark that cannot see the difference is not testing the claim.
    """
    events, ledger, _ = world
    perfect = score(asked, _perfect(ledger))
    broken = score(asked, _latest_mention(events))
    assert broken.accuracy < perfect.accuracy - 0.15, \
        f"perfect={perfect.as_dict()} latest_mention={broken.as_dict()}"


def test_a_system_that_never_declines_leaks_what_it_was_told_to_forget(world, asked):
    _, ledger, _ = world
    broken = score(asked, _never_declines(ledger))
    assert broken.forgotten_leak_rate > 0.5, broken.as_dict()

    # Measured over the questions that still forbid a value, not the whole
    # family. Four of the eighteen ask about a value the world told again after
    # the delete; there the right answer *is* the value, so any system reaches
    # it and a family-wide bound would be measuring the world's composition
    # rather than the mutant.
    strict = [q for q in asked
              if q.family is Family.FORGOTTEN and q.forbidden]
    assert len(strict) >= 10, "còn quá ít câu cấm để kết luận"
    assert score(strict, _never_declines(ledger)).accuracy == 0.0, \
        "hệ không bao giờ từ chối vẫn qua được câu có giá trị bị cấm"


def test_refusing_everything_does_not_pass(world, asked):
    """Guards the opposite bias. A benchmark weighted toward deletions would
    reward a system that knows nothing."""
    _, ledger, _ = world
    broken = score(asked, _always_declines(ledger))
    assert broken.accuracy < 0.4, broken.as_dict()
    # It leaks nothing, which is the one thing refusing everything gets right —
    # stated so this test cannot pass by the family simply being absent.
    assert broken.forgotten_leak_rate == 0.0, broken.as_dict()
    assert broken.leak_n > 0, "không có câu nào kiểm rò rỉ — test này chưa chứng minh gì"


def test_new_information_after_a_delete_is_still_answerable(asked):
    """A delete removes a value, not a topic.

    If every deletion question expected a refusal, a system correctly reporting
    the *new* address after "forget my old address" would be marked wrong — the
    benchmark punishing the right behaviour, which has already happened twice
    on this project.
    """
    forgotten = [q for q in asked if q.family is Family.FORGOTTEN]
    assert forgotten, "không có câu nào về việc đã xoá"
    assert sum(1 for q in forgotten if q.forbidden) >= len(forgotten) * 0.6, \
        "quá ít câu nêu chuỗi cấm — bộ đề không còn đo được việc xoá"
    for q in forgotten:
        # The one question with neither is meaningless: nothing to forbid and
        # nothing to expect. `forbidden` is dropped only where the world told
        # the value again after the delete, and there the value is the answer.
        assert q.forbidden or q.value, q
    assert any(q.expect is Expect.VALUE for q in forgotten), \
        "không câu nào cho phép trả lời giá trị mới — sẽ phạt hành vi đúng"
    assert any(q.expect is Expect.DECLINE for q in forgotten), \
        "không câu nào đòi từ chối — sẽ bỏ sót việc xoá mà không có gì thay thế"


def test_a_value_the_world_told_again_after_a_delete_is_not_forbidden():
    """Observation-time scoping on the answer key.

    Measured in the benchmark world: "Hãy quên lương của Phạm Vy" at t=747, then
    the world says "Lương của Phạm Vy là 40 triệu" at t=943 and repeats it at
    955 and 970. Asking at t=1000 while forbidding "40 triệu" demands a refusal
    for something the system was told three times *after* the deletion — the
    third unanswerable-question defect on this benchmark, after two rounds of
    backdated `correct()`.

    A delete erases what was known then. It does not gag the world afterwards.
    """
    ledger = TruthLedger()
    people = [Subject(subject_id="S1", name="Phạm Vy")]
    ledger.assert_("S1", "salary", "40 triệu", tick=10)
    ledger.forget("S1", "salary", tick=20)

    asked_before = [q for q in questions_at(ledger, people, 30,
                                            rng=random.Random(0), per_family=6)
                    if q.family is Family.FORGOTTEN]
    assert asked_before, "không sinh được câu hỏi về việc đã xoá"
    assert asked_before[0].forbidden == "40 triệu"
    assert asked_before[0].expect is Expect.DECLINE

    ledger.assert_("S1", "salary", "40 triệu", tick=40)

    asked_after = [q for q in questions_at(ledger, people, 50,
                                           rng=random.Random(0), per_family=6)
                   if q.family is Family.FORGOTTEN]
    assert asked_after, "câu hỏi biến mất sau khi giá trị được nói lại"
    assert asked_after[0].forbidden == "", asked_after[0]
    assert asked_after[0].expect is Expect.VALUE
    assert asked_after[0].value == "40 triệu"
    assert asked_after[0].grade("Lương của Phạm Vy là 40 triệu."), \
        "trả lời đúng điều vừa được nói lại vẫn bị chấm là rò rỉ"


def test_a_leak_is_judged_against_the_person_it_was_deleted_for():
    """Subject scoping, and per memory rather than across the retrieved set.

    This world hands five phone numbers to twenty subjects, so bare value
    containment flags other people's true records. Measured: three of six
    "privacy leaks" were somebody else's number, and the store had in fact
    removed everything it was asked to.

    The answer is judged differently on purpose — the question already names
    the person, so a bare value there is offered as hers, and requiring the
    name again would let a system pass by returning the deleted number alone.
    """
    q = Question(Family.FORGOTTEN, 100, "Số điện thoại của Hoàng Yến?",
                 Expect.DECLINE, "0977123456", "S04", "phone")
    q.forbidden = "0977123456"
    q.subject_name = "Hoàng Yến"

    assert q.leaks_in_memory("Số điện thoại của Hoàng Yến là 0977123456.")
    assert not q.leaks_in_memory("Số điện thoại của Vũ An là 0977123456."), \
        "ký ức thật của người khác bị tính là rò rỉ"

    # Joined first, these two would read as a leak — one memory's name beside
    # another memory's value. Judged one at a time, neither is.
    memories = ["Hoàng Yến sống ở Hà Nội.",
                "Số điện thoại của Vũ An là 0977123456."]
    assert not any(q.leaks_in_memory(m) for m in memories)
    assert q.leaks_in_memory(" ".join(memories)), \
        "ví dụ này không còn chứng minh được điều nó định chứng minh"

    # The answer stays strict: the bare value is a leak.
    assert q.leaks_in_answer("0977123456")
    assert not q.grade("0977123456")


def test_the_benchmark_separates_every_broken_system_from_the_good_one(world, asked):
    """The summary claim, stated once so a regression in any fake shows up here.

    A margin below 0.15 anywhere means the benchmark has stopped distinguishing
    that failure mode, whatever its other numbers say.
    """
    events, ledger, _ = world
    good = score(asked, _perfect(ledger)).accuracy
    for name, factory, source in (
        ("first_value", _first_value, ledger),
        ("latest_mention", _latest_mention, events),
        ("never_declines", _never_declines, ledger),
        ("always_declines", _always_declines, ledger),
        ("plausible_wrong", _plausible_wrong, ledger),
    ):
        bad = score(asked, factory(source)).accuracy
        assert good - bad >= 0.15, (
            f"{name} chỉ kém {good - bad:.3f} — benchmark không phân biệt được "
            f"kiểu hỏng này (perfect={good:.3f}, {name}={bad:.3f})"
        )
