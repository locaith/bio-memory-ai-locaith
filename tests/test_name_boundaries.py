"""A name does not run across a clause boundary.

The defect this closes, `PREFIX_JOINS_THE_NAME`: `_names` tokenised with
`[\\w]+`, which discards punctuation, and a lowercase token was allowed to join
a following capitalised one. So the last word of the clause *before* a name was
absorbed into it, and "…thông tin trước là sai, Bùi Cường sinh năm 1991." was
stored under the subject "sai Bùi Cường" — a person who does not exist.

Why it mattered far downstream: once `SUBJECT_IDENTITY_READ` selects a person's
history by stored identity, a corrupted subject span is not a cosmetic error.
The row is a definite mismatch, `_by_subject` drops it with no textual rescue,
and it leaves the chronology silently.

And the corrupted sentences were not a random sample. "Đính chính," and "Nhắc
lại," are the frames that mark a correction and a restatement — the two things
`claim_history` needs the row for. `temporal_operator._core` strips exactly
these markers before comparing claims; nothing stripped them at ingest, so the
rows carrying lifecycle meaning were precisely the rows whose identity broke.

The old suite killed this mutant only through three temporal assertions three
stages downstream (`['asserted']`, `assert 0 == 1`), which is why two rounds of
hypotheses looked at row ordering and case folding instead of at extraction.
These tests fail at the producer.
"""

from __future__ import annotations

import pytest

from bio_agent_os.cognitive.aspect_resolver import resolve_frame
from bio_agent_os.cognitive.slot_backfill import slot_for

PLAIN = "Bùi Cường sinh năm 1990."

#: Every frame is the same claim about the same person. The subject must not
#: depend on what the sentence says before the name.
FRAMED = [
    "Nhắc lại, Bùi Cường sinh năm 1990.",
    "Đính chính: thông tin trước là sai, Bùi Cường sinh năm 1990.",
    "Đính chính. Bùi Cường sinh năm 1990.",
    "Theo một nguồn khác, Bùi Cường sinh năm 1990.",
    "Hôm nay Bùi Cường sinh năm 1990.",
    "Xác nhận lại; Bùi Cường sinh năm 1990.",
    "Anh ấy nói (sai) Bùi Cường sinh năm 1990.",
    "Tôi nhớ nhầm — thật ra Bùi Cường sinh năm 1990.",
]


@pytest.mark.parametrize("sentence", FRAMED)
def test_a_discourse_prefix_is_not_part_of_the_name(sentence):
    """PREFIX_JOINS_THE_NAME dies here, at the producer."""
    assert resolve_frame(sentence).subject == resolve_frame(PLAIN).subject, (
        f"khung câu đã đổi chủ thể: {sentence!r}")


@pytest.mark.parametrize("sentence", FRAMED)
def test_the_persisted_entity_matches_the_plain_sentence(sentence):
    """The same invariant one layer down, where it is actually stored — a
    resolver that is right and a slot writer that persists something else is
    the same failure to a reader."""
    assert slot_for(sentence)["entity"] == slot_for(PLAIN)["entity"], sentence


def test_the_lowercased_family_name_rescue_still_works():
    """The rescue this bound narrows exists for a real shape: a sentence that
    opens mid-phrase lowercases the family name. Narrowing it to clause-initial
    position must not remove it."""
    assert resolve_frame(
        "Từ hôm nay, phạm Vy làm việc tại công ty Locaith.").subject == "phạm Vy"


def test_a_date_prefix_does_not_become_a_family_name():
    """The digit guard, kept. "ngày 21 Phạm Vy…" once read "21" as a family
    name, which is a new identity per date."""
    assert resolve_frame("Ngày 21 Phạm Vy sinh năm 1990.").subject == "Phạm Vy"


def test_two_names_across_a_comma_stay_two_names():
    """A run closes at the boundary instead of spanning it."""
    frame = resolve_frame("Công ty Locaith, Bùi Cường làm trưởng nhóm.")
    assert frame.subject != "Locaith Bùi Cường"


@pytest.mark.xfail(strict=True, reason=(
    "Known cost of bounding the lowercase rescue to clause-initial position: a "
    "lowercased family name that is NOT clause-initial now splits to the given "
    "name alone. 'Ngày 21 phạm Vy…' resolves to 'Vy', not 'phạm Vy'. This is a "
    "split, not a merge — one person under two identities rather than two "
    "people under one — and the eval world never generates the shape (all four "
    "of its prefixes end in a comma). Recorded strict so it cannot quietly "
    "become acceptable, and so that fixing it is visible when someone does."))
def test_a_lowercased_family_name_mid_clause_is_still_a_name():
    assert resolve_frame("Ngày 21 phạm Vy sinh năm 1990.").subject == "phạm Vy"
