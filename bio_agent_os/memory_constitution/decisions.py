"""Every time a law let something through or stopped it, and why.

The flight recorder for whatever learns next. A reflection engine that
consolidates lessons will be blocked by these laws sometimes, and six months
later "why does the system not know X" has to be answerable — usually the
answer is that a rule refused it on a date, under a version of the constitution
that no longer exists.

Records carry `constitution_version` for that reason. When RULE_007 arrives,
the same input legitimately produces a different decision, and only the version
explains it. Without it the log says the system changed its mind for no reason.

The log holds references, never content. It is written on every gated
operation, so anything quoted in it would be one more place a deletion has to
reach — the same rule that keeps the lifecycle transition log to ids and enums.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .laws import CONSTITUTION_VERSION, Outcome, Verdict

TABLE = "constitution_decisions"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    decision_id          TEXT PRIMARY KEY,
    rule_id              TEXT NOT NULL,
    constitution_version TEXT NOT NULL,
    operation            TEXT NOT NULL,
    decision             TEXT NOT NULL,
    reason               TEXT NOT NULL,
    input_refs_json      TEXT NOT NULL,
    evidence_refs_json   TEXT NOT NULL,
    actor                TEXT NOT NULL,
    at                   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_rule ON {TABLE}(rule_id, at);
"""


@dataclass
class Decision:
    rule_id: str
    operation: str
    decision: str
    reason: str = ""
    input_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    actor: str = ""
    constitution_version: str = CONSTITUTION_VERSION
    at: str = ""
    decision_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "rule_id": self.rule_id,
            "constitution_version": self.constitution_version,
            "operation": self.operation, "decision": self.decision,
            "reason": self.reason, "input_refs": self.input_refs,
            "evidence_refs": self.evidence_refs, "actor": self.actor,
            "at": self.at,
        }


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def record(conn: sqlite3.Connection, verdict: Verdict, *, operation: str,
           actor: str, input_refs: list[str] | None = None,
           evidence_refs: list[str] | None = None) -> Decision:
    """Write one decision. Refuses without an actor.

    A gate nobody is accountable for cannot be audited, and an audit trail with
    holes reads as complete — the same requirement the lifecycle transitions
    carry.
    """
    if not str(actor).strip():
        raise ValueError("quyết định của constitution phải ghi rõ actor")

    ensure_schema(conn)
    decision = Decision(
        decision_id=str(uuid4()),
        rule_id=verdict.law_id,
        constitution_version=verdict.constitution_version,
        operation=str(operation),
        decision=verdict.outcome.value,
        reason=verdict.detail,
        input_refs=list(input_refs or []),
        evidence_refs=list(evidence_refs or []),
        actor=str(actor).strip(),
        at=datetime.now(timezone.utc).isoformat(),
    )
    conn.execute(
        f"INSERT INTO {TABLE}(decision_id, rule_id, constitution_version, "
        f"operation, decision, reason, input_refs_json, evidence_refs_json, "
        f"actor, at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (decision.decision_id, decision.rule_id, decision.constitution_version,
         decision.operation, decision.decision, decision.reason,
         json.dumps(decision.input_refs, ensure_ascii=False),
         json.dumps(decision.evidence_refs, ensure_ascii=False),
         decision.actor, decision.at),
    )
    conn.commit()
    return decision


def decisions(conn: sqlite3.Connection, *, rule_id: str | None = None,
              operation: str | None = None) -> list[dict[str, Any]]:
    """Everything the constitution decided, oldest first."""
    sql = (f"SELECT decision_id, rule_id, constitution_version, operation, "
           f"decision, reason, input_refs_json, evidence_refs_json, actor, at "
           f"FROM {TABLE}")
    clauses, params = [], []
    if rule_id:
        clauses.append("rule_id=?")
        params.append(rule_id)
    if operation:
        clauses.append("operation=?")
        params.append(operation)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY rowid"

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    out = []
    for row in rows:
        out.append({
            "decision_id": row[0], "rule_id": row[1],
            "constitution_version": row[2], "operation": row[3],
            "decision": row[4], "reason": row[5],
            "input_refs": json.loads(row[6] or "[]"),
            "evidence_refs": json.loads(row[7] or "[]"),
            "actor": row[8], "at": row[9],
        })
    return out


class ConstitutionViolation(RuntimeError):
    """Raised when a gated operation is refused."""

    def __init__(self, verdict: Verdict, operation: str) -> None:
        super().__init__(
            f"[{verdict.law_id} @ {verdict.constitution_version}] "
            f"{operation}: {verdict.outcome.value} — {verdict.detail}")
        self.verdict = verdict
        self.operation = operation


def gate(conn: sqlite3.Connection | None, verdicts: list[Verdict], *,
         operation: str, actor: str,
         input_refs: list[str] | None = None) -> None:
    """Let an operation through, or stop it. Fail-closed.

    `NOT_EVALUABLE` blocks exactly like `FAIL`. A law that could not be checked
    has not been satisfied, and the direction to fail in is the safe one —
    otherwise a broken checker silently becomes a disabled law, which is the
    failure this whole layer exists to prevent.

    `NOT_APPLICABLE` passes: the law genuinely has nothing to say here.
    """
    for verdict in verdicts:
        if conn is not None:
            record(conn, verdict, operation=operation, actor=actor,
                   input_refs=input_refs)
        if verdict.blocks():
            raise ConstitutionViolation(verdict, operation)


__all__ = [
    "CONSTITUTION_VERSION",
    "ConstitutionViolation",
    "Decision",
    "Outcome",
    "SCHEMA",
    "TABLE",
    "decisions",
    "ensure_schema",
    "gate",
    "record",
]
