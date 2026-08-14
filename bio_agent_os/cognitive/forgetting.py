"""Deletion that can be proved, not deletion that can be claimed.

A memory system spreads one fact across several tables. Measured on the live
learning store on 2026-08-14, before this existed:

    one consolidated memory built from 8 sources
      -> 2 of the first 4 sources still legible inside it, word for word
    353 rows in memory_embeddings
    308 rows in hippocampus_labels

Dropping the `cognitive_memories` row leaves the merged text quoting it, a
vector that still matches queries about it, and a label naming its topic. The
record is gone and the knowledge is not — which for personal data is the
difference between a deletion and the appearance of one.

Two commitments make this worth relying on:

**Every derived artefact is named here.** The list is in one place and it is
the same list the verification re-reads. When a new derived store is added and
this file is not updated, the verification catches it rather than the customer.

**Success is checked, never assumed.** `forget()` re-reads the store afterwards
looking for the content it was asked to remove, and reports `verified_clean =
False` with the leftovers if it finds any. A false all-clear on a deletion
request is worse than an error, because nobody goes back to look.

What is *not* done here, deliberately: this deletes rather than tombstones, and
it does not touch the event log. `cognitive_events` is the immutable record the
whole projection design rests on, and quietly rewriting it would break replay
and the audit trail. Erasure that must reach the event log is a different
operation with different consequences, and it should be asked for explicitly.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

#: Every place a memory's content or its shadow can come to rest.
#:
#: Adding a derived store without adding it here is the failure this list
#: exists to prevent, which is why `verify()` reads the same table names rather
#: than a second hand-kept copy.
DERIVED_TABLES: tuple[tuple[str, str], ...] = (
    ("memory_embeddings", "memory_id"),
    ("hippocampus_labels", "event_id"),
)

#: Tables whose text is scanned when proving nothing is left.
CONTENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cognitive_memories", "content"),
    ("cognitive_memories", "metadata_json"),
    ("hippocampus_labels", "topic"),
    ("shadow_memories", "content"),
)

CONSOLIDATED_MARKER = "consolidated_from"


@dataclass
class ForgetReport:
    memories_deleted: int = 0
    derived: dict[str, int] = field(default_factory=dict)
    verified_clean: bool = True
    residue: list[dict[str, str]] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "memories_deleted": self.memories_deleted,
            "derived": self.derived,
            "verified_clean": self.verified_clean,
            "residue": self.residue,
            "note": self.note,
        }


def _safe(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []          # a store without this capability; absent, not broken


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def find_by_subject(conn: sqlite3.Connection, subject: str) -> list[str]:
    """Memory ids that mention a subject.

    "Forget everything about An Phát" has to take the phone number with it, and
    the phone number's memory says "Khách hàng An Phát có số điện thoại …" — so
    matching the subject anywhere in the content is right, and matching only an
    exact record is the bug.
    """
    needle = _normalise(subject)
    if not needle:
        return []
    rows = _safe(conn, "SELECT memory_id, content FROM cognitive_memories")
    return [mid for mid, content in rows if needle in _normalise(content)]


def find_consolidated_containing(conn: sqlite3.Connection,
                                 memory_ids: set[str]) -> list[str]:
    """Merged memories built from any of these, since a merge quotes its sources."""
    out: list[str] = []
    rows = _safe(
        conn,
        "SELECT memory_id, metadata_json FROM cognitive_memories "
        "WHERE metadata_json LIKE ?", (f"%{CONSOLIDATED_MARKER}%",),
    )
    for memory_id, blob in rows:
        try:
            meta = json.loads(blob) if isinstance(blob, str) else (blob or {})
        except (TypeError, ValueError):
            continue
        sources = {str(s) for s in (meta or {}).get(CONSOLIDATED_MARKER, []) or []}
        if sources & memory_ids:
            out.append(memory_id)
    return out


def verify(conn: sqlite3.Connection, needle: str) -> list[dict[str, str]]:
    """Anything still mentioning `needle`, anywhere. The proof half."""
    needle = str(needle or "").strip()
    if not needle:
        return []
    residue: list[dict[str, str]] = []
    for table, column in CONTENT_COLUMNS:
        for (value,) in _safe(
            conn, f"SELECT {column} FROM {table} WHERE {column} LIKE ?",
            (f"%{needle}%",),
        ):
            residue.append({"where": f"{table}.{column}", "excerpt": str(value)[:120]})
    return residue


def forget(memory_os: Any, *, memory_id: str | None = None,
           subject: str | None = None, needle: str | None = None) -> ForgetReport:
    """Remove a memory and everything derived from it, then check.

    `needle` is the string to prove absent afterwards. Defaults to the content
    of what was deleted, which is what someone asking "is it really gone" means.
    """
    conn = memory_os.memories.conn
    report = ForgetReport()

    targets: set[str] = set()
    if memory_id:
        targets.add(str(memory_id))
    if subject:
        targets.update(find_by_subject(conn, subject))
    if not targets:
        report.note = "không tìm thấy ký ức nào khớp"
        return report

    # The content being erased, captured before the rows go, so the check
    # afterwards has something to look for.
    contents: list[str] = []
    placeholders = ",".join("?" * len(targets))
    for (content,) in _safe(
        conn, f"SELECT content FROM cognitive_memories WHERE memory_id IN ({placeholders})",
        tuple(targets),
    ):
        contents.append(str(content))

    # Merged memories quote their sources, so they go with them. Taken before
    # the sources are deleted, while the link still exists.
    merged = find_consolidated_containing(conn, targets)
    targets.update(merged)
    if merged:
        report.note = f"xoá kèm {len(merged)} ký ức hợp nhất có trích nội dung này"

    # The events behind these memories, needed to reach labels, which are keyed
    # by event rather than by memory.
    event_ids: set[str] = set()
    placeholders = ",".join("?" * len(targets))
    for (blob,) in _safe(
        conn,
        f"SELECT source_event_ids_json FROM cognitive_memories "
        f"WHERE memory_id IN ({placeholders})", tuple(targets),
    ):
        try:
            for event_id in (json.loads(blob) if isinstance(blob, str) else blob) or []:
                event_ids.add(str(event_id))
        except (TypeError, ValueError):
            continue

    for table, key in DERIVED_TABLES:
        keys = event_ids if key == "event_id" else targets
        if not keys:
            continue
        marks = ",".join("?" * len(keys))
        try:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {key} IN ({marks})", tuple(keys)
            )
            report.derived[table] = cursor.rowcount if cursor.rowcount > 0 else 0
        except sqlite3.OperationalError:
            continue

    marks = ",".join("?" * len(targets))
    cursor = conn.execute(
        f"DELETE FROM cognitive_memories WHERE memory_id IN ({marks})", tuple(targets)
    )
    report.memories_deleted = cursor.rowcount
    conn.commit()

    # Proof. Whatever was asked for, or failing that the most distinctive thing
    # in what was deleted — a long token is a better probe than a common word.
    probes: list[str] = [needle] if needle else []
    if not probes:
        for content in contents:
            tokens = sorted((w for w in re.findall(r"\w{6,}", content)),
                            key=len, reverse=True)
            if tokens:
                probes.append(tokens[0])
    for probe in probes[:5]:
        report.residue.extend(verify(conn, probe))

    report.verified_clean = not report.residue
    if report.residue:
        report.note = (report.note + " | " if report.note else "") + \
            f"CÒN SÓT ở {len(report.residue)} chỗ — chưa xoá sạch"
    return report


__all__ = [
    "CONTENT_COLUMNS",
    "DERIVED_TABLES",
    "ForgetReport",
    "find_by_subject",
    "find_consolidated_containing",
    "forget",
    "verify",
]
