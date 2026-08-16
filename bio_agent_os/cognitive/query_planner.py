"""Not every question is a topic. Some name a kind of memory.

Measured on the live learning store on 2026-08-15, against a calibrated floor
of 0.3547:

    "hallucination của LLM là gì"         cao nhất 0.8013   vượt sàn 5/413
    "tôi từng làm sai câu nào"            cao nhất 0.2795   vượt sàn 0/413
    "điểm yếu của tôi là gì cần ôn lại"   cao nhất 0.2918   vượt sàn 0/413

Both self-referential questions return nothing. The tempting fix is to lower
the floor, and it is wrong twice over. The 0.2918 hit *was* the right memory,
so the ranking already worked; and admitting it would also admit four hundred
topically-similar memories, replacing an honest empty answer with a confident
wrong one.

The actual reason, measured before any of this was written:

    413 memories, every one of memory_type `semantic`
    metadata empty except on the 45 consolidated rows
    24 memories reading "Làm sai câu" / "Điểm yếu tự ghi nhận"
    0 of those 24 carrying any class label

The question asks for a class of memory, and the class was never recorded. No
similarity threshold reaches that, because similarity is not the relation being
asked about: "tôi từng làm sai gì" is about how a group of memories relates to
the person, not about what they are on the topic of.

So this module does two things and deliberately not more:

* **classify the question**, by rule, deterministically. A model call per read
  costs money on every query and returns something different next Tuesday;
* **route a class-seeking question to a structured selection**, which has no
  cosine in it at all.

Ordinary content questions go through `recall()` untouched. The retrieval path
that nine canary runs hardened is not replaced by a second one that happens to
be newer.

When the class was never recorded, the honest answer is still nothing — but
with a reason attached. The old failure was not the empty result; it was that
nobody was told why it was empty.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: The key a memory's class is stored under, in its metadata.
CLASS_KEY = "memory_class"


class MemoryClass(str, Enum):
    """What a memory *is*, as opposed to what it is about."""

    LESSON_FACT = "lesson_fact"
    MISTAKE = "mistake"
    WEAKNESS = "weakness"
    SCORE = "score"
    COMPLETION = "completion"
    PROGRESS = "progress"


class QueryKind(str, Enum):
    CONTENT = "content"
    SELF_HISTORY = "self_history"
    TEMPORAL = "temporal"
    META = "meta"


#: Questions about the asker's own record. The marker is a first-person
#: pronoun near a word about erring or weakness — "sai" alone is far too common
#: ("câu nào sai trong các câu sau" is a content question).
_SELF = r"(tôi|anh|mình|em|my|i|me)"
_SELF_HISTORY = re.compile(
    rf"\b{_SELF}\b[^.?!]{{0,40}}"
    r"(sai|nhầm|lỗi|thiếu sót|điểm yếu|yếu|hay quên|chưa nắm|trượt"
    r"|wrong|mistake|weak|fail|miss)"
    r"|(điểm yếu|lỗi|sai|mistake|weakness)[^.?!]{0,40}"
    rf"\b{_SELF}\b",
    re.IGNORECASE,
)

#: Questions about the memory rather than about the world.
_META = re.compile(
    r"(thông tin nào.*(cũ|lỗi thời|hết hạn)|(cũ|lỗi thời).*(thông tin|dữ liệu)"
    r"|biết (những )?gì về|nhớ (những )?gì về|đổi ý|thay đổi quan điểm"
    r"|what do you (know|remember) about|how old is|changed my mind"
    r"|bao nhiêu lần)",
    re.IGNORECASE,
)

#: "Was this ever true?" — a question about the whole history, not a moment.
#:
#: It needs its own marker because `as_of` filtering is exactly wrong for it.
#: A claim that was superseded is the *evidence* that something was once true,
#: and filtering to the asking moment removes it. Measured on the answer-layer
#: A/B of 16/08: the EVER family fell 0.4750 -> 0.4000, three questions, while
#: current and forgotten rose — a regression caused by the filter, not by the
#: lifecycle it was added to serve.
_EVER = re.compile(
    r"(từng|đã bao giờ|có bao giờ|trước giờ có"
    r"|\bever\b|has .{0,20}\bever\b|used to)",
    re.IGNORECASE,
)

#: Questions anchored to a past moment.
_TEMPORAL = re.compile(
    r"(hồi (tháng|năm|đó)|trước đây|lúc trước|khi đó|trước kia"
    r"|vào (ngày|tháng|năm)|tính đến (ngày|tháng)"
    r"|\b(in|on|during|back in)\s+(january|february|march|april|may|june|july"
    r"|august|september|october|november|december|\d{4})"
    r"|\bwas\b.{0,30}\bin\b\s+\w+"
    r"|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

#: Which classes answer which kind of question.
_CLASSES_FOR: dict[QueryKind, tuple[MemoryClass, ...]] = {
    QueryKind.SELF_HISTORY: (MemoryClass.MISTAKE, MemoryClass.WEAKNESS),
}


def classify(question: str) -> QueryKind:
    """What kind of question this is. Rule-based, deterministic, no model.

    Order matters: META is checked before SELF_HISTORY because "anh đã đổi ý
    về chuyện đó mấy lần" contains a first-person pronoun but asks about the
    memory's own history, not about mistakes.
    """
    text = str(question or "")
    if _META.search(text):
        return QueryKind.META
    if _SELF_HISTORY.search(text):
        return QueryKind.SELF_HISTORY
    if _TEMPORAL.search(text):
        return QueryKind.TEMPORAL
    return QueryKind.CONTENT


def class_of(memory: Any) -> str | None:
    meta = getattr(memory, "metadata", None) or {}
    value = meta.get(CLASS_KEY)
    return str(value) if value else None


def labelled_count(memory_os: Any) -> int:
    """How many memories carry a class at all.

    Used to tell "nothing of that kind exists" apart from "nothing was ever
    labelled", which are different answers and were previously the same one.
    """
    try:
        rows = memory_os.memories.conn.execute(
            f"SELECT COUNT(*) FROM cognitive_memories "
            f"WHERE metadata_json LIKE ?", (f'%"{CLASS_KEY}"%',)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(rows[0]) if rows else 0


def select_by_class(memory_os: Any, classes: list[MemoryClass], *,
                    context: Any, limit: int = 20) -> list[Any]:
    """Every memory of these classes, newest first. No similarity anywhere.

    The point is that topical resemblance plays no part: "what did I get
    wrong" wants the mistakes, not the memories that most resemble the phrase
    "làm sai".
    """
    wanted = {c.value if isinstance(c, MemoryClass) else str(c) for c in classes}
    if not wanted:
        return []

    tenant = getattr(context, "tenant_id", None)
    workspace = getattr(context, "workspace_id", None)
    sql = ("SELECT * FROM cognitive_memories WHERE superseded_at IS NULL "
           "AND metadata_json LIKE ?")
    params: list[Any] = [f'%"{CLASS_KEY}"%']
    if tenant:
        sql += " AND tenant_id=?"
        params.append(tenant)
    if workspace:
        sql += " AND (workspace_id=? OR workspace_id IS NULL)"
        params.append(workspace)
    sql += " ORDER BY observed_at DESC"

    conn = memory_os.memories.conn
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    out: list[Any] = []
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if str(meta.get(CLASS_KEY) or "") in wanted:
            out.append(memory_os.memories._row(row))
        if len(out) >= limit:
            break
    return out


def _as_memory(memory_os: Any, span: Any) -> Any:
    """The stored memory behind a temporal span, so callers keep one shape."""
    conn = memory_os.memories.conn
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM cognitive_memories WHERE memory_id=? "
        "ORDER BY version DESC LIMIT 1", (span.memory_id,)).fetchone()
    return memory_os.memories._row(row) if row is not None else span


@dataclass
class Plan:
    kind: QueryKind
    used: str
    memories: list[Any] = field(default_factory=list)
    classes: list[MemoryClass] = field(default_factory=list)
    unanswerable_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "used": self.used,
                "n": len(self.memories),
                "classes": [c.value for c in self.classes],
                "unanswerable_reason": self.unanswerable_reason}


def plan(memory_os: Any, question: str, *, context: Any,
         limit: int = 10, as_of: str | None = None) -> Plan:
    """Route the question, and say which route was taken.

    A router nobody can inspect is a router nobody can debug, so the choice is
    part of the result rather than a side effect.

    `as_of` filters retrieval to claims valid at that instant. Default None
    keeps every existing caller returning exactly what it returned before —
    and until something passes it, a lifecycle can close every validity window
    in the store and no answer changes. Measured: a first A/B closed 39
    windows and emptied 17, and scored identically to the arm that closed
    none, because nothing on the read path was looking.

    It is dropped for "was this ever true" questions. A superseded claim is
    precisely the evidence that something was once the case, and filtering to
    the asking moment throws it away. Measured on the answer-layer A/B of
    16/08: the EVER family fell three questions while current and forgotten
    rose, so the arm's total was unchanged and the cause was invisible in it.
    """
    kind = classify(question)
    if as_of and _EVER.search(question):
        as_of = None

    # A dated question goes to the temporal operator, not to vector search.
    #
    # This branch is the whole point: the classifier already recognised 29 of
    # 55 failed questions as TEMPORAL and every one of them fell through to
    # `recall`, because there was no route. Similarity cannot answer "who held
    # this job in January" — several memories are equally about the job and the
    # answer is chosen by *when*.
    if kind is QueryKind.TEMPORAL:
        from .temporal_operator import answer_temporal

        answer = answer_temporal(memory_os, question, context=context)
        if answer.executed:
            return Plan(kind=kind, used="temporal_operator",
                        memories=[_as_memory(memory_os, s) for s in answer.spans
                                  if s.memory_id in
                                  {p["memory_id"] for p in answer.provenance}])
        # Falling back is a reported decision, not a silent one: a question the
        # operator could not resolve is still better served by retrieval than
        # by nothing, but the run has to be able to count how often that
        # happened or `temporal_execution_accuracy` measures nothing.
        found = memory_os.recall(query=question, context=context, limit=limit,
                                 as_of=as_of)
        result = Plan(kind=kind, used="recall_after_temporal_failed",
                      memories=[r.memory for r in (found or [])])
        result.unanswerable_reason = f"{answer.stage_failed}: {answer.note}"
        return result

    classes = list(_CLASSES_FOR.get(kind, ()))

    if not classes:
        found = memory_os.recall(query=question, context=context, limit=limit,
                                 as_of=as_of)
        return Plan(kind=kind, used="recall",
                    memories=[r.memory for r in (found or [])])

    memories = select_by_class(memory_os, classes, context=context, limit=limit)
    result = Plan(kind=kind, used="select_by_class", memories=memories,
                  classes=classes)
    if not memories and labelled_count(memory_os) == 0:
        result.unanswerable_reason = (
            "Câu hỏi này hỏi theo LỚP ký ức, nhưng chưa ký ức nào trong kho "
            "được ghi lớp. Không phải không tìm thấy — là chưa từng ghi lại để "
            "mà tìm. Hạ ngưỡng tương đồng sẽ trả về ký ức giống chủ đề, tức "
            "một câu trả lời sai trông có vẻ đúng."
        )
    return result


__all__ = [
    "CLASS_KEY",
    "MemoryClass",
    "Plan",
    "QueryKind",
    "class_of",
    "classify",
    "labelled_count",
    "plan",
    "select_by_class",
]
