"""What a question is *about*, as a predicate the store can be asked with.

Measured on the lifetime run of 15/08/2026: 14 of 34 failures — 41%, the
largest group by a wide margin — were the right person answered from the wrong
slot.

    hỏi   : "Vào ngày 2024-11-21, Phạm Vy đang sống ở đâu?"
    cần   : 'Hà Nội'
    trả về: "Phạm Vy đang giữ chức nhân viên kinh doanh."

The aspect handed downstream was the single word "sống", produced by dropping
stopwords and keeping whatever survived. One common token cannot separate
LOCATION from ROLE, so both slots stayed in the candidate set and the interval
logic picked whichever came first.

The dangerous failure in a memory system is not "nothing found". It is **right
subject, wrong predicate, answered confidently** — nobody re-checks a confident
answer, and this produced fourteen of them.

So the result is a normalised predicate, never a surface word:

    "đang làm ở công ty nào"  ->  EMPLOYER
    "nơi công tác"            ->  EMPLOYER
    "employer hiện tại"       ->  EMPLOYER

Signals are tried in order of how much each actually proves:

    1. an explicit alias phrase from the ontology     (strongest)
    2. a multi-token phrase overlap
    3. a discriminating single token, where the ontology says one exists
    4. UNKNOWN                                        (weakest, and fine)

**UNKNOWN is a result.** Guessing beats it only when the guess is right, and a
wrong slot answered confidently is the failure being removed. A resolver that
never says "I cannot tell" simply moves its errors somewhere nobody counts them.

No model, no embedder: a call per read costs money on every query and returns
something different next Tuesday. The predicate set is small and closed, which
is exactly the case where an ontology beats a similarity score.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class Predicate(Enum):
    """A slot the store can hold, with the attribute key it maps to.

    The attribute is the point: a predicate that names nothing the store keeps
    cannot be looked up, so each one carries the key the world writes.
    """

    EMPLOYER = "employer"
    ROLE = "job_title"
    LOCATION = "city"
    PHONE = "phone"
    BIRTHDAY = "birthday"
    PROJECT = "project"
    SALARY = "salary"
    #: The one predicate here that holds several values at once. It has no
    #: ontology entry below and so is never *resolved* from a question — it is
    #: named directly by a caller that already knows the slot. It exists
    #: because a relation classifier that has only ever met single-valued
    #: predicates deletes one of "thích Python" and "thích Rust", and a
    #: multi-valued case that lives only in a test is a case the production
    #: table has never been asked to describe. See `relations.SEMANTICS`.
    PREFERENCE = "preference"
    UNKNOWN = ""

    @property
    def attribute(self) -> str:
        return self.value


#: Phrases that name a predicate outright. Multi-token entries are checked
#: first and are the strongest signal available without a model.
ONTOLOGY: dict[Predicate, tuple[str, ...]] = {
    Predicate.EMPLOYER: (
        "làm ở đâu", "làm việc ở đâu", "công ty nào", "nơi làm việc",
        "nơi công tác", "đơn vị công tác", "đang làm cho", "làm cho ai",
        "đang làm tại", "làm việc tại", "đơn vị nào", "employer",
        "work at", "works at", "does .* work", "company",
    ),
    Predicate.ROLE: (
        "chức vụ", "giữ chức", "vị trí", "vai trò", "chức danh",
        "job title", "position", "role",
    ),
    Predicate.LOCATION: (
        "sống ở đâu", "sống ở", "ở đâu", "địa chỉ", "thành phố", "nơi ở",
        "quê ở", "chuyển đến", "cư trú", "lives in", "address", "city",
    ),
    Predicate.PHONE: (
        "số điện thoại", "điện thoại", "liên hệ", "số máy",
        "phone", "contact number",
    ),
    Predicate.BIRTHDAY: (
        "sinh ngày", "ngày sinh", "sinh nhật", "sinh năm", "năm sinh",
        "birthday", "date of birth", "born",
    ),
    Predicate.PROJECT: (
        "dự án nào", "phụ trách dự án", "dự án", "đang phụ trách",
        "project",
    ),
    Predicate.SALARY: (
        "lương", "mức lương", "thu nhập", "salary", "income", "pay",
    ),
}

#: Single tokens that name exactly one predicate and nothing else.
#:
#: Deliberately short. "sống" and "làm" are *not* here, and that is the whole
#: invariant: they occur across several slots, and letting one of them decide
#: is what produced fourteen confident wrong answers. "lương" names one thing
#: and refusing it would be a different sort of error.
DISCRIMINATING: dict[str, Predicate] = {
    "lương": Predicate.SALARY,
    "thu nhập": Predicate.SALARY,
    "sinh nhật": Predicate.BIRTHDAY,
    "địa chỉ": Predicate.LOCATION,
    "chức vụ": Predicate.ROLE,
    "salary": Predicate.SALARY,
    "birthday": Predicate.BIRTHDAY,
    # Decisive *within this closed set of predicates*, which is the only claim
    # being made. English "work" could mean a project elsewhere; here PROJECT
    # is reached through "dự án"/"project" and nothing else competes for it.
    # A wider ontology would have to re-examine this entry rather than inherit
    # it.
    "work": Predicate.EMPLOYER,
}

#: Below this, the resolver says UNKNOWN rather than picking a side.
MIN_CONFIDENCE = 0.5

_TITLES = {"anh", "chị", "em", "ông", "bà", "cô", "chú", "bác",
           "mr", "mrs", "ms"}
_STOP = {
    "vào", "ngày", "tháng", "năm", "hồi", "lúc", "khi", "thì", "là", "gì",
    "nào", "đâu", "ở", "của", "đang", "hiện", "tại", "bây", "giờ", "trước",
    "sau", "đã", "từng", "và", "có", "cho", "rằng", "sang", "rời", "một",
    "the", "was", "is", "did", "does", "what", "where", "which", "who",
    "in", "on", "at", "of", "do", "his", "her", "their",
}

_BEFORE = re.compile(r"(trước khi|trước lúc|before\b)", re.IGNORECASE)
_AFTER = re.compile(r"(sau khi|sau lúc|after\b)", re.IGNORECASE)
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MY = re.compile(r"tháng\s*(\d{1,2})\s*[/-]?\s*(\d{4})", re.IGNORECASE)
_HISTORICAL = re.compile(
    r"(vào ngày|hồi|trước đây|lúc trước|trước khi|sau khi|đã từng"
    r"|\bwas\b|\bused to\b)", re.IGNORECASE)


@dataclass
class AspectResolution:
    predicate: Predicate = Predicate.UNKNOWN
    confidence: float = 0.0
    method: str = ""
    evidence: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class QueryFrame:
    subject: str | None = None
    predicate: Predicate = Predicate.UNKNOWN
    when: str | None = None
    anchor: str | None = None
    query_type: str = "current"
    aspect: AspectResolution = field(default_factory=AspectResolution)


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


_VOCABULARY: set[str] = set()


def _ontology_words() -> set[str]:
    """Every word this resolver recognises. Computed once."""
    if not _VOCABULARY:
        for phrases in ONTOLOGY.values():
            for phrase in phrases:
                _VOCABULARY.update(w for w in re.findall(r"\w+", phrase.lower())
                                   if w not in _STOP)
        _VOCABULARY.update(w for token in DISCRIMINATING
                           for w in token.split())
    return _VOCABULARY


def _names(text: str) -> list[str]:
    """Runs of capitalised words — every proper name in the question.

    Both the subject and any anchor are names, and both must be kept out of
    the aspect. "Trước khi sang Bình Minh thì Bùi Cường làm ở đâu" once left an
    aspect of "bình minh làm", which matched a birthday memory.

    A lone capital that opens the sentence is not a name. "Lương của Bùi Cường
    là bao nhiêu?" and "Địa chỉ của Bùi Cường?" begin with ordinary words, and
    stripping them as names left nothing to resolve from — the resolver then
    said UNKNOWN to four questions it should have answered outright.

    A single capitalised word anywhere is ambiguous between a name and a
    sentence opening, which is the same shape as the single-token aspect this
    module refuses to decide on. Runs of two or more are names; a lone capital
    counts only when something else already started the sentence.
    """
    vocabulary = _ontology_words()
    tokens = re.findall(r"[\w]+", _fold(text), re.UNICODE)
    runs: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    for index, token in enumerate(tokens):
        low = token.lower()
        # A Vietnamese name is family name then given name, and a sentence
        # that opens mid-phrase lowercases the first of them: "Từ hôm nay,
        # phạm Vy làm việc tại …". Requiring an uppercase token to *start* a
        # run cut that name to "Vy" — one person stored under two identities,
        # on 20 of the world's 35 sentence templates.
        #
        # A lowercase token joins a name when the token after it is
        # capitalised and it is not a stopword, a title or ontology
        # vocabulary. That is the family-name-then-given-name shape itself,
        # not a rule about this corpus: "phạm Vy" reads as a name because
        # "Vy" is capitalised and "phạm" is nothing else.
        joins_a_name = (
            not token[:1].isupper()
            and index + 1 < len(tokens)
            and tokens[index + 1][:1].isupper()
            and tokens[index + 1].lower() not in vocabulary
            and low not in vocabulary and low not in _TITLES
            and low not in _STOP and len(token) > 1
            # A digit is never a name syllable. Without this, "ngày 21 Phạm
            # Vy…" read "21" as a family name and the subject became
            # "21 Phạm Vy" — a new identity per date, which is the split this
            # module exists to close, arriving through the fix for it.
            and not any(ch.isdigit() for ch in token)
        )
        if joins_a_name:
            if not current:
                start = index
            current.append(token)
            continue
        # A word this resolver already knows is never a name. "Lương của Đặng
        # Phúc là bao nhiêu?" capitalises "Lương" because it opens the
        # template, and prefixing a date pushed it mid-sentence where the
        # position rule no longer protected it — the aspect was then stripped
        # away as though it were somebody's surname.
        if low in vocabulary:
            if current:
                runs.append((start, current))
            current = []
            continue
        if token[:1].isupper() and low not in _TITLES and low not in _STOP:
            if not current:
                start = index
            current.append(token)
        else:
            if current:
                runs.append((start, current))
            current = []
    if current:
        runs.append((start, current))
    return [" ".join(words) for position, words in runs
            if len(words) >= 2 or position > 0]


def _strip_names_and_dates(text: str) -> str:
    stripped = text
    for name in _names(text):
        stripped = stripped.replace(name, " ")
    stripped = _ISO.sub(" ", stripped)
    stripped = _DMY.sub(" ", stripped)
    stripped = _MY.sub(" ", stripped)
    return _fold(stripped)


def resolve_aspect(question: str) -> AspectResolution:
    """Which predicate this question is about, or UNKNOWN.

    Names and dates come out first — they are never the aspect, and leaving
    them in is how a company name became one.
    """
    text = _strip_names_and_dates(_fold(question)).lower()
    if not text.strip():
        return AspectResolution(note="câu hỏi không còn chữ nào sau khi bỏ "
                                     "tên riêng và ngày tháng")

    # 1. an explicit alias, longest first so "sống ở đâu" beats "ở đâu".
    hits: list[tuple[int, Predicate, str]] = []
    for predicate, phrases in ONTOLOGY.items():
        for phrase in phrases:
            if " " not in phrase and "." not in phrase:
                continue                       # single tokens handled below
            if re.search(phrase, text) if "." in phrase else phrase in text:
                hits.append((len(phrase), predicate, phrase))
    if hits:
        hits.sort(reverse=True)
        _, predicate, phrase = hits[0]
        rivals = {p for _, p, _ in hits}
        confidence = 0.95 if len(rivals) == 1 else 0.75
        return AspectResolution(predicate, confidence, "alias", [phrase])

    # 2. a single token the ontology calls decisive.
    for token, predicate in DISCRIMINATING.items():
        if token in text:
            return AspectResolution(predicate, 0.8, "discriminating", [token])

    # 3. token overlap with an alias, needing more than one word to count.
    words = {w for w in re.findall(r"\w+", text, re.UNICODE)
             if w not in _STOP and len(w) > 1}
    scored: list[tuple[float, Predicate, list[str]]] = []
    for predicate, phrases in ONTOLOGY.items():
        shared: set[str] = set()
        for phrase in phrases:
            shared |= words & set(phrase.split())
        if len(shared) >= 2:
            scored.append((len(shared), predicate, sorted(shared)))
    if scored:
        scored.sort(reverse=True)
        best, predicate, shared = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0
        if best > runner:
            return AspectResolution(predicate, 0.6, "overlap", shared)
        return AspectResolution(
            note=f"nhiều vị từ cùng khớp {shared} — không đủ để chọn",
            evidence=shared)

    # 4. UNKNOWN. A single common word never decides.
    return AspectResolution(
        note=f"không đủ tín hiệu để xác định vị từ từ {sorted(words)!r}; "
             f"một từ thông dụng đơn lẻ không phân biệt được slot")


def resolve_frame(question: str) -> QueryFrame:
    """Subject, predicate, time — everything needed to ask the store."""
    text = _fold(question)
    names = _names(text)

    anchor = None
    if _BEFORE.search(text) or _AFTER.search(text):
        marker = _BEFORE.search(text) or _AFTER.search(text)
        tail_names = _names(text[marker.end():])
        anchor = tail_names[0] if tail_names else None

    subject = next((n for n in names if n != anchor), None)

    when = None
    match = _ISO.search(text)
    if match:
        when = f"{match.group(1)}-{match.group(2)}-{match.group(3)}T00:00:00+00:00"
    else:
        match = _DMY.search(text)
        if match:
            day, month, year = match.groups()
            when = f"{year}-{int(month):02d}-{int(day):02d}T00:00:00+00:00"
        else:
            match = _MY.search(text)
            if match:
                month, year = match.groups()
                when = f"{year}-{int(month):02d}-01T00:00:00+00:00"

    aspect = resolve_aspect(question)
    query_type = "historical" if (when or _HISTORICAL.search(text)) else "current"
    return QueryFrame(subject=subject, predicate=aspect.predicate, when=when,
                      anchor=anchor, query_type=query_type, aspect=aspect)


__all__ = [
    "AspectResolution",
    "DISCRIMINATING",
    "MIN_CONFIDENCE",
    "ONTOLOGY",
    "Predicate",
    "QueryFrame",
    "resolve_aspect",
    "resolve_frame",
]
