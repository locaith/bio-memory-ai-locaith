"""When a claim was true, why it stopped being true, and who says so.

    2026-01: CTO = An
    2026-08: CTO = Bình

    "Ai là CTO bây giờ?"      -> Bình
    "Ai là CTO tháng 1/2026?" -> An
    "An từng là CTO không?"   -> có

An's claim is neither wrong nor deleted. It is superseded: true then, not now.
A store that can only mark a memory *inactive* answers the first question and
loses the other two, and that is the whole difference between a memory with a
lifecycle and a vector database with timestamps.

Almost no new storage was needed. `valid_from` and `valid_to` have been on
`CognitiveMemory` since the beginning, unused, and `MemoryStore._valid_at`
already reads them as a half-open window:

    valid_from <= as_of < valid_to

So supersession is one field, and the engine is a detector and a recorder
rather than a new storage layer.

**Not `superseded_at`.** Measured on the shipped store before any of this was
written: `candidate_pool` filters `superseded_at IS NULL` in SQL
(memory_store.py:481), *before* `as_of` is applied. Marking An `superseded_at`
therefore removes An from January as well as from today — the historical answer
disappears together with the current one, and the query that demonstrates the
feature is the one it breaks. `superseded_at` keeps its existing meaning:
row-version supersession, a storage concept, not a belief one.

**Correction is the same mechanism, used differently.** A corrected claim was
never true, so it must answer nothing at any `as_of`. Setting `valid_to ==
valid_from` makes the window empty by construction: no instant satisfies both
`as_of >= valid_from` and `as_of < valid_from`. The row stays, its history
stays, and it answers nothing — no new filter, no second code path, nothing
else to keep in sync.

The three invariants, which the benchmark measures separately:

    REPEAT     does not change what is true, only how fresh it is
    SUPERSEDE  the old value WAS true, and still answers historical questions
    CORRECT    the old value was NEVER true, and answers nothing

Every transition is recorded with reason, evidence, actor and time. The log
holds ids and enum values only — no memory content. It is written on every
state change, so anything quoted in it would be one more place a deletion has
to reach, and `DERIVED_TABLES` in `forgetting` names this table for exactly
that reason.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

TRANSITIONS_TABLE = "memory_lifecycle_transitions"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TRANSITIONS_TABLE} (
    transition_id TEXT PRIMARY KEY,
    memory_id     TEXT NOT NULL,
    tenant_id     TEXT,
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    actor         TEXT NOT NULL,
    at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{TRANSITIONS_TABLE}_memory
    ON {TRANSITIONS_TABLE}(memory_id, at);
"""


class ClaimState(str, Enum):
    """Where a claim stands, as distinct from how much it is believed.

    Deliberately separate from `BeliefState`, which is about confidence
    (proposed → reinforced → stable → challenged). Overloading that enum would
    make "we are unsure about this" and "this stopped being true in August" the
    same value, and the second is not a degree of the first.
    """

    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    CORRECTED = "corrected"
    FORGOTTEN = "forgotten"


class Reason(str, Enum):
    OBSERVED = "observed"
    REPLACED_BY_NEWER = "replaced_by_newer"
    MARKED_WRONG = "marked_wrong"
    AGE_AND_VOLATILITY = "age_and_volatility"
    USER_REQUESTED_DELETE = "user_requested_delete"
    RECONFIRMED = "reconfirmed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(memory_os: Any) -> sqlite3.Connection:
    return memory_os.memories.conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _record(memory_os: Any, memory_id: str, *, from_state: ClaimState,
            to_state: ClaimState, reason: Reason, actor: str,
            evidence: list[str] | None = None, at: str | None = None) -> None:
    """Write the transition. Refuses without an actor.

    A state change nobody is accountable for cannot be audited, and an audit
    trail with holes in it is worse than none because it reads as complete.
    """
    if not str(actor).strip():
        raise ValueError("chuyển trạng thái phải ghi rõ actor")

    conn = _conn(memory_os)
    ensure_schema(conn)
    row = conn.execute(
        "SELECT tenant_id FROM cognitive_memories WHERE memory_id=? LIMIT 1",
        (memory_id,)).fetchone()
    conn.execute(
        f"INSERT INTO {TRANSITIONS_TABLE}(transition_id, memory_id, tenant_id, "
        f"from_state, to_state, reason, evidence_json, actor, at) "
        f"VALUES(?,?,?,?,?,?,?,?,?)",
        (str(uuid4()), memory_id, row[0] if row else None,
         from_state.value, to_state.value, reason.value,
         json.dumps(sorted(evidence or []), ensure_ascii=False),
         str(actor).strip(), at or _now()),
    )
    conn.commit()


def transitions(memory_os: Any, *, memory_id: str) -> list[dict[str, Any]]:
    """Everything that ever happened to this claim, in the order it was recorded.

    Ordered by `rowid`, not by `at`. `at` carries two different clocks by
    design: `reconfirm` is told when the restatement happened in the world,
    while `supersede` stamps when it was recorded. Sorting by a column that
    mixes clocks puts a 2026-08-14 world event after a wall-clock entry from
    today, and `state_of` reads the last row — so a superseded claim reads back
    as active. Seen in the worked example before this was changed.
    """
    conn = _conn(memory_os)
    try:
        rows = conn.execute(
            f"SELECT from_state, to_state, reason, evidence_json, actor, at "
            f"FROM {TRANSITIONS_TABLE} WHERE memory_id=? ORDER BY rowid",
            (memory_id,)).fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict[str, Any]] = []
    for from_state, to_state, reason, evidence, actor, at in rows:
        try:
            parsed = json.loads(evidence)
        except (TypeError, ValueError):
            parsed = []
        out.append({"from_state": from_state, "to_state": to_state,
                    "reason": reason, "evidence": parsed,
                    "actor": actor, "at": at})
    return out


