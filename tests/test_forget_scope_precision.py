"""Deletion scope decided by predicate, not by a number.

Measured across the 18 deletion requests of the lifetime world, both directions
at once — because the previous round improved one and quietly wrecked the other:

    scope_recall             51/52 = 0.981
    scope_precision          51/90 = 0.567
    collateral_deletion_rate 39/90 = 0.433

Forty-three per cent of what a deletion removed should have stayed. A request to
forget where somebody lives was taking their phone number, their job and their
project with it. Unlike a leak, that cannot be undone.

The cause was the rule itself. `cut = best * 0.6` drops with `best`, so a topic
that matches nothing strongly admits nearly everything. And the two populations
overlap — measured, not assumed:

    nên xoá  : min 0.227  p25 0.352  median 0.456  max 0.786
    KHÔNG nên: min -0.017 p25 0.075  median 0.129  max 0.477

A sweep confirms no single cut works: at 0.30, precision 0.619; at 0.425, recall
0.559. A threshold there is only choosing where to be wrong.

Predicate agreement separates them completely:

    request  "Hãy quên nơi sống của Nguyễn Dũng"  -> LOCATION
    memory   "Nguyễn Dũng sống ở Cần Thơ."        -> LOCATION   in scope
    memory   "Số điện thoại của Nguyễn Dũng là …" -> PHONE       out

    precision 1.000, recall 1.000, 68 true / 379 rejected, 0 errors

Deterministic, auditable, and it does not move when the embedder changes. Where
the memory names no predicate the answer is ABSTAIN — reported, never deleted
on a guess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "precision.db")
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


@pytest.fixture()
def dung(os_):
    """One person, five predicates. Only one of them is ever requested."""
    return {
        "city": _remember(os_, "Nguyễn Dũng sống ở Cần Thơ."),
        "city_repeat": _remember(os_, "Nhắc lại, Nguyễn Dũng sống ở Cần Thơ."),
        "phone": _remember(os_, "Số điện thoại của Nguyễn Dũng là 0987654321."),
        "job": _remember(os_, "Nguyễn Dũng đang giữ chức phó giám đốc."),
        "project": _remember(os_, "Nguyễn Dũng phụ trách dự án kho dữ liệu."),
        "salary": _remember(os_, "Lương của Nguyễn Dũng là 32 triệu."),
    }


def test_a_city_request_takes_only_the_city_memories(os_, dung):
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    matched = {m.memory_id for m in
               preview(os_, resolve("Hãy quên nơi sống của Nguyễn Dũng."))}
    assert matched == {dung["city"], dung["city_repeat"]}, [
        m for m in matched]


@pytest.mark.parametrize("request_text,expected", [
    ("Hãy quên nơi sống của Nguyễn Dũng.", {"city", "city_repeat"}),
    ("Hãy quên số điện thoại của Nguyễn Dũng.", {"phone"}),
    ("Hãy quên chức vụ của Nguyễn Dũng.", {"job"}),
    ("Hãy quên dự án phụ trách của Nguyễn Dũng.", {"project"}),
    ("Hãy quên lương của Nguyễn Dũng.", {"salary"}),
])
def test_each_predicate_request_is_exact(os_, dung, request_text, expected):
    """Both directions on every predicate: nothing missed, nothing extra."""
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    matched = {m.memory_id for m in preview(os_, resolve(request_text))}
    assert matched == {dung[key] for key in expected}, request_text


def test_a_whole_subject_request_still_takes_everything(os_, dung):
    """Naming no predicate means the whole person, and that must not narrow."""
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    matched = {m.memory_id for m in
               preview(os_, resolve("xoá mọi thứ liên quan tới Nguyễn Dũng"))}
    assert matched == set(dung.values())


def test_a_memory_with_no_readable_predicate_is_abstained_not_deleted(os_):
    """NOT_EVALUABLE, in the deletion path.

    A sentence the resolver cannot place must not be swept up on a guess, and
    must not be silently ignored either — it is reported so a person can decide.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped, preview, resolve

    keep = _remember(os_, "Nguyễn Dũng sống ở Cần Thơ.")
    vague = _remember(os_, "Nguyễn Dũng có vẻ ổn dạo này.")

    scope = resolve("Hãy quên nơi sống của Nguyễn Dũng.")
    matched = {m.memory_id for m in preview(os_, scope)}
    assert matched == {keep}, "câu mơ hồ bị cuốn vào lệnh xoá"

    result = forget_scoped(os_, "Hãy quên nơi sống của Nguyễn Dũng.",
                           actor="test")
    assert vague in {a["memory_id"] for a in result.abstained}, (
        "câu không đọc được vị từ vừa không bị xoá vừa không được nêu ra")
    assert result.abstained[0]["reason"]


def test_abstentions_are_reported_in_the_result(os_, dung):
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    _remember(os_, "Nguyễn Dũng có vẻ ổn dạo này.")
    result = forget_scoped(os_, "Hãy quên nơi sống của Nguyễn Dũng.",
                           actor="test")
    payload = result.as_dict()
    assert "abstained" in payload
    assert payload["abstained"], "không nêu mẩu nào cần người quyết định"


def test_the_rule_does_not_depend_on_an_embedder(os_, dung):
    """No vectors at all, same answer.

    A scope rule that changes when the embedding backfill has not caught up is
    the defect this replaces — and one that changes when the embedder is swapped
    cannot be audited a year later.
    """
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    assert os_.retrieval.embedder is None
    matched = {m.memory_id for m in
               preview(os_, resolve("Hãy quên số điện thoại của Nguyễn Dũng."))}
    assert matched == {dung["phone"]}


def test_the_match_says_which_predicate_it_agreed_on(os_, dung):
    """Auditable: "why was this deleted" answered without re-running anything."""
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    matched = preview(os_, resolve("Hãy quên lương của Nguyễn Dũng."))
    assert matched
    assert "SALARY" in matched[0].why, matched[0].why


def test_another_persons_record_is_never_in_scope(os_, dung):
    """Predicate agreement must not override subject scoping."""
    from bio_agent_os.cognitive.forget_scope import preview, resolve

    other = _remember(os_, "Trần Thảo sống ở Huế.")
    matched = {m.memory_id for m in
               preview(os_, resolve("Hãy quên nơi sống của Nguyễn Dũng."))}
    assert other not in matched
