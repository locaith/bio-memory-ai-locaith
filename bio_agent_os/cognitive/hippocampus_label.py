"""The deterministic half of the hippocampus, on the write path.

Phase 1 measured the model-driven labeller at p50 = 20.4 s and p95 = 24.8 s per
event (`reports/hippocampus_characterisation.md`). The threshold set before any
number existed was "a few hundred milliseconds", so a model call cannot sit on
the write path — and the `ProjectionBuilder` contract says the same thing for a
second, independent reason: a builder runs inside the worker's open write
transaction and must be deterministic, or replay cannot reproduce what it
replayed.

So the join is two phases, not one:

    in the transaction    this file           deterministic, no model, microseconds
    outside it, later     the upgrader        the model call, in batches

**This module must never import a model, an engine, or `background_jobs`.** The
dependency points one way on purpose: `cognitive/` is the layer that has to keep
answering when the model is down, slow, or returning rubbish. The rule is worth
more than the convenience of reusing an import.

What lands here is a row marked `label_pending = 1`, which is the same marker
`Hippocampus.relabel_pending()` already drains. Nothing reads these labels for
ranking or retrieval — that stays true until Phase 3 proves the labels make
answers better, which is a different experiment from proving the labels are
good.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS hippocampus_labels (
    projection_key       TEXT PRIMARY KEY,
    event_id             TEXT NOT NULL,
    tenant_id            TEXT NOT NULL,
    workspace_id         TEXT NOT NULL,
    topic                TEXT,
    importance_score     INTEGER NOT NULL,
    is_junk_or_transient INTEGER NOT NULL,
    user_state           TEXT,
    label_source         TEXT NOT NULL,
    label_pending        INTEGER NOT NULL DEFAULT 1,
    label_failed         TEXT,
    projection_version   INTEGER NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_hippocampus_labels_pending
    ON hippocampus_labels(label_pending, created_at);
CREATE INDEX IF NOT EXISTS idx_hippocampus_labels_event
    ON hippocampus_labels(event_id);
CREATE INDEX IF NOT EXISTS idx_hippocampus_labels_scope
    ON hippocampus_labels(tenant_id, workspace_id);
"""

#: Deliberately crude. These exist to cost nothing, not to be right — the
#: upgrader overwrites them with a real label before anything depends on them.
JUNK_MARKERS = (
    "ok", "oke", "okay", "vâng", "dạ", "ừ", "uh", "thanks", "thank you",
    "cảm ơn", "cám ơn", "hi", "hello", "chào", "xin chào", "bye",
)

CHEAP = "cheap"
MODEL = "model"


@dataclass(frozen=True, slots=True)
class CheapLabel:
    topic: str
    importance_score: int
    is_junk_or_transient: bool
    user_state: str
    label_pending: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "importance_score": self.importance_score,
            "is_junk_or_transient": self.is_junk_or_transient,
            "user_state": self.user_state,
            "label_pending": self.label_pending,
        }


def cheap_label(content: str) -> CheapLabel:
    """A label derived from the text alone. Same input, same output, always.

    The single source of truth for the cheap path:
    `Hippocampus._cheap_label` delegates here so the write path and the
    consolidation path cannot drift apart in what "unlabelled" means.
    """
    text = " ".join(str(content).split())
    stripped = text.lower().strip(" .!?,:;")
    is_junk = len(stripped) <= 24 and any(
        stripped == marker or stripped.startswith(marker + " ") for marker in JUNK_MARKERS
    )
    # Digits often carry the facts worth keeping — codes, dates, amounts.
    importance = 3 if is_junk else (7 if any(char.isdigit() for char in text) else 5)
    return CheapLabel(
        topic="unlabeled",
        importance_score=importance,
        is_junk_or_transient=is_junk,
        user_state="unknown",
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def pending_count(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM hippocampus_labels WHERE label_pending = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        # The table belongs to a capability this database may not have enabled.
        # Absent is not broken.
        return 0
    return int(row[0]) if row else 0


class HippocampusLabelBuilder:
    """Writes the deterministic label into the worker's open transaction.

    Does not commit — the worker holds the ledger row on this same connection
    and commits the pair, so committing here would split one transaction into
    two and leave a window where the label exists and the ledger cannot yet say
    what produced it.
    """

    projection_type = "hippocampus_label"

    def __init__(self, conn_schema_ready: bool = False) -> None:
        self._schema_ready = conn_schema_ready

    def build(self, event: Any, job: Any, conn: sqlite3.Connection) -> Any:
        # Imported here, not at module scope: importing the worker at module
        # scope would make this module and the worker import each other.
        from .reconciliation_worker import BuildOutcome, BuildResult

        content = (getattr(event, "payload", None) or {}).get("content", "")
        if not str(content).strip():
            return BuildResult(BuildOutcome.SKIPPED.value, reason="event carries no content")

        if not self._schema_ready:
            ensure_schema(conn)
            self._schema_ready = True

        label = cheap_label(str(content))
        now = getattr(event, "observed_at", None) or _now_iso()

        conn.execute(
            "INSERT INTO hippocampus_labels ("
            "  projection_key, event_id, tenant_id, workspace_id, topic,"
            "  importance_score, is_junk_or_transient, user_state,"
            "  label_source, label_pending, projection_version, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,1,?,?) "
            # Replay must be able to rebuild this row without the second write
            # becoming a duplicate or silently clobbering a real model label
            # that landed in between. Rebuilding the *cheap* label over a
            # *model* label would be a regression, so it is excluded.
            "ON CONFLICT(projection_key) DO UPDATE SET"
            "  topic=excluded.topic,"
            "  importance_score=excluded.importance_score,"
            "  is_junk_or_transient=excluded.is_junk_or_transient,"
            "  updated_at=excluded.created_at "
            "WHERE hippocampus_labels.label_source = ?",
            (
                job.key, event.event_id, event.tenant_id, event.workspace_id,
                label.topic, label.importance_score,
                1 if label.is_junk_or_transient else 0, label.user_state,
                CHEAP, job.projection_version, now, CHEAP,
            ),
        )
        return BuildResult(BuildOutcome.BUILT.value, target_id=job.key)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CHEAP",
    "MODEL",
    "SCHEMA",
    "CheapLabel",
    "HippocampusLabelBuilder",
    "cheap_label",
    "ensure_schema",
    "pending_count",
]
