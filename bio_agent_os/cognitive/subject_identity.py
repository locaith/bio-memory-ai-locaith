"""Who a memory is about, as an identity rather than as a string.

Two failures, and they pull in opposite directions:

    SPLIT   one person stored under two identities. "Phạm Vy" and "phạm Vy"
            are the same woman; a store that keeps them apart answers half
            her history and reports the other half as absent.
    MERGE   two people collapsed into one. "An" appears in "Vũ An" and in
            "Trần An"; a store that joins them answers one person's question
            with another person's records, which is the privacy failure.

Both are zero-targets and the second is the worse one, so the tie-break is
stated once: **when it is not established that two surfaces are the same
person, they stay apart.** An unresolved identity is a question left open. A
false merge is a leak.

That is why a bare given name never merges into a fuller one. "Vy" *might* be
Phạm Vy and might be Lê Vy, and nothing in the sentence says which — so it
resolves to itself and waits, rather than joining the most popular candidate.

Three fields, because they answer three different questions:

    subject_surface     what the sentence actually said. Changes freely.
    subject_normalized  case and whitespace folded. A comparison key.
    subject_id          the identity. Stable, and the thing history is keyed
                        by.

The same epistemic model as the predicate slot, for the same reason: an
identity read out of free text is an inference, and persisting it does not
promote it. `subject_epistemic_status` is UNTRUSTED for everything this
module produces.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from .slot_backfill import TRUSTED, UNKNOWN, UNTRUSTED

RESOLVER_VERSION = "subject_identity@1"


def normalize(surface: str) -> str:
    """Case and whitespace folded; diacritics kept.

    Stripping diacritics would merge distinct Vietnamese names — this world
    holds both "Hà" and "Hạ" — and a comparison that cannot tell them apart
    is a false-merge generator.
    """
    text = unicodedata.normalize("NFC", str(surface or ""))
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True)
class SubjectIdentity:
    surface: str
    normalized: str
    subject_id: str
    status: str = UNTRUSTED
    source: str = ""
    resolver_version: str = RESOLVER_VERSION
    #: Why this did not resolve to a fuller name, when it did not.
    note: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.subject_id) and self.status != UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {"subject_surface": self.surface,
                "subject_normalized": self.normalized,
                "subject_id": self.subject_id,
                "subject_epistemic_status": self.status,
                "subject_source": self.source,
                "subject_resolver_version": self.resolver_version,
                "subject_note": self.note}


def identify(surface: str, *, known: Iterable[str] = (),
             source: str = "resolver") -> SubjectIdentity:
    """Turn a surface form into an identity.

    `known` is the set of surfaces already seen in this store. It is what
    makes the ambiguous case detectable at all: without it, "Vy" is just a
    short name; with it, "Vy" is a suffix of two different people and the
    honest answer is that this sentence did not say which.
    """
    normalized = normalize(surface)
    if not normalized:
        return SubjectIdentity("", "", "", status=UNKNOWN, source=source,
                               note="không có tên nào")

    known_normalized = {normalize(k) for k in known if normalize(k)}
    known_normalized.discard(normalized)

    # An exact match, whatever the casing, is the same person. This is the
    # split case, and it is the whole reason `normalized` exists.
    if normalized in {normalize(k) for k in known}:
        return SubjectIdentity(surface, normalized, normalized,
                               status=UNTRUSTED, source=source)

    # A strict suffix of a fuller name is ambiguous exactly when more than one
    # fuller name ends in it. One candidate is a safe completion; two is a
    # coin toss, and a coin toss between two people is the failure that costs
    # the most.
    completions = sorted(k for k in known_normalized
                         if k.endswith(" " + normalized))
    if len(completions) == 1:
        return SubjectIdentity(surface, normalized, completions[0],
                               status=UNTRUSTED, source=source,
                               note=f"hoàn thiện từ {completions[0]!r}")
    if len(completions) > 1:
        return SubjectIdentity(
            surface, normalized, normalized, status=UNKNOWN, source=source,
            note=f"{len(completions)} người cùng kết thúc bằng {normalized!r}: "
                 f"{completions[:3]} — không đủ căn cứ để gộp")

    return SubjectIdentity(surface, normalized, normalized,
                           status=UNTRUSTED, source=source)


def split_rate(groups: dict[str, set[str]]) -> float:
    """Of the true people, how many ended up under more than one id."""
    if not groups:
        return 0.0
    split = sum(1 for ids in groups.values() if len(ids) > 1)
    return round(split / len(groups), 4)


def false_merge_rate(groups: dict[str, set[str]]) -> float:
    """Of the ids produced, how many cover more than one true person."""
    owners: dict[str, set[str]] = {}
    for person, ids in groups.items():
        for subject_id in ids:
            owners.setdefault(subject_id, set()).add(person)
    if not owners:
        return 0.0
    merged = sum(1 for people in owners.values() if len(people) > 1)
    return round(merged / len(owners), 4)


__all__ = ["RESOLVER_VERSION", "SubjectIdentity", "TRUSTED", "UNKNOWN",
           "UNTRUSTED", "false_merge_rate", "identify", "normalize",
           "split_rate"]
