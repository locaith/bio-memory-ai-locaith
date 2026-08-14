"""Deletion that can be proved, not deletion that can be claimed.

A memory system spreads one fact across several tables. Measured on the live
learning store on 2026-08-14, before this existed:

    one consolidated memory built from 8 sources
      -> 2 of the first 4 sources still legible inside it, word for word
    353 rows in memory_embeddings
    308 rows in hippocampus_labels

Dropping the `cognitive_memories` row leaves the merged text quoting it, a
vector that still matches queries about it, and a label naming its topic. The
record is gone and the knowledge is not.

The first version of this file fixed that and then made a subtler mistake.
Measured on 2026-08-14, same day:

    forget()                                    -> verified_clean = True
    cognitive_events.payload_json               -> still holds the secret
    rebuild_version('cognitive_memory') + worker
                                                -> the secret is back, verbatim

The event log is append-only by design and by trigger, which is what makes
replay possible at all. So deleting a projection is *inherently* reversible —
and the report said "clean". A routine repair undid the deletion and nothing
complained.

Hence three named operations rather than one word doing three jobs:

============================================================================
 forget_projection   the `cognitive_memories` row. The system stops using it.
                     Vectors and labels stay. Reversible by replay.
 forget_derived      the above, plus embeddings, labels, and consolidated
                     memories quoting it. Reversible by replay.
 erase_history       the above, plus redaction of the event payload.
                     NOT reversible. Requires confirm, reason and actor.
============================================================================

Every report carries `reversible` and, when it is True, `reversible_via` names
where the content survives. A caller who needs real erasure sees it and
escalates rather than trusting a clean-looking report.

Three commitments make this worth relying on:

**Every derived artefact is named here.** One list, and it is the same list the
verification re-reads. Adding a derived store without updating this file is
caught by the verification rather than by the customer.

**Success is checked, never assumed.** Each operation re-reads the store
afterwards looking for what it was asked to remove, and reports
`verified_clean = False` with the leftovers if it finds any. A false all-clear
on a deletion request is worse than an error, because nobody goes back to look.

**A redaction can never be silent.** `erase_history` rewrites the payload but
leaves the checksum describing the payload that used to be there, so the hash
stops matching on purpose. `verify_event_integrity` treats a mismatch as
tampering unless an erasure record explains it — delete the record to hide the
erasure and it reads as tampering, which is the point.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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

#: The append-only log. Scanned separately because content surviving *here* is
#: not residue — it is the design — but it does mean the deletion is undoable,
#: and that has to be reported rather than discovered.
EVENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cognitive_events", "payload_json"),
    ("cognitive_events", "metadata_json"),
)

CONSOLIDATED_MARKER = "consolidated_from"

ERASURE_TABLE = "event_erasures"

#: The trigger that makes the event log append-only. `erase_history` drops it
#: for the length of one statement and puts back the definition it found, so a
#: change to the trigger in `event_store.py` cannot be quietly reverted here.
_NO_UPDATE_TRIGGER = "cognitive_events_no_update"


class Scope:
    """How far a deletion reached. Named, because "forget" alone never says."""

    PROJECTION = "projection"
    DERIVED = "derived"
    HISTORY = "history"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ForgetReport:
    scope: str = Scope.DERIVED
    memories_deleted: int = 0
    derived: dict[str, int] = field(default_factory=dict)
    events_redacted: int = 0
    verified_clean: bool = True
    residue: list[dict[str, str]] = field(default_factory=list)
    #: True when the content can be rebuilt from something this scope did not
    #: touch — almost always the event log.
    reversible: bool = False
    reversible_via: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "memories_deleted": self.memories_deleted,
            "derived": self.derived,
            "events_redacted": self.events_redacted,
            "verified_clean": self.verified_clean,
            "residue": self.residue,
            "reversible": self.reversible,
            "reversible_via": self.reversible_via,
            "note": self.note,
        }


@dataclass
class IntegrityVerdict:
    """Why a checksum stopped matching, which is a different question from
    whether it stopped matching."""

    checked: int = 0
    tampered: list[dict[str, str]] = field(default_factory=list)
    redacted: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.tampered

    def as_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "ok": self.ok,
                "tampered": self.tampered, "redacted": self.redacted}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _safe(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []          # a store without this capability; absent, not broken


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _events_conn(memory_os: Any) -> sqlite3.Connection:
    events = getattr(memory_os, "events", None)
    return getattr(events, "conn", None) or memory_os.memories.conn


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


def verify(conn: sqlite3.Connection, needle: str,
           columns: tuple[tuple[str, str], ...] = CONTENT_COLUMNS) -> list[dict[str, str]]:
    """Anything still mentioning `needle`, anywhere in `columns`. The proof half."""
    needle = str(needle or "").strip()
    if not needle:
        return []
    residue: list[dict[str, str]] = []
    for table, column in columns:
        for (value,) in _safe(
            conn, f"SELECT {column} FROM {table} WHERE {column} LIKE ?",
            (f"%{needle}%",),
        ):
            residue.append({"where": f"{table}.{column}", "excerpt": str(value)[:120]})
    return residue


def _probes(contents: list[str], needle: str | None) -> list[str]:
    """What to search for afterwards. A long token beats a common word."""
    if needle:
        return [needle]
    out: list[str] = []
    for content in contents:
        tokens = sorted(re.findall(r"\w{6,}", content), key=len, reverse=True)
        if tokens:
            out.append(tokens[0])
    return out[:5]


def _resolve(conn: sqlite3.Connection, memory_id: str | None,
             subject: str | None) -> set[str]:
    targets: set[str] = set()
    if memory_id:
        targets.add(str(memory_id))
    if subject:
        targets.update(find_by_subject(conn, subject))
    return targets


def _contents_of(conn: sqlite3.Connection, targets: set[str]) -> list[str]:
    if not targets:
        return []
    marks = ",".join("?" * len(targets))
    return [str(c) for (c,) in _safe(
        conn, f"SELECT content FROM cognitive_memories WHERE memory_id IN ({marks})",
        tuple(targets))]


def _event_ids_of(conn: sqlite3.Connection, targets: set[str]) -> set[str]:
    """Events behind these memories. Needed for labels, which are keyed by
    event, and for redaction, which is only about events."""
    if not targets:
        return set()
    out: set[str] = set()
    marks = ",".join("?" * len(targets))
    for (blob,) in _safe(
        conn,
        f"SELECT source_event_ids_json FROM cognitive_memories "
        f"WHERE memory_id IN ({marks})", tuple(targets),
    ):
        try:
            for event_id in (json.loads(blob) if isinstance(blob, str) else blob) or []:
                out.add(str(event_id))
        except (TypeError, ValueError):
            continue
    return out


def _delete_memories(conn: sqlite3.Connection, targets: set[str]) -> int:
    marks = ",".join("?" * len(targets))
    cursor = conn.execute(
        f"DELETE FROM cognitive_memories WHERE memory_id IN ({marks})", tuple(targets))
    return cursor.rowcount


def _note_reversibility(conn: sqlite3.Connection, report: ForgetReport,
                        probes: list[str]) -> None:
    """Say where the content survives outside this scope.

    Not residue and not a failure — an append-only log is the design. But a
    caller who is told "clean" and not told "and a replay brings it back" has
    been given the wrong impression, which is the defect this exists to fix.
    """
    seen: set[str] = set()
    for probe in probes:
        for hit in verify(conn, probe, EVENT_COLUMNS):
            seen.add(hit["where"])
    if seen:
        report.reversible = True
        report.reversible_via = sorted(seen)


# --------------------------------------------------------------------------
# level 1 — the projection only
# --------------------------------------------------------------------------

def forget_projection(memory_os: Any, *, memory_id: str | None = None,
                      subject: str | None = None,
                      needle: str | None = None) -> ForgetReport:
    """Delete the memory row and nothing else.

    For a memory that is *wrong* rather than unwanted: the system stops using
    it, and the vectors and labels remain. That leaves retrievable traces, so
    this reports `verified_clean = False` whenever it finds any — the narrow
    scope is the caller's choice, not a reason to soften the report.
    """
    conn = memory_os.memories.conn
    report = ForgetReport(scope=Scope.PROJECTION)

    targets = _resolve(conn, memory_id, subject)
    if not targets:
        report.note = "không tìm thấy ký ức nào khớp"
        return report

    contents = _contents_of(conn, targets)
    report.memories_deleted = _delete_memories(conn, targets)
    conn.commit()

    probes = _probes(contents, needle)
    for probe in probes:
        report.residue.extend(verify(conn, probe))

    # A vector keyed by a memory that no longer exists still retrieves.
    for table, key in DERIVED_TABLES:
        keys = targets if key == "memory_id" else set()
        if not keys:
            continue
        marks = ",".join("?" * len(keys))
        rows = _safe(conn, f"SELECT COUNT(*) FROM {table} WHERE {key} IN ({marks})",
                     tuple(keys))
        if rows and rows[0][0]:
            report.residue.append({"where": table,
                                   "excerpt": f"{rows[0][0]} dòng dẫn xuất còn lại"})

    report.verified_clean = not report.residue
    _note_reversibility(_events_conn(memory_os), report, probes)
    return report


# --------------------------------------------------------------------------
# level 2 — the projection and everything derived from it
# --------------------------------------------------------------------------

def forget_derived(memory_os: Any, *, memory_id: str | None = None,
                   subject: str | None = None,
                   needle: str | None = None) -> ForgetReport:
    """Remove a memory and everything derived from it, then check.

    `needle` is the string to prove absent afterwards. Defaults to the most
    distinctive token in what was deleted, which is what someone asking "is it
    really gone" means.

    Still reversible: the source events are untouched, so `reversible` comes
    back True and `reversible_via` says where.
    """
    conn = memory_os.memories.conn
    report = ForgetReport(scope=Scope.DERIVED)

    targets = _resolve(conn, memory_id, subject)
    if not targets:
        report.note = "không tìm thấy ký ức nào khớp"
        return report

    # Captured before the rows go, so the check afterwards has something to
    # look for.
    contents = _contents_of(conn, targets)

    # Merged memories quote their sources, so they go with them. Taken while
    # the link still exists.
    merged = find_consolidated_containing(conn, targets)
    targets.update(merged)
    if merged:
        report.note = f"xoá kèm {len(merged)} ký ức hợp nhất có trích nội dung này"

    event_ids = _event_ids_of(conn, targets)

    for table, key in DERIVED_TABLES:
        keys = event_ids if key == "event_id" else targets
        if not keys:
            continue
        marks = ",".join("?" * len(keys))
        try:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {key} IN ({marks})", tuple(keys))
            report.derived[table] = max(cursor.rowcount, 0)
        except sqlite3.OperationalError:
            continue

    report.memories_deleted = _delete_memories(conn, targets)
    conn.commit()

    probes = _probes(contents, needle)
    for probe in probes:
        report.residue.extend(verify(conn, probe))

    report.verified_clean = not report.residue
    if report.residue:
        report.note = (report.note + " | " if report.note else "") + \
            f"CÒN SÓT ở {len(report.residue)} chỗ — chưa xoá sạch"
    _note_reversibility(_events_conn(memory_os), report, probes)
    return report


#: What `forget` has always meant here. Kept because the CLI, the tests and any
#: caller outside this repository already use it, and renaming it out from
#: under them buys nothing.
forget = forget_derived


# --------------------------------------------------------------------------
# level 3 — redaction of the event payload
# --------------------------------------------------------------------------

def _ensure_erasure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ERASURE_TABLE} (
            erasure_id        TEXT PRIMARY KEY,
            event_id          TEXT NOT NULL,
            tenant_id         TEXT,
            original_checksum TEXT NOT NULL,
            reason            TEXT NOT NULL,
            actor             TEXT NOT NULL,
            erased_at         TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{ERASURE_TABLE}_event "
        f"ON {ERASURE_TABLE}(event_id)"
    )


def _redact_payloads(conn: sqlite3.Connection, event_ids: set[str],
                     *, reason: str, actor: str) -> int:
    """Rewrite the payloads, then put the append-only trigger back.

    The trigger definition is read from `sqlite_master` and restored verbatim
    rather than retyped, so a change in `event_store.py` cannot be silently
    reverted by this function. Restoration is in a `finally`: a crash mid-way
    that left the log writable would be a far worse outcome than a failed
    erasure.
    """
    if not event_ids:
        return 0

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
        (_NO_UPDATE_TRIGGER,),
    ).fetchone()
    trigger_sql = row[0] if row else None

    redacted = 0
    try:
        if trigger_sql:
            conn.execute(f"DROP TRIGGER IF EXISTS {_NO_UPDATE_TRIGGER}")
        for event_id in sorted(event_ids):
            tomb = json.dumps(
                {"redacted": True, "erased_at": _utc_now(), "reason": reason,
                 "actor": actor},
                ensure_ascii=False, sort_keys=True,
            )
            cursor = conn.execute(
                "UPDATE cognitive_events SET payload_json=?, metadata_json=? "
                "WHERE event_id=?",
                (tomb, json.dumps({"redacted": True}, sort_keys=True), event_id),
            )
            redacted += max(cursor.rowcount, 0)
        conn.commit()
    finally:
        if trigger_sql:
            conn.execute(trigger_sql)
            conn.commit()
        still_there = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name=?",
            (_NO_UPDATE_TRIGGER,),
        ).fetchone()[0]
        if trigger_sql and not still_there:      # pragma: no cover - defensive
            raise RuntimeError(
                "append-only trigger was not restored; the event log is writable"
            )
    return redacted


def erase_history(memory_os: Any, *, memory_id: str | None = None,
                  subject: str | None = None, needle: str | None = None,
                  reason: str = "", actor: str = "",
                  confirm: bool = False) -> ForgetReport:
    """Delete the memory, its derived artefacts, and redact the source events.

    The only level that can answer "is it really gone" with a yes, and the only
    one that cannot be undone. The event row survives so replay ordering and the
    audit trail stay intact; its payload does not.

    Deliberately awkward to call. `confirm=True` is required because this sits
    one keyword away from the reversible version, and a destructive default is
    a destructive default no matter how well documented — the same reasoning
    behind `ProjectionReplayEngine.replay(dry_run=True)`.
    """
    if not confirm:
        raise PermissionError(
            "erase_history không thể hoàn tác — phải truyền confirm=True. "
            "Muốn xoá có thể phục hồi thì dùng forget_derived()."
        )
    if not str(reason).strip():
        raise ValueError("erase_history cần reason — xoá vĩnh viễn phải ghi lý do")
    if not str(actor).strip():
        raise ValueError("erase_history cần actor — xoá vĩnh viễn phải ghi ai làm")

    conn = memory_os.memories.conn
    events = _events_conn(memory_os)

    targets = _resolve(conn, memory_id, subject)
    if not targets:
        report = ForgetReport(scope=Scope.HISTORY)
        report.note = "không tìm thấy ký ức nào khớp"
        return report

    # Resolved before anything is deleted: once the memory rows go, the link
    # from memory to event is gone with them.
    contents = _contents_of(conn, targets)
    merged = find_consolidated_containing(conn, targets)
    event_ids = _event_ids_of(conn, targets | set(merged))
    probes = _probes(contents, needle)

    report = forget_derived(memory_os, memory_id=memory_id, subject=subject,
                            needle=needle)
    report.scope = Scope.HISTORY

    _ensure_erasure_table(events)
    stamped: list[tuple] = []
    for event_id in sorted(event_ids):
        row = events.execute(
            "SELECT checksum, tenant_id FROM cognitive_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            continue
        stamped.append((str(uuid4()), event_id, row[1], row[0], reason, actor,
                        _utc_now()))

    report.events_redacted = _redact_payloads(events, event_ids,
                                              reason=reason, actor=actor)
    if stamped:
        events.executemany(
            f"INSERT INTO {ERASURE_TABLE}(erasure_id, event_id, tenant_id, "
            f"original_checksum, reason, actor, erased_at) VALUES(?,?,?,?,?,?,?)",
            stamped,
        )
        events.commit()

    # Proof, this time including the log the other levels leave alone.
    report.residue = []
    for probe in probes:
        report.residue.extend(verify(conn, probe))
        report.residue.extend(verify(events, probe, EVENT_COLUMNS))
    report.verified_clean = not report.residue
    report.reversible = bool(report.residue)
    report.reversible_via = sorted({h["where"] for h in report.residue}) \
        if report.residue else []
    if report.residue:
        report.note = (report.note + " | " if report.note else "") + \
            f"CÒN SÓT ở {len(report.residue)} chỗ — chưa xoá sạch"
    return report


# --------------------------------------------------------------------------
# the paper trail
# --------------------------------------------------------------------------

def erasures(conn: sqlite3.Connection, *,
             event_id: str | None = None) -> list[dict[str, Any]]:
    """Every recorded erasure. Never quotes what was erased."""
    sql = (f"SELECT erasure_id, event_id, tenant_id, original_checksum, reason, "
           f"actor, erased_at FROM {ERASURE_TABLE}")
    params: tuple = ()
    if event_id:
        sql += " WHERE event_id=?"
        params = (event_id,)
    sql += " ORDER BY erased_at"
    keys = ("erasure_id", "event_id", "tenant_id", "original_checksum",
            "reason", "actor", "erased_at")
    return [dict(zip(keys, row)) for row in _safe(conn, sql, params)]


def verify_event_integrity(conn: sqlite3.Connection, *,
                           tenant_id: str | None = None) -> IntegrityVerdict:
    """Recompute every event checksum and explain each mismatch.

    A redaction breaks the hash on purpose — the row still carries the checksum
    of the payload it used to hold. That break is *accounted for* only when an
    erasure record names the same event and the same old checksum. Anything
    else is tampering, including a redaction whose record was deleted to hide
    it.

    The checksum is recomputed with the event store's own function rather than
    a copy: two implementations of one hash drift, and the drift would show up
    as phantom tampering.
    """
    from bio_agent_os.cognitive.event_store import SQLiteEventStore

    verdict = IntegrityVerdict()
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM cognitive_events"
    params: tuple = ()
    if tenant_id:
        sql += " WHERE tenant_id=?"
        params = (tenant_id,)

    known: dict[str, set[str]] = {}
    for record in erasures(conn):
        known.setdefault(record["event_id"], set()).add(record["original_checksum"])

    for row in _safe(conn, sql, params):
        verdict.checked += 1
        stored = row["checksum"]
        try:
            recomputed = SQLiteEventStore._checksum(SQLiteEventStore._row(row))
        except (TypeError, ValueError, KeyError) as exc:
            verdict.tampered.append({"event_id": row["event_id"],
                                     "why": f"không đọc lại được: {exc}"})
            continue
        if recomputed == stored:
            continue
        if stored in known.get(row["event_id"], set()):
            verdict.redacted.append({"event_id": row["event_id"],
                                     "why": "đã xoá vĩnh viễn, có ghi nhận"})
        else:
            verdict.tampered.append({
                "event_id": row["event_id"],
                "why": "payload không khớp checksum và không có bản ghi xoá nào giải thích",
            })
    return verdict


__all__ = [
    "CONTENT_COLUMNS",
    "DERIVED_TABLES",
    "ERASURE_TABLE",
    "EVENT_COLUMNS",
    "ForgetReport",
    "IntegrityVerdict",
    "Scope",
    "erase_history",
    "erasures",
    "find_by_subject",
    "find_consolidated_containing",
    "forget",
    "forget_derived",
    "forget_projection",
    "verify",
    "verify_event_integrity",
]
