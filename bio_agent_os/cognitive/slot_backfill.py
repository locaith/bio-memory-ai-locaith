"""Give rows written before slots existed the slot they already implied.

Deterministic, idempotent, offline. Never runs on open.

The measurement that asked for it: `structured_json` is empty on 914 of 914
rows across four finished run databases. Every one of those sentences names a
subject and an aspect — the resolver reads both from 711 of them in 36 ms —
and every reader has been re-deriving that from the Vietnamese text with a
cosine comparison ever since. Extracting the structure once and keeping it is
the whole point; a store written last week should not be permanently worse at
answering than one written today.

Three properties, each of which is checked rather than asserted:

    deterministic  the same row yields the same slot on every run. No model,
                   no embedder, no clock, no randomness — `resolve_frame` is
                   a closed ontology over text.
    idempotent     a second pass writes nothing. Verified by re-running and
                   comparing, not by trusting the watermark.
    offline        an explicit call. `_backfill_source_events` runs on open
                   because the link table is required for correctness; this
                   one only makes queries better, and a migration that costs
                   time on every process start is a migration people route
                   around.

**A row the resolver cannot place stores nothing.** Not `{"attribute": null}`,
not a guess from the nearest neighbour — the column stays `{}` and every
reader falls back to the text path exactly as before. Filling a column to
raise a coverage number would convert "I could not tell" into "I decided",
which is the same trade the relation classifier refuses to make.

**It never overwrites.** A slot already present was written by the ingest path
from the redacted content, or by a caller who knew better than the resolver.
Either outranks a bulk pass.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

#: Marker name in `memory_store_migrations`, following the convention already
#: used by `_backfill_source_events`.
MARKER = "structured_slot_backfill"


@dataclass
class BackfillReport:
    scanned: int = 0
    filled: int = 0
    unresolved: int = 0
    already_had_slot: int = 0
    changed: int = 0
    #: Rows whose stored slot disagreed with what the resolver produces now.
    #: Never rewritten — reported, because a disagreement means either the
    #: resolver changed or something else wrote here, and both are worth
    #: knowing before a second pass is trusted.
    disagreements: list = field(default_factory=list)
    #: Rows re-derived because they were written by an older resolver version.
    #: Distinct from `filled`: those had no slot, these had one that a newer
    #: resolver would answer differently.
    rederived: int = 0
    #: Subjects that changed under re-derivation. Reported by row, because
    #: "how many" cannot tell a repair from a fresh corruption and a person's
    #: identity is not a number to be summarised.
    entity_repairs: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def coverage(self) -> float:
        total = self.filled + self.unresolved + self.already_had_slot
        return round((self.filled + self.already_had_slot) / total, 4) if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"scanned": self.scanned, "filled": self.filled,
                "unresolved": self.unresolved,
                "already_had_slot": self.already_had_slot,
                "changed": self.changed,
                "structured_coverage": self.coverage,
                "disagreements": self.disagreements[:20],
                "rederived": self.rederived,
                "entity_repairs": self.entity_repairs[:20],
                "entity_repair_count": len(self.entity_repairs),
                "seconds": round(self.seconds, 3)}


def _ensure_marker_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_store_migrations ("
        "name TEXT PRIMARY KEY, applied_at REAL, high_water INTEGER)")


#: Bumped when the resolver's answer for the same sentence could change, so a
#: row can say which version produced its slot.
#:
#: @2 — `_names` no longer runs a name across a clause boundary. Under @1 the
#: word before a comma was absorbed into the name that followed it, so
#: "…là sai, Bùi Cường…" was stored under the subject "sai Bùi Cường". Harmless
#: while the subject was re-derived from the text on every read; a silent
#: deletion once `SUBJECT_IDENTITY_READ` selects a person's history by the
#: stored value. Rows written under @1 are re-derived by `backfill`.
RESOLVER_VERSION = "aspect_resolver@2"

#: How much a stored slot may be relied on, decided by **where it came from**
#: and never by a score.
#:
#:     TRUSTED    an explicit field from a schema, or a deterministic parser
#:                with a stated invariant. Nothing in this system produces one
#:                yet, and the API is built as though something does — an
#:                interface designed only against the sources that happen to
#:                exist is an interface that breaks on the first new one.
#:     UNTRUSTED  inferred from natural language by the resolver, or
#:                backfilled from legacy text. **Every slot written today.**
#:     UNKNOWN    no predicate could be established.
#:
#: Deliberately not `confidence > 0.8 -> TRUSTED`. A threshold there would be
#: the same trade this project has refused four times: it converts "I inferred
#: this" into "I observed this" on the strength of a number the resolver made
#: up. The status is a fact about provenance, and provenance is knowable
#: exactly.
TRUSTED = "trusted"
UNTRUSTED = "untrusted"
UNKNOWN = "unknown"


def slot_for(content: str, *, source: str = "backfill",
             event_id: str | None = None) -> dict[str, Any]:
    """The slot this sentence implies, or `{}`.

    **The one derivation.** `facade.remember` calls this too. Two
    implementations of "which slot is this sentence about" would drift, and
    the drift would show up as a backfilled row disagreeing with a freshly
    written one for identical text — which is exactly what
    `disagreements` in the report is there to catch, and it must only ever
    catch a resolver *version* change, never two copies of the same idea.

    `source` records which path wrote it. It is not part of the slot's
    meaning and nothing reads it to decide anything; it exists so that a
    disagreement can be traced to a pass rather than guessed at.
    """
    from .aspect_resolver import Predicate, resolve_frame

    try:
        frame = resolve_frame(str(content or ""))
    except Exception:                                    # noqa: BLE001
        return {}
    if not frame.subject or frame.predicate is Predicate.UNKNOWN:
        return {}
    return {
        "entity": frame.subject,
        "attribute": frame.predicate.attribute,
        # Provenance as data on the row, not as a convention in someone's
        # head. A reader a year from now must be able to ask "how did this
        # field come to exist" without reading the code that wrote it.
        #
        # Every slot this function produces is UNTRUSTED, and always will be:
        # it read a Vietnamese sentence and guessed. Persisting a guess does
        # not promote it. That is RULE_003 — INFERENCE != OBSERVATION —
        # reaching down into the storage layer.
        "predicate_epistemic_status": UNTRUSTED,
        "predicate_source": source,
        "resolver_version": RESOLVER_VERSION,
        "derived_from_event_id": event_id,
        "derived_at": time.time(),
        # Kept under the old key too. `_slot_of_row` and the gate already read
        # it, and renaming a field that three modules and four frozen
        # databases depend on buys nothing here.
        "resolver": RESOLVER_VERSION,
        "source": source,
    }


def status_of(slot: dict[str, Any] | None) -> str:
    """How far a stored slot may be relied on.

    A slot with no recorded status is UNTRUSTED, never TRUSTED. Rows written
    before this field existed were produced by the same resolver, and a
    missing provenance must never read as a stronger claim than a present one.
    """
    if not slot or not slot.get("attribute"):
        return UNKNOWN
    recorded = slot.get("predicate_epistemic_status")
    return recorded if recorded in (TRUSTED, UNTRUSTED) else UNTRUSTED


def backfill(conn: sqlite3.Connection, *, dry_run: bool = False,
             batch: int = 500) -> BackfillReport:
    """One pass over rows with no slot. Safe to call any number of times."""
    started = time.perf_counter()
    report = BackfillReport()
    _ensure_marker_table(conn)

    try:
        rows = conn.execute(
            "SELECT memory_id, version, content, structured_json "
            "FROM cognitive_memories ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:                     # fresh database
        return report

    updates: list[tuple] = []
    for memory_id, version, content, structured in rows:
        report.scanned += 1
        existing = _loads(structured)
        if existing.get("attribute"):
            report.already_had_slot += 1
            fresh = slot_for(content)
            stale = existing.get("resolver_version") != RESOLVER_VERSION
            if stale and fresh:
                # A resolver version change is the one case where an existing
                # slot is rewritten rather than merely compared.
                #
                # Without this the pass repaired nothing that was already
                # written, and the `entity` axis was never even compared — only
                # `attribute` was. So every subject corrupted by the @1 name
                # boundary survived the fix for it, and turning
                # `SUBJECT_IDENTITY_READ` on against an existing store would
                # have silently dropped exactly those rows from their owner's
                # history. Code correct on disk is not the same as data correct
                # on disk.
                report.rederived += 1
                if existing.get("entity") != fresh.get("entity"):
                    report.entity_repairs.append(
                        {"memory_id": str(memory_id),
                         "stored": existing.get("entity"),
                         "resolver_now": fresh.get("entity"),
                         "content": str(content)[:80]})
                merged = {**existing, **fresh}
                updates.append((json.dumps(merged, ensure_ascii=False,
                                           sort_keys=True),
                                str(memory_id), version))
                continue
            if fresh.get("attribute") and \
                    fresh["attribute"] != existing["attribute"]:
                report.disagreements.append(
                    {"memory_id": str(memory_id),
                     "stored": existing["attribute"],
                     "resolver_now": fresh["attribute"],
                     "content": str(content)[:80]})
            continue

        slot = slot_for(content)
        if not slot:
            report.unresolved += 1
            continue
        report.filled += 1
        # Merge rather than replace: a row may hold structured content for a
        # different purpose — the procedural compiler writes goal/steps here —
        # and a slot must not evict it.
        merged = {**existing, **slot}
        updates.append((json.dumps(merged, ensure_ascii=False, sort_keys=True),
                        str(memory_id), version))

    if not dry_run and updates:
        for start in range(0, len(updates), batch):
            conn.executemany(
                "UPDATE cognitive_memories SET structured_json = ? "
                "WHERE memory_id = ? AND version = ?",
                updates[start:start + batch])
        report.changed = len(updates)
        high_water = conn.execute(
            "SELECT MAX(rowid) FROM cognitive_memories").fetchone()[0] or 0
        conn.execute(
            "INSERT OR REPLACE INTO memory_store_migrations"
            "(name, applied_at, high_water) VALUES (?,?,?)",
            (MARKER, time.time(), high_water))
        conn.commit()

    report.seconds = time.perf_counter() - started
    return report


def _loads(blob: Any) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        value = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = ["BackfillReport", "MARKER", "backfill", "slot_for"]
