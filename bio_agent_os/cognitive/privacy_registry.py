"""Every persistent store, declared — and a closed world, not a list.

`rejected_inputs.payload_json` was found by an independent review sitting
outside the deletion verifier's reach. Adding it to a list would fix that one
table and leave the next one to be found the same way, by somebody reading
carefully at the right moment.

So the rule is subtraction, not enumeration:

    every table in the database
      − every table declared here
      = ∅

A store nobody declared fails the suite. `test_privacy_registry.py` performs
that subtraction against a live database, so a table added in six months
breaks CI on the commit that adds it rather than on the day someone audits.

Enumerating the real database turned up **five** stores holding raw content
outside the verifier, not one:

    cognitive_memory_fts.content     the full-text index — a copy of every
                                     sentence, searchable, and invisible to a
                                     verifier that reads `cognitive_memories`
    context_blocks.content           compiled context, quoting memories
    rejected_inputs.content          the review's finding
    agent_checkpoints.payload_json   session state
    projection_outbox.payload_json   queued work

Each declaration answers what a deletion has to do about it, so the policy
lives beside the fact rather than in whichever module happened to be written
last.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

#: What a deletion at each level must do with a store.
CASCADE = "cascade"          # rows go when the memory goes
INVALIDATE = "invalidate"    # entries are dropped or marked unusable
REDACT = "redact"            # payload rewritten, row kept
KEEP = "keep"                # deliberately untouched, and the reason is stated
NOT_APPLICABLE = "n/a"       # holds no content a deletion could reach


@dataclass(frozen=True)
class StorePolicy:
    store_name: str
    contains_raw_payload: bool
    contains_derived_payload: bool
    contains_identity_data: bool
    contains_secret_possible: bool
    serving_or_history: str            # "serving" | "history" | "operational"
    forget_derived_policy: str
    erase_history_policy: str
    replay_policy: str
    verification_strategy: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in (
            "store_name", "contains_raw_payload", "contains_derived_payload",
            "contains_identity_data", "contains_secret_possible",
            "serving_or_history", "forget_derived_policy",
            "erase_history_policy", "replay_policy",
            "verification_strategy", "note")}


def _store(name: str, *, raw=False, derived=False, identity=False,
           secret=False, kind="operational", forget=NOT_APPLICABLE,
           erase=NOT_APPLICABLE, replay="rebuilt",
           verify="none", note="") -> StorePolicy:
    return StorePolicy(name, raw, derived, identity, secret, kind, forget,
                       erase, replay, verify, note)


#: One row per persistent table. Missing entries fail the suite.
REGISTRY: dict[str, StorePolicy] = {p.store_name: p for p in (
    _store("cognitive_memories", raw=True, identity=True, secret=True,
           kind="serving", forget=CASCADE, erase=CASCADE,
           replay="blocked by tombstone",
           verify="CONTENT_COLUMNS: content, metadata_json, structured_json",
           note="the serving row. structured_json holds the slot and the "
                "subject name, which is why it was added to CONTENT_COLUMNS "
                "before anything wrote there."),

    _store("cognitive_events", raw=True, identity=True, secret=True,
           kind="history", forget=KEEP, erase=REDACT,
           replay="blocked by tombstone",
           verify="EVENT_COLUMNS, reported as reversible not residue",
           note="append-only by design and by trigger. Content surviving "
                "here is the history being explainable, not a leak — and "
                "`erase_history` is the only operation that touches it."),

    _store("cognitive_memory_fts", derived=True, identity=True, secret=True,
           kind="serving", forget=CASCADE, erase=CASCADE,
           replay="rebuilt from cognitive_memories",
           verify="external content table, follows its parent row",
           note="FOUND BY ENUMERATION, not by review. A searchable copy of "
                "every sentence. It is an external-content FTS5 table, so "
                "its rows follow `cognitive_memories` — but nothing said so "
                "anywhere before this line existed."),

    _store("shadow_memories", derived=True, identity=True, secret=True,
           kind="operational", forget=CASCADE, erase=CASCADE,
           replay="rebuilt", verify="CONTENT_COLUMNS: content",
           note="shadow projection output, never reachable by production "
                "recall."),

    _store("context_blocks", derived=True, identity=True, secret=True,
           kind="serving", forget=INVALIDATE, erase=INVALIDATE,
           replay="recompiled",
           verify="invalidate_scope on write; content quoted from memories",
           note="compiled context quoting memories verbatim. Invalidated by "
                "scope rather than by row, so a deletion reaches it only "
                "through the scope call."),

    _store("rejected_inputs", raw=True, identity=True, secret=True,
           kind="history", forget=KEEP, erase=REDACT,
           replay="never materialised",
           verify="NOT scanned by CONTENT_COLUMNS — open finding F6",
           note="the review's finding. Content is stored verbatim on "
                "purpose: a rejection that cannot be replayed is a deletion "
                "with extra steps. It is outside the verifier and that is "
                "recorded here rather than quietly fixed, because 'kept "
                "deliberately' and 'kept accidentally' must not look alike."),

    _store("memory_tombstones", identity=False, kind="history",
           forget=KEEP, erase=KEEP, replay="consulted, never rebuilt",
           verify="ids and reasons only, no content",
           note="deleting these would undo deletions. 'Was this ever "
                "deleted' has to stay answerable."),

    _store("memory_source_events", kind="operational", forget=CASCADE,
           erase=CASCADE, replay="rebuilt", verify="ids only"),

    # Created lazily by components that do not run on a bare open, so they
    # were absent from the first enumeration and present in the verifier —
    # which is exactly the disagreement `test_the_registry_matches_what_the_
    # deletion_verifier_scans` exists to catch. Two lists that must agree,
    # checked rather than assumed.
    _store("hippocampus_labels", derived=True, identity=True,
           kind="operational", forget=CASCADE, erase=CASCADE,
           replay="relabelled",
           verify="CONTENT_COLUMNS: topic",
           note="consolidation labels, keyed by event. Named in "
                "DERIVED_TABLES so a deletion reaches them by event id."),

    _store("memory_embeddings", derived=True, identity=True, secret=True,
           kind="serving", forget=CASCADE, erase=CASCADE,
           replay="re-embedded",
           verify="row count after deletion; vectors are not text",
           note="a vector keyed by a memory that no longer exists still "
                "retrieves, which is why `forget_projection` reports them as "
                "residue rather than deleting them silently."),

    _store("event_erasures", kind="history", forget=KEEP, erase=KEEP,
           replay="never",
           verify="checksums of redacted payloads, no content",
           note="the record that a redaction happened. Deleting it makes the "
                "checksum break read as tampering — which is the point."),

    _store("memory_lifecycle_transitions", kind="history", forget=CASCADE,
           erase=CASCADE, replay="never",
           verify="ids and enum values only",
           note="named in DERIVED_TABLES: it records that a memory existed "
                "and when its state changed, which is content of a kind."),

    _store("claim_relation_judgements", kind="history", forget=KEEP,
           erase=KEEP, replay="never",
           verify="values compared, no free text",
           note="stores the two values it judged, on purpose — without them "
                "the row cannot be re-judged and an audit nobody can re-run "
                "is a receipt rather than a record."),

    _store("projection_outbox", derived=True, secret=True, kind="operational",
           forget=INVALIDATE, erase=INVALIDATE,
           replay="skipped when tombstoned",
           verify="job payloads; drained and skipped on tombstone",
           note="queued work can outlive the decision that queued it, which "
                "is why the worker checks tombstones at apply time as well "
                "as at enqueue."),

    _store("agent_checkpoints", raw=True, identity=True, secret=True,
           kind="operational", forget=KEEP, erase=REDACT,
           replay="restored", verify="NOT scanned — open finding",
           note="session state, holds whatever a caller checkpointed. Same "
                "class as rejected_inputs and found the same way."),

    _store("constitution_decisions", kind="history", forget=KEEP, erase=KEEP,
           replay="never", verify="reasons and ids, no content",
           note="the audit of the gate. Erasing it would erase the record "
                "of what was refused."),

    _store("runtime_sessions", kind="operational", verify="no content",
           note="who was running, at which build."),
    _store("memory_store_migrations", kind="operational", verify="no content"),
    _store("context_cache_entries", derived=True, kind="operational",
           forget=INVALIDATE, erase=INVALIDATE, replay="recomputed",
           verify="block ids only"),
    _store("context_shares", kind="operational", verify="ids only"),
    _store("prospective_triggers", derived=True, kind="operational",
           forget=INVALIDATE, erase=INVALIDATE, verify="conditions, no content"),
    _store("self_capabilities", kind="operational", verify="ids only"),
)}

#: FTS5 shadow tables. Created by SQLite for `cognitive_memory_fts`, never by
#: this project, and they carry whatever their parent carries. Declared as a
#: family so a new FTS index does not need five more rows — and so that a
#: table that merely *looks* like one still has to be declared.
FTS_SUFFIXES = ("_config", "_content", "_data", "_docsize", "_idx")


def is_fts_shadow(table: str) -> bool:
    return any(table.endswith(suffix) and table[:-len(suffix)] in REGISTRY
               for suffix in FTS_SUFFIXES)


def declared(table: str) -> StorePolicy | None:
    return REGISTRY.get(table)


def persistent_tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}


def undeclared(conn: sqlite3.Connection) -> set[str]:
    """The subtraction. Empty is the only acceptable answer."""
    return {t for t in persistent_tables(conn)
            if t not in REGISTRY and not is_fts_shadow(t)}


def unscanned_content_stores() -> list[StorePolicy]:
    """Declared stores that hold content no verifier reads.

    Not a failure — `cognitive_events` is deliberately here — but a list that
    must be short and must be looked at. "Kept deliberately" and "kept
    accidentally" have to be distinguishable, and this is where that
    distinction is written down.
    """
    return [p for p in REGISTRY.values()
            if (p.contains_raw_payload or p.contains_derived_payload)
            and "NOT scanned" in p.verification_strategy]


__all__ = ["CASCADE", "INVALIDATE", "KEEP", "NOT_APPLICABLE", "REDACT",
           "REGISTRY", "StorePolicy", "declared", "is_fts_shadow",
           "persistent_tables", "undeclared", "unscanned_content_stores"]
