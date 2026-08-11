"""Turns cheap labels into real ones, off the write path, in batches.

The write path produces a deterministic label in microseconds and marks it
`label_pending = 1`. This is the other half: it finds those rows and asks a
model for a real label. It costs about twenty seconds an event
(`reports/hippocampus_characterisation.md`, p50 = 20.4 s, p95 = 24.8 s), which
is exactly why it lives here and not in a transaction.

Three rules, and they are the ones section 2.3 of the join plan calls
non-negotiable:

1. **Never inside a transaction.** Each batch commits on its own. A model call
   holding a write transaction open for twenty seconds is the defect the last
   nine canary runs were spent removing.
2. **Never a cloud model with someone's memories.** Local backend or nothing,
   unless a human explicitly says otherwise for this run.
3. **Never overwrite a good label with a worse one.** A batch that comes back
   short must not shift labels onto the wrong rows, and a failure must leave
   the row pending rather than stamping it done.

Bounded on purpose: `upgrade_batch()` does one batch and returns how many rows
it changed, so the caller decides how long to keep going. On this machine a
batch of ten is roughly three to four minutes of GPU, and the owner's rule is
that nothing GPU-bound runs unattended overnight — the power supply has not been
replaced and there have been three hard power-offs.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bio_agent_os.cognitive.hippocampus_label import CHEAP, MODEL

#: Backends that keep the data on this machine. Anything else needs a human to
#: say so out loud, per run, because these rows are someone's memories.
LOCAL_BACKENDS = {"ollama", "llamacpp", "vllm", "local"}
ALLOW_REMOTE_ENV = "BIO_HIPPOCAMPUS_ALLOW_REMOTE_LABELS"

#: Phase 1 measured p95 = 24.8 s for a single label. A batch of ten shares one
#: call, so this is generous rather than tight — the point is that it is finite.
BATCH_TIMEOUT_SECONDS = float(os.getenv("BIO_HIPPOCAMPUS_BATCH_TIMEOUT_S", "180"))


class RemoteLabellingRefused(RuntimeError):
    """Raised rather than quietly sending memories to someone else's server."""


@dataclass
class UpgradeMetrics:
    batches: int = 0
    rows_seen: int = 0
    rows_upgraded: int = 0
    rows_left_pending: int = 0
    failures: list[str] = field(default_factory=list)


class LabelUpgrader:
    def __init__(self, engine: Any, conn: sqlite3.Connection,
                 *, allow_remote: bool | None = None) -> None:
        self.engine = engine
        self.conn = conn
        self.metrics = UpgradeMetrics()
        if allow_remote is None:
            allow_remote = os.getenv(ALLOW_REMOTE_ENV, "0").strip() == "1"
        self._assert_local_enough(allow_remote)

    def _assert_local_enough(self, allow_remote: bool) -> None:
        backend = str(getattr(self.engine, "backend", "") or "").lower()
        if backend in LOCAL_BACKENDS or allow_remote:
            return
        raise RemoteLabellingRefused(
            f"backend {backend!r} is not local, and these rows are memories. "
            f"Set {ALLOW_REMOTE_ENV}=1 to say that is intended for this run."
        )

    def pending(self, limit: int) -> list[sqlite3.Row]:
        """Oldest first — the backlog drains in the order it accumulated."""
        try:
            return self.conn.execute(
                "SELECT h.projection_key, h.event_id, e.payload_json "
                "FROM hippocampus_labels h "
                "JOIN cognitive_events e ON e.event_id = h.event_id "
                "WHERE h.label_pending = 1 AND h.label_source = ? "
                "ORDER BY h.created_at LIMIT ?",
                (CHEAP, limit),
            ).fetchall()          # fetchall releases the read transaction before
                                  # a twenty-second model call begins
        except sqlite3.OperationalError:
            return []

    async def upgrade_batch(self, limit: int = 10) -> int:
        """One batch: one model call, then one commit. Returns rows upgraded."""
        import json

        from bio_agent_os.background_jobs.hippocampus import MemoryLabelBatch

        rows = self.pending(limit)
        if not rows:
            return 0

        self.metrics.batches += 1
        self.metrics.rows_seen += len(rows)

        contents = []
        for row in rows:
            try:
                payload = json.loads(row[2] or "{}")
            except (TypeError, ValueError):
                payload = {}
            contents.append(str(payload.get("content", ""))[:600])

        listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(contents))
        prompt = (
            "You are the hippocampus for an AI agent.\n"
            f"Label each of the following {len(rows)} observations with topic, "
            "importance (1-10), whether it is junk/transient, and observed state.\n"
            "Return exactly one label per observation, in the same order.\n\n"
            f"{listing}"
        )

        try:
            result = await asyncio.wait_for(
                self.engine.generate_structured(
                    prompt, schema=MemoryLabelBatch, temperature=0.1
                ),
                timeout=BATCH_TIMEOUT_SECONDS,
            )
            labels = (result or {}).get("labels") or []
        except Exception as exc:
            # Left pending on purpose. A failed batch must be retryable, and a
            # row stamped `done` with a fabricated label never would be.
            self.metrics.failures.append(f"{type(exc).__name__}: {exc}"[:200])
            self.metrics.rows_left_pending += len(rows)
            return 0

        # Pair by index and drop the remainder. A short or long list must never
        # shift labels onto the wrong observations — a confidently wrong label
        # on the wrong memory is worse than no label at all.
        upgraded = 0
        for row, label in zip(rows, labels):
            score = label.get("importance_score")
            if not isinstance(score, int) or not 1 <= score <= 10:
                # The schema bounds this, so reaching here means something
                # bypassed validation. Leave the row pending.
                continue
            self.conn.execute(
                "UPDATE hippocampus_labels SET topic=?, importance_score=?, "
                "is_junk_or_transient=?, user_state=?, label_source=?, "
                "label_pending=0, updated_at=datetime('now') "
                "WHERE projection_key=? AND label_source=?",
                (
                    str(label.get("topic", "unknown"))[:200], score,
                    1 if label.get("is_junk_or_transient") else 0,
                    str(label.get("user_state", "unknown"))[:100],
                    MODEL, row[0], CHEAP,
                ),
            )
            upgraded += 1

        self.conn.commit()          # one commit per batch, outside everything
        self.metrics.rows_upgraded += upgraded
        self.metrics.rows_left_pending += len(rows) - upgraded
        return upgraded

    async def drain(self, *, batch: int = 10, max_batches: int = 10,
                    rest_seconds: float = 5.0) -> UpgradeMetrics:
        """Bounded drain with a rest between batches.

        `max_batches` is not a formality. Ten batches of ten is roughly half an
        hour of GPU on this machine, and the rule here is that nothing
        GPU-bound runs unattended overnight.
        """
        for index in range(max_batches):
            if await self.upgrade_batch(batch) == 0 and not self.pending(1):
                break
            if rest_seconds and index + 1 < max_batches:
                await asyncio.sleep(rest_seconds)
        return self.metrics


__all__ = [
    "ALLOW_REMOTE_ENV",
    "BATCH_TIMEOUT_SECONDS",
    "LOCAL_BACKENDS",
    "LabelUpgrader",
    "RemoteLabellingRefused",
    "UpgradeMetrics",
]