def state_of(memory_os: Any, memory_id: str) -> ClaimState:
    """The claim's current standing, from its last recorded transition.

    Derived rather than stored: a state column and a transition log would be
    two records of one fact, and they drift.

    Restatements are skipped rather than treated as the latest word. A repeat
    does not change what is true — that is one of the three invariants — so a
    superseded claim that someone mentions again is still superseded. Reading
    only the final row would quietly promote it back to active.
    """
    for record in reversed(transitions(memory_os, memory_id=memory_id)):
        if record["reason"] == Reason.RECONFIRMED.value:
            continue
        return ClaimState(record["to_state"])
    return ClaimState.ACTIVE


def _window(memory_os: Any, memory_id: str) -> tuple[str | None, str | None, str]:
    row = _conn(memory_os).execute(
        "SELECT valid_from, valid_to, observed_at FROM cognitive_memories "
        "WHERE memory_id=? ORDER BY version DESC LIMIT 1", (memory_id,)).fetchone()
    if row is None:
        raise KeyError(memory_id)
    return row[0], row[1], row[2]


def _set_window(memory_os: Any, memory_id: str, *, valid_from: str | None = None,
                valid_to: str | None = None) -> None:
    """Write the validity window directly.

    Not through `MemoryStore.supersede()`, which creates a new row version and
    sets `superseded_at` — the one field that must not be touched here. The
    version is unchanged, so the FTS key (`memory_id || ':' || version`) stays
    valid and the memory remains findable.
    """
    sets, params = [], []
    if valid_from is not None:
        sets.append("valid_from=?")
        params.append(valid_from)
    if valid_to is not None:
        sets.append("valid_to=?")
        params.append(valid_to)
    if not sets:
        return
    params.append(memory_id)
    conn = _conn(memory_os)
    conn.execute(
        f"UPDATE cognitive_memories SET {', '.join(sets)} WHERE memory_id=?",
        params)
    conn.commit()


# --------------------------------------------------------------------------
# the three transitions
# --------------------------------------------------------------------------

def supersede(memory_os: Any, *, old_id: str, new_id: str, boundary: str,
              actor: str, evidence: list[str] | None = None) -> None:
    """The old claim stopped being current at `boundary`. It stays true before.

    Both sides of the boundary are written in one call. Closing the old window
    without opening the new one at the same instant leaves an interval where no
    claim is valid, and the honest-looking answer there is "tôi không có ghi
    nhận nào" — a fact erased by an engine that deleted nothing. No per-row
    invariant can see that gap, so it is prevented rather than detected.
    """
    _set_window(memory_os, old_id, valid_to=boundary)
    _set_window(memory_os, new_id, valid_from=boundary)
    _record(memory_os, old_id, from_state=ClaimState.ACTIVE,
            to_state=ClaimState.SUPERSEDED, reason=Reason.REPLACED_BY_NEWER,
            actor=actor, evidence=[new_id, *(evidence or [])])


def correct(memory_os: Any, *, wrong_id: str, right_id: str, actor: str,
            evidence: list[str] | None = None) -> None:
    """The old claim was never true. It answers nothing, at any point in time.

    An empty window (`valid_to == valid_from`) does the work: `_valid_at`
    requires `as_of >= valid_from` and `as_of < valid_to`, which no instant
    satisfies when they are equal. The row and its history survive, so "why did
    you change your mind" stays answerable — a corrected belief that vanished
    entirely would take the explanation with it.
    """
    valid_from, _, observed_at = _window(memory_os, wrong_id)
    instant = valid_from or observed_at
    _set_window(memory_os, wrong_id, valid_from=instant, valid_to=instant)
    _record(memory_os, wrong_id, from_state=ClaimState.ACTIVE,
            to_state=ClaimState.CORRECTED, reason=Reason.MARKED_WRONG,
            actor=actor, evidence=[right_id, *(evidence or [])])


def reconfirm(memory_os: Any, *, memory_id: str, at: str, actor: str,
              evidence: list[str] | None = None) -> None:
    """Someone restated this claim. Nothing about its truth changes.

    The trap this exists to avoid: a store that ranks by most-recent-mention
    reads a restatement of an old value as a new value, and reports a retired
    fact as current. Measured in the lifetime benchmark — `latest_mention`
    keeps 0.933 on current-truth questions and drops to 0.533 on historical
    ones for precisely this reason.

    So the window is not touched. What changes is the confirmation count, which
    is evidence of freshness and an input to staleness, not to truth.
    """
    conn = _conn(memory_os)
    conn.execute(
        "UPDATE cognitive_memories SET reinforcement_count = "
        "COALESCE(reinforcement_count, 0) + 1 WHERE memory_id=?", (memory_id,))
    conn.commit()
    _record(memory_os, memory_id, from_state=ClaimState.ACTIVE,
            to_state=ClaimState.ACTIVE, reason=Reason.RECONFIRMED,
            actor=actor, evidence=list(evidence or []), at=at)


__all__ = [
    "ClaimState",
    "Reason",
    "SCHEMA",
    "TRANSITIONS_TABLE",
    "correct",
    "ensure_schema",
    "reconfirm",
    "state_of",
    "supersede",
    "transitions",
]
