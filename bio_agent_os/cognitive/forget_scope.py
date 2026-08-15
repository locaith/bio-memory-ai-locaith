"""What does a deletion request actually cover?

Measured on the first real lifetime run, 15/08/2026:

    yêu cầu xoá đã THỰC SỰ xoá được gì: 3/18
    forgotten (truy xuất): 0.000 ở mọi mốc

Fifteen of eighteen requests matched nothing. Not a threshold problem:
`forget_derived(subject=...)` looks for a literal substring, and "Hãy quên
thông tin về city của Trần An" is not a substring of "Trần An sống ở Hà Nội".
The request describes a *scope*; the deleter was hunting a string.

A scope has a shape:

    memory_id           this exact memory
    subject             everything about a person
    subject + topic     one aspect of a person
    topic               everything on a subject matter
    time_range          everything from a period

and a request is resolved into one before anything is deleted:

    request -> resolve -> preview -> execute -> purge derived
            -> verify -> result

**Topic scoping is semantic, and has to be.** "Quên thông tin sức khoẻ của anh
Hùng" must take "Anh Hùng có tiền sử bệnh tim", which never contains the words
"sức khoẻ". No amount of word overlap reaches that, which is why `forget-002`
has been xfailed since this morning. The embedder already in the store does
reach it — injected, never imported here, because `cognitive/` must keep
working with no model available.

**No match is not success.** The rule this module exists to enforce. A request
that matched nothing reports `NO_MATCH`, and `succeeded` is False. Two separate
questions:

    verified_clean  is the content gone from the store
    succeeded       did this request do anything

A store is trivially "clean" of content it never found. Collapsing the two is
how "we found nothing to delete" comes to read as "we deleted it" — the exact
shape of the harness defect that made this project's forgetting scores
meaningless for a day.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Words that appear in every deletion request and in no memory. Counting them
#: as content is what put the word-overlap matcher below its own threshold on
#: fifteen of eighteen requests.
_BOILERPLATE = {
    "hãy", "xin", "làm", "ơn", "quên", "xoá", "xóa", "bỏ", "huỷ", "hủy",
    "thông", "tin", "dữ", "liệu", "mọi", "hết", "tất", "cả", "về", "của",
    "liên", "quan", "tới", "đến", "đi", "cái", "chuyện", "thứ", "những",
    "please", "forget", "delete", "remove", "erase", "all", "about",
    "everything", "data", "information", "the", "my", "any",
}

#: Titles that precede a name and are not part of it.
_TITLES = {"anh", "chị", "em", "ông", "bà", "cô", "chú", "bác", "khách", "hàng",
           "mr", "mrs", "ms", "sir"}

#: Where the topic half of a scope tends to sit, and a hint that one exists.
_TOPIC_MARKERS = ("về", "liên quan tới", "thuộc", "phần")


class ForgetStatus(str, Enum):
    DELETED = "deleted"
    NO_MATCH = "no_match"
    PARTIAL = "partial"      # deleted something, residue remains


@dataclass
class ForgetScope:
    memory_id: str | None = None
    subject: str | None = None
    topic: str | None = None
    since: str | None = None
    until: str | None = None

    def is_empty(self) -> bool:
        return not any((self.memory_id, self.subject, self.topic,
                        self.since, self.until))

    def describe(self) -> str:
        bits = []
        if self.memory_id:
            bits.append(f"ký ức {self.memory_id[:8]}")
        if self.subject:
            bits.append(f"chủ thể '{self.subject}'")
        if self.topic:
            bits.append(f"chủ đề '{self.topic}'")
        if self.since or self.until:
            bits.append(f"khoảng {self.since or '…'}–{self.until or '…'}")
        return ", ".join(bits) or "(không xác định)"


@dataclass
class Match:
    memory_id: str
    content: str
    why: str


@dataclass
class ForgetResult:
    status: ForgetStatus = ForgetStatus.NO_MATCH
    scope: ForgetScope = field(default_factory=ForgetScope)
    matched_claims: int = 0
    deleted_claims: int = 0
    derived_invalidated: int = 0
    #: How many probes were actually searched for afterwards.
    #:
    #: Carried over from `ForgetReport` deliberately. Zero checks must never
    #: read as a clean store — that guarantee was won this morning after
    #: `_probes()` was measured producing nothing at all for four of six
    #: ordinary Vietnamese sentences, and a new result type that quietly
    #: dropped the field would give the hole straight back.
    checks_run: int = 0
    residue: list[dict[str, str]] = field(default_factory=list)
    unmatched_reason: str = ""

    @property
    def verified_clean(self) -> bool:
        if self.checks_run <= 0:
            return False
        return not self.residue

    @property
    def succeeded(self) -> bool:
        """Did this request do anything?

        Deliberately not `verified_clean`. A store contains nothing of what it
        never found, so a request that matched nothing would otherwise report a
        clean bill of health and read as a completed deletion.
        """
        return self.status is ForgetStatus.DELETED and self.deleted_claims > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "scope": self.scope.describe(),
            "matched_claims": self.matched_claims,
            "deleted_claims": self.deleted_claims,
            "derived_invalidated": self.derived_invalidated,
            "checks_run": self.checks_run,
            "verified_clean": self.verified_clean,
            "residue": self.residue,
            "unmatched_reason": self.unmatched_reason,
            "succeeded": self.succeeded,
        }


# --------------------------------------------------------------------------
# resolving
# --------------------------------------------------------------------------

def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", _fold(text).lower(), re.UNICODE)]


def _is_name_word(word: str) -> bool:
    """A capitalised word that is not a title or boilerplate.

    Vietnamese names are capitalised per syllable ("Trần An", "An Phát"), so a
    run of capitalised words is a name.
    """
    return (word[:1].isupper() and word.lower() not in _TITLES
            and word.lower() not in _BOILERPLATE)


def resolve(request: str) -> ForgetScope:
    """Turn "quên thông tin sức khoẻ của anh Hùng" into a scope.

    The subject is the longest run of capitalised words. The topic is whatever
    content words are left after boilerplate and the subject are removed — and
    only when a marker like "về" says an aspect was named at all, so that
    "quên hết dữ liệu khách hàng An Phát" scopes to the person rather than
    inventing a topic out of the leftovers.
    """
    text = _fold(request)
    tokens = re.findall(r"[\w./-]+", text, re.UNICODE)

    runs: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if _is_name_word(token):
            current.append(token)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    subject = " ".join(max(runs, key=len)) if runs else None

    # Whatever survives boilerplate, titles and the subject *is* the topic.
    #
    # An earlier version also required a marker word like "về", and it dropped
    # the topic from "quên số điện thoại của An Phát đi" — a request that names
    # an aspect perfectly clearly without one. The leftovers already carry the
    # distinction: "quên hết dữ liệu khách hàng An Phát" leaves nothing once
    # "dữ liệu" and "khách hàng" are recognised, so it scopes to the person.
    subject_words = set(_words(subject or ""))
    leftovers = [w for w in _words(text)
                 if w not in _BOILERPLATE and w not in subject_words
                 and w not in _TITLES and len(w) > 1]
    topic = " ".join(leftovers) if leftovers else None

    return ForgetScope(subject=subject, topic=topic)


# --------------------------------------------------------------------------
# previewing
# --------------------------------------------------------------------------

def _mentions(content: str, subject: str) -> bool:
    """Does this memory talk about that subject?

    Every syllable of the name must appear. "Trần An" matching a memory about
    "An Phát" because they share "An" would delete a second person's records on
    the first person's request.
    """
    low = _fold(content).lower()
    return all(part in low for part in _words(subject))


#: Below this, nothing in the candidate set is about the topic at all.
#:
#: Only a sanity check, not the selection rule. Its job is to answer "does this
#: person have *any* memories about this aspect", so that a request to forget
#: something they never told us deletes nothing rather than deleting whatever
#: happened to rank highest.
MIN_TOPIC_SIMILARITY = 0.25

#: A memory belongs to the topic when it scores within this fraction of the
#: best match in the same candidate set.
TOPIC_RELATIVE = 0.6


def _topic_members(scores: dict[str, float]) -> set[str]:
    """Which of these memories are about the topic.

    Relative, and it has to be. The first version borrowed the *retrieval*
    floor, which `calibrate_with_probes` derives from off-topic questions
    against whole memories. Comparing a two-word topic label to a sentence is a
    different measurement on a different scale, and the borrowed threshold
    rejected everything. Measured, on one person's five memories:

        chủ đề "chức vụ"  vs  "… đang giữ chức nhân viên kinh doanh"   0.329
                          vs  "Nhắc lại, … đang giữ chức …"            0.414
                          vs  "… sống ở Đà Nẵng."                      0.123
                          vs  "… sinh ngày 30/01/1992."               -0.008
        sàn truy xuất = 0.5189  ->  không cái nào qua

    The ranking was already perfect — the job memories separate from the rest
    by more than a factor of two. What was wrong was asking an absolute
    question of an inherently comparative one: "which of this person's memories
    are about X" is answered by the gap, not by a fixed number.

    Same statements-versus-questions confusion that once put a calibrated floor
    at 0.950 and recall at 0/38. Different symptom, same root.
    """
    if not scores:
        return set()
    best = max(scores.values())
    if best < MIN_TOPIC_SIMILARITY:
        return set()                 # nothing here is about that topic
    cut = best * TOPIC_RELATIVE
    return {memory_id for memory_id, score in scores.items() if score >= cut}


def preview(memory_os: Any, scope: ForgetScope) -> list[Match]:
    """What this scope covers, without deleting any of it.

    A deletion whose extent cannot be inspected beforehand is one nobody can
    approve.
    """
    if scope.is_empty():
        return []

    conn = memory_os.memories.conn
    rows = conn.execute(
        "SELECT memory_id, content, observed_at FROM cognitive_memories"
    ).fetchall()

    if scope.memory_id:
        return [Match(m, c, "chỉ định trực tiếp")
                for m, c, _ in rows if m == scope.memory_id]

    candidates: list[tuple[str, str]] = []
    for memory_id, content, observed_at in rows:
        if scope.subject and not _mentions(content, scope.subject):
            continue
        if scope.since and str(observed_at or "") < scope.since:
            continue
        if scope.until and str(observed_at or "") > scope.until:
            continue
        candidates.append((memory_id, content))

    if not scope.topic:
        return [Match(m, c, f"thuộc chủ thể '{scope.subject}'")
                for m, c in candidates]

    # Topic scoping is semantic. "Sức khoẻ" has to reach "tiền sử bệnh tim",
    # which shares no word with it — the reason `forget-002` could not be
    # satisfied by any word-overlap rule.
    embedder = getattr(getattr(memory_os, "retrieval", None), "embedder", None)
    if embedder is None:
        needles = [w for w in _words(scope.topic) if len(w) > 2]
        return [Match(m, c, "khớp từ (không có embedder)")
                for m, c in candidates
                if any(n in _fold(c).lower() for n in needles)]

    from .semantic_index import cosine, load_vectors

    topic_vector = embedder.embed(scope.topic)
    vectors = load_vectors(conn, [m for m, _ in candidates],
                           dims=len(topic_vector) or None)

    scores = {memory_id: cosine(topic_vector, vectors[memory_id])
              for memory_id, _ in candidates if memory_id in vectors}
    members = _topic_members(scores)
    best = max(scores.values()) if scores else 0.0

    return [Match(memory_id, content,
                  f"chủ đề '{scope.topic}' ({scores[memory_id]:.3f}, "
                  f"cao nhất {best:.3f})")
            for memory_id, content in candidates if memory_id in members]


# --------------------------------------------------------------------------
# executing
# --------------------------------------------------------------------------

def forget_scoped(memory_os: Any, request: str, *, actor: str,
                  scope: ForgetScope | None = None) -> ForgetResult:
    """Resolve, preview, delete, purge, verify. Report all of it.

    `actor` is required: an irreversible-feeling operation with no record of
    who asked is not auditable.
    """
    if not str(actor).strip():
        raise ValueError("xoá theo phạm vi phải ghi rõ actor")

    from .forgetting import forget_derived

    scope = scope or resolve(request)
    result = ForgetResult(scope=scope)

    if scope.is_empty():
        result.unmatched_reason = (
            f"không rút được phạm vi nào từ yêu cầu {request!r} — không có tên "
            f"riêng, chủ đề hay khoảng thời gian nào để bám vào")
        return result

    matched = preview(memory_os, scope)
    result.matched_claims = len(matched)
    if not matched:
        result.unmatched_reason = (
            f"phạm vi {scope.describe()} không khớp ký ức nào. Đây KHÔNG phải "
            f"là đã xoá xong — không tìm thấy gì để xoá là một kết quả khác.")
        return result

    probes = [m.content for m in matched]
    for match in matched:
        report = forget_derived(memory_os, memory_id=match.memory_id)
        result.deleted_claims += report.memories_deleted
        result.derived_invalidated += sum(report.derived.values())

    # Verified against the content that was matched, not against the request —
    # the request's words never appear in a memory, so verifying on them would
    # pass trivially every time.
    from .forgetting import verify

    used = [p for p in probes[:12] if str(p).strip()]
    result.checks_run = len(used)
    for probe in used:
        result.residue.extend(verify(memory_os.memories.conn, probe))

    if result.deleted_claims == 0:
        result.status = ForgetStatus.NO_MATCH
        result.unmatched_reason = "khớp được nhưng không xoá được dòng nào"
    elif result.residue:
        result.status = ForgetStatus.PARTIAL
        result.unmatched_reason = f"còn sót ở {len(result.residue)} chỗ"
    else:
        result.status = ForgetStatus.DELETED

    # RULE_002 runs on the real path, not only in a test.
    #
    # A constitution that lives entirely in its own test file constrains
    # nothing: the rule holds until someone changes this function, and the
    # only thing that notices is a suite nobody runs before shipping. Gating
    # here means a report that claims clean without probing cannot leave.
    _check_constitution(memory_os, result, actor=actor)
    return result


def _check_constitution(memory_os: Any, result: ForgetResult, *,
                        actor: str) -> None:
    """Record and enforce the laws that apply to a deletion.

    Wrapped so a missing constitution package cannot break deletion itself —
    but a law that *ran* and refused is allowed to raise, which is the point of
    a gate.
    """
    try:
        from bio_agent_os.memory_constitution import gate
        from bio_agent_os.memory_constitution.laws import (
            check_memory_not_evidence,
        )
    except ImportError:            # pragma: no cover - package always present
        return

    gate(memory_os.memories.conn, [check_memory_not_evidence(result)],
         operation="forget_scoped", actor=actor)


__all__ = [
    "ForgetResult",
    "ForgetScope",
    "ForgetStatus",
    "Match",
    "forget_scoped",
    "preview",
    "resolve",
]
