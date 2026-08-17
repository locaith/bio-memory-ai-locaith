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
    #: Rows whose observation came from a runtime hook. Never given a slot.
    operational_skipped: int = 0
    #: Rows that already carried a slot they were never eligible for, and had
    #: it removed. Listed by row: this is the one thing in this pass that takes
    #: something away, so it is reported by name and not as a count.
    operational_demoted: list = field(default_factory=list)
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
                "operational_skipped": self.operational_skipped,
                "operational_demoted": self.operational_demoted[:20],
                "operational_demoted_count": len(self.operational_demoted),
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


#: Hooks whose events are the runtime describing itself. Declared, never
#: inferred from the text: a rule that reads the words is a rule that fires on
#: a person quoting a log line, and misses a hook that changes its wording.
#:
#: The boundary this draws:
#:
#:     EVENT OBSERVED
#:         ├─ a claim about the world   -> memory semantics
#:         ├─ telemetry                 -> observability, kept, not semanticized
#:         ├─ system event              -> provenance / audit
#:         └─ unknown                   -> no semantic promotion
#:
#: Measured on the owner's real store, 17/08/2026: 183 rows, 156 of them hook
#: telemetry, and **all 20** rows that carried a structured slot had an entity
#: beginning `hook` or `UserPromptSubmit`. The system held a stored belief
#: about the *employer* of a thing called "hook UserPromptSubmit prompt …",
#: nine times over. Without this boundary a memory meant to last years fills up
#: with heartbeats, retries and counters, held as facts about a life.
#:
#: Raw events are **not** affected. Telemetry stays exactly where it is — it is
#: provenance, and it may well be what a later reflection pass learns from. It
#: is only barred from becoming a subject/predicate/value claim on its own.
OPERATIONAL_HOOKS: frozenset[str] = frozenset({
    "UserPromptSubmit", "SessionStart", "SessionEnd", "PreCompact",
    "PostCompact", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "Notification", "Stop", "PermissionRequest",
})


def is_operational_source(source: str | None) -> bool:
    """Did a runtime hook emit this, rather than somebody saying something?

    The shape is `<agent>:<HookName>` — `claude-code:UserPromptSubmit`. The
    agent half is deliberately not matched: which agent is running is not what
    makes an event operational.
    """
    if not source or ":" not in str(source):
        return False
    return str(source).rsplit(":", 1)[-1].strip() in OPERATIONAL_HOOKS


def slot_for(content: str, *, source: str = "backfill",
             event_id: str | None = None,
             event_source: str | None = None) -> dict[str, Any]:
    """The slot this sentence implies, or `{}`.

    **The one derivation.** `facade.remember` calls this too. Two
    implementations of "which slot is this sentence about" would drift, and
    the drift would show up as a backfilled row disagreeing with a freshly
    written one for identical text — which is exactly what
    `disagreements` in the report is there to catch, and it must only ever
    catch a resolver *version* change, never two copies of the same idea.

    `source` records which *derivation path* wrote it — "ingest" or
    "backfill". It is not part of the slot's meaning and nothing reads it to
    decide anything; it exists so that a disagreement can be traced to a pass
    rather than guessed at.

    `event_source` is a different thing and does decide: it is the provenance
    of the observation itself. An event a runtime hook emitted is the system
    describing its own operation, and no amount of well-formed Vietnamese in a
    log line makes it a claim about the world. Eligibility is checked **here**,
    in the one derivation, so ingest and backfill cannot disagree about what
    deserves to become a claim — a boundary enforced in only one of the two is
    a boundary the other quietly reopens.
    """
    from .aspect_resolver import Predicate, resolve_frame

    if is_operational_source(event_source):
        return {}

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
            "SELECT memory_id, version, content, structured_json, "
            "source_event_ids_json "
            "FROM cognitive_memories ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:                     # fresh database
        return report

    # Which observation each memory came from, so eligibility is decided from
    # the same fact ingest decides it from. Resolving it here rather than
    # per-row keeps the pass to two queries on a store of any size.
    event_source: dict[str, str] = {}
    try:
        event_source = {str(eid): str(src) for eid, src in conn.execute(
            "SELECT event_id, source FROM cognitive_events")}
    except sqlite3.OperationalError:
        pass                          # no event log; nothing is operational

    updates: list[tuple] = []
    for memory_id, version, content, structured, event_ids in rows:
        report.scanned += 1
        origin = _first_event_source(event_ids, event_source)
        if is_operational_source(origin):
            # The runtime describing itself. Kept exactly where it is — this
            # pass has never deleted anything and does not start here — but it
            # does not become a subject/predicate/value claim, and a row that
            # already did is repaired by having that claim removed.
            report.operational_skipped += 1
            if existing_slot := _loads(structured):
                if existing_slot.get("attribute"):
                    report.operational_demoted.append(
                        {"memory_id": str(memory_id), "source": origin,
                         "was_entity": existing_slot.get("entity"),
                         "was_attribute": existing_slot.get("attribute")})
                    kept = {k: v for k, v in existing_slot.items()
                            if k not in _SLOT_KEYS}
                    updates.append((json.dumps(kept, ensure_ascii=False,
                                               sort_keys=True),
                                    str(memory_id), version))
            continue
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


#: The keys `slot_for` owns. Removing a slot removes exactly these and leaves
#: whatever else shares the blob — the procedural compiler writes goal/steps
#: here, and a demotion that evicted them would be a deletion wearing the word
#: "boundary".
_SLOT_KEYS = frozenset({
    "entity", "attribute", "predicate_epistemic_status", "predicate_source",
    "resolver_version", "resolver", "derived_from_event_id", "derived_at",
    "source",
})


def _first_event_source(event_ids: Any, index: dict[str, str]) -> str | None:
    """Where the observation behind this memory came from."""
    try:
        ids = json.loads(event_ids) if isinstance(event_ids, str) else event_ids
    except (TypeError, ValueError):
        return None
    if not isinstance(ids, list) or not ids:
        return None
    return index.get(str(ids[0]))


def _loads(blob: Any) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        value = json.loads(blob) if isinstance(blob, str) else blob
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = ["BackfillReport", "MARKER", "backfill", "slot_for"]
