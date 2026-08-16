"""One person, one identity — and two people, never one.

The bug this closes: `_names` required an uppercase token to *start* a name,
and the world lowercases the first character on SUPERSEDE / CORRECT / REPEAT /
CONTRADICT sentences. "Từ hôm nay, phạm Vy làm việc tại …" stored `entity:
"Vy"` while the plain form stored `"Phạm Vy"`. One woman, two identities, on
20 of 35 templates — latent only because nothing read the field yet.

Two rates, both zero, and the tie-break between them stated once:

    subject_identity_split_rate   one person under several ids
    subject_false_merge_rate      one id covering several people

When it is not established that two surfaces are the same person, they stay
apart. An unresolved identity is a question left open; a false merge answers
one person's question with another person's records.
"""

from __future__ import annotations

import pytest

from bio_agent_os.cognitive.aspect_resolver import resolve_frame
from bio_agent_os.cognitive.subject_identity import (
    UNKNOWN, UNTRUSTED, false_merge_rate, identify, normalize, split_rate,
)

#: The frozen identity suite. Every shape the world and ordinary Vietnamese
#: produce, with the person each sentence is really about.
FROZEN = [
    # plain, two syllables
    ("Phạm Vy làm việc tại công ty An Phát.", "Phạm Vy"),
    # lowercased first syllable — the bug
    ("Từ hôm nay, phạm Vy làm việc tại công ty Bình Minh.", "Phạm Vy"),
    ("Đính chính: thông tin trước là sai, phạm Vy sống ở Huế.", "Phạm Vy"),
    ("Nhắc lại, phạm Vy đang giữ chức trưởng nhóm.", "Phạm Vy"),
    ("Theo một nguồn khác, phạm Vy sống ở Cần Thơ.", "Phạm Vy"),
    # three syllables
    ("Nguyễn Văn An sống ở Hà Nội.", "Nguyễn Văn An"),
    ("Từ hôm nay, nguyễn Văn An sống ở Huế.", "Nguyễn Văn An"),
    # mid-sentence
    ("Lương của Phạm Vy là 40 triệu.", "Phạm Vy"),
    ("Số điện thoại của Phạm Vy là 0912345678.", "Phạm Vy"),
    # punctuation around the name
    ("Phạm Vy, hiện tại, đang giữ chức phó giám đốc.", "Phạm Vy"),
    # repeated in one sentence
    ("Phạm Vy nói rằng Phạm Vy sẽ chuyển đi.", "Phạm Vy"),
]


def _identity_of(text: str, known: set[str]) -> str:
    frame = resolve_frame(text)
    if not frame.subject:
        return ""
    return identify(frame.subject, known=known).subject_id


def test_the_same_person_gets_one_identity_across_every_shape():
    """`subject_identity_split_rate = 0` on the frozen suite."""
    known: set[str] = set()
    groups: dict[str, set[str]] = {}
    unresolved = []
    for text, person in FROZEN:
        frame = resolve_frame(text)
        if frame.subject:
            known.add(frame.subject)
    for text, person in FROZEN:
        subject_id = _identity_of(text, known)
        if not subject_id:
            unresolved.append(text)
            continue
        groups.setdefault(person, set()).add(subject_id)

    rate = split_rate(groups)
    assert rate == 0.0, (
        f"subject_identity_split_rate={rate}; "
        f"{ {p: ids for p, ids in groups.items() if len(ids) > 1} }")
    # Coverage may be low and that is allowed. Splits are not.
    assert len(unresolved) <= 2, unresolved


def test_two_people_are_never_merged():
    """`subject_false_merge_rate = 0`. The worse of the two failures, so it
    gets the tie-break: an ambiguous surface stays unresolved."""
    known = {"Vũ An", "Trần An", "Phạm Vy"}
    groups = {
        "Vũ An": {identify("Vũ An", known=known).subject_id},
        "Trần An": {identify("Trần An", known=known).subject_id},
        "Phạm Vy": {identify("Phạm Vy", known=known).subject_id},
    }
    assert false_merge_rate(groups) == 0.0


def test_an_ambiguous_given_name_refuses_to_pick_a_person():
    """"An" ends both "Vũ An" and "Trần An". Joining the more popular one is
    a coin toss between two people, which is the leak."""
    result = identify("An", known={"Vũ An", "Trần An", "Phạm Vy"})
    assert result.status == UNKNOWN
    assert "không đủ căn cứ" in result.note
    assert result.subject_id == "an"          # itself, joined to nobody


def test_an_unambiguous_short_form_does_complete():
    """One candidate is a safe completion; the refusal above is about two."""
    result = identify("Vy", known={"Phạm Vy", "Trần An"})
    assert result.subject_id == "phạm vy"
    assert result.status == UNTRUSTED
    assert "hoàn thiện" in result.note


def test_case_alone_never_splits_an_identity():
    known = {"Phạm Vy"}
    assert identify("Phạm Vy", known=known).subject_id == \
        identify("phạm vy", known=known).subject_id


def test_diacritics_are_not_stripped():
    """This world holds both "Hà" and "Hạ". A comparison that folds them is a
    false-merge generator."""
    assert normalize("Trần Hà") != normalize("Trần Hạ")


def test_an_identity_read_from_text_is_never_trusted():
    """Same epistemic model as the predicate slot, for the same reason: an
    identity read out of free text is an inference, and persisting it does
    not promote it."""
    result = identify("Phạm Vy", known={"Phạm Vy"})
    assert result.status == UNTRUSTED
    assert result.resolver_version
    assert result.as_dict()["subject_epistemic_status"] == UNTRUSTED


@pytest.mark.parametrize("text,expected", [
    ("Từ hôm nay, phạm Vy làm việc tại công ty Bình Minh.", "Phạm Vy"),
    ("Đính chính: thông tin trước là sai, lê Sơn sống ở Huế.", "Lê Sơn"),
    ("Nhắc lại, nguyễn Văn An đang giữ chức trưởng nhóm.", "Nguyễn Văn An"),
])
def test_a_lowercased_first_syllable_is_still_part_of_the_name(text, expected):
    """The extraction fix, at the surface level rather than the identity one.

    A Vietnamese name is family name then given name; a lowercase token joins
    one when the token after it is capitalised and it is nothing else. That is
    the shape of the language, not a rule about this corpus.
    """
    frame = resolve_frame(text)
    assert frame.subject
    assert normalize(frame.subject) == normalize(expected), frame.subject
