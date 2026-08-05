"""Shadow mode — proving the outbox path before trusting it.

The legacy write path works and people depend on it. The outbox path is
believed to be equivalent, and belief is not evidence. Shadow mode runs both
from **one canonical input** and compares the results, with the shadow output
kept somewhere production can never read.

Three properties this file exists to guarantee:

* **Isolation.** Shadow projections live in their own table. Production recall
  cannot see them by construction, not by a filter someone might forget.
* **One input.** Both paths derive from `CanonicalObservation`, reconstructed
  deterministically from the immutable event. Comparing two results built from
  two different inputs would prove nothing.
* **Legacy is never at risk.** A shadow failure is audited and swallowed. The
  caller's observe() succeeds or fails exactly as it did before.

Scope: `cognitive_memory` only. The other four projection types have no
builder, and the capability registry reports them `unsupported` rather than
letting them look tested.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import EventRecord, MemoryType
from .outbox import PROJECTION_VERSION, projection_key
from .projection_capability import shadow_supported
from .projection_registry import ProjectionType

logger = logging.getLogger("bio_agent_os.shadow")

MODE_ENV = "BIO_AGENT_PROJECTION_MODE"
COGNITIVE_MEMORY = ProjectionType.COGNITIVE_MEMORY.value


class ProjectionMode(str, Enum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    OUTBOX = "outbox"


def current_mode(explicit: str | None = None) -> ProjectionMode:
    """Resolve the mode. Anything unrecognised falls back to legacy.

    Defaulting to legacy on a typo is deliberate: a misconfigured environment
    variable must not quietly change how memories are written.
    """
    raw = (explicit or os.environ.get(MODE_ENV) or ProjectionMode.LEGACY.value).strip().lower()
    try:
        return ProjectionMode(raw)
    except ValueError:
        logger.warning("unknown %s=%r; falling back to legacy", MODE_ENV, raw)
        return ProjectionMode.LEGACY


# --------------------------------------------------------------------------
# canonical input
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalObservation:
    """The one input both paths build from.

    Reconstructed from the immutable event rather than captured alongside it,
    so a replay months later derives exactly the same thing from exactly the
    same row. Anything not derivable from the event has no business
    influencing a projection.
    """

    event_id: str
    tenant_id: str
    workspace_id: str | None
    content: str
    metadata: dict[str, Any]
    epistemic_status: str
    source: str
    observed_at: str
    effective_at: str | None
    security_label: str
    provenance: dict[str, Any]

    @classmethod
    def from_event(cls, event: EventRecord) -> CanonicalObservation:
        payload = event.payload or {}
        return cls(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            content=str(payload.get("content", "")),
            metadata=dict(event.metadata or {}),
            epistemic_status=event.epistemic_status.value,
            source=event.source,
            observed_at=event.observed_at,
            effective_at=event.valid_from,
            security_label=event.security_label.value,
            provenance={
                "actor": event.actor,
                "source": event.source,
                "trust_tier": int(event.trust_tier),
                "checksum": event.checksum,
            },
        )

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "content": self.content,
                "metadata": self.metadata,
                "epistemic_status": self.epistemic_status,
                "source": self.source,
                "observed_at": self.observed_at,
                "effective_at": self.effective_at,
                "security_label": self.security_label,
                "provenance": self.provenance,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def input_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def output_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


# --------------------------------------------------------------------------
# isolated shadow store
# --------------------------------------------------------------------------

SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_memories (
    shadow_projection_key TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    legacy_memory_id TEXT,
    projection_version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL NOT NULL,
    epistemic_status TEXT NOT NULL,
    security_label TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    provenance_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    canonical_input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    comparison_status TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_event ON shadow_memories(source_event_id);
CREATE INDEX IF NOT EXISTS idx_shadow_tenant ON shadow_memories(tenant_id, workspace_id);
"""


class ShadowMemoryStore:
    """Shadow projections, in a table production never queries.

    A separate table rather than a flag on `cognitive_memories`: a flag is one
    forgotten `WHERE` away from leaking shadow rows into a user's recall, and
    no amount of care makes that risk go away.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(SHADOW_SCHEMA)
        self.conn.commit()

    def put(self, row: dict[str, Any], *, commit: bool = True) -> str:
        self.conn.execute(
            "INSERT OR IGNORE INTO shadow_memories("
            " shadow_projection_key, source_event_id, legacy_memory_id, projection_version,"
            " tenant_id, workspace_id, memory_type, content, confidence, epistemic_status,"
            " security_label, valid_from, valid_to, provenance_json, metadata_json,"
            " canonical_input_hash, output_hash, comparison_status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["shadow_projection_key"], row["source_event_id"], row.get("legacy_memory_id"),
                row["projection_version"], row["tenant_id"], row.get("workspace_id"),
                row["memory_type"], row["content"], row["confidence"], row["epistemic_status"],
                row["security_label"], row.get("valid_from"), row.get("valid_to"),
                json.dumps(row.get("provenance", {}), sort_keys=True, ensure_ascii=False),
                json.dumps(row.get("metadata", {}), sort_keys=True, ensure_ascii=False),
                row["canonical_input_hash"], row["output_hash"],
                row.get("comparison_status"), time.time(),
            ),
        )
        if commit:
            self.conn.commit()
        return row["shadow_projection_key"]

    def by_event(self, event_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM shadow_memories WHERE source_event_id=? ORDER BY created_at",
            (event_id,),
        ).fetchall()

    def get(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM shadow_memories WHERE shadow_projection_key=?", (key,)
        ).fetchone()

    def count(self, *, tenant_id: str | None = None) -> int:
        if tenant_id:
            return int(self.conn.execute(
                "SELECT COUNT(*) FROM shadow_memories WHERE tenant_id=?", (tenant_id,)
            ).fetchone()[0])
        return int(self.conn.execute("SELECT COUNT(*) FROM shadow_memories").fetchone()[0])

    def set_comparison(self, key: str, status: str) -> None:
        self.conn.execute(
            "UPDATE shadow_memories SET comparison_status=? WHERE shadow_projection_key=?",
            (status, key),
        )
        self.conn.commit()


def shadow_key(event_id: str, version: int = PROJECTION_VERSION) -> str:
    """Distinct from the production projection key on purpose.

    Sharing the key space would let a shadow ledger row satisfy a production
    job's idempotency check, and the two paths would stop being independent.
    """
    return hashlib.sha256(f"shadow\x1f{event_id}\x1f{version}".encode()).hexdigest()


class ShadowMemoryBuilder:
    """Builds the shadow projection from the canonical observation."""

    projection_type = COGNITIVE_MEMORY

    def __init__(self, shadow_store: ShadowMemoryStore) -> None:
        self.shadow = shadow_store

    def build(self, event: EventRecord, job: Any, conn: sqlite3.Connection) -> Any:
        from .reconciliation_worker import BuildOutcome, BuildResult

        canonical = CanonicalObservation.from_event(event)
        if not canonical.content.strip():
            return BuildResult(BuildOutcome.SKIPPED.value, reason="event carries no content")

        fields = projected_fields(canonical)
        key = shadow_key(event.event_id, job.projection_version)
        self.shadow.put(
            {
                "shadow_projection_key": key,
                "source_event_id": event.event_id,
                "projection_version": job.projection_version,
                "tenant_id": canonical.tenant_id,
                "workspace_id": canonical.workspace_id,
                "memory_type": fields["memory_type"],
                "content": fields["content"],
                "confidence": fields["confidence"],
                "epistemic_status": fields["epistemic_status"],
                "security_label": fields["security_label"],
                "valid_from": fields["valid_from"],
                "valid_to": fields["valid_to"],
                "provenance": canonical.provenance,
                "metadata": canonical.metadata,
                "canonical_input_hash": canonical.input_hash(),
                "output_hash": output_hash(fields),
            },
            # Commits here, exactly as the production builder's `put()` does.
            # The worker has already inserted the ledger row on this same
            # connection without committing, so this commit makes the ledger
            # and the shadow projection durable together. An earlier version
            # passed commit=False and relied on a later incidental commit —
            # atomicity by accident, and a crash test proved it: the child died
            # with the shadow row still uncommitted and nothing survived.
            commit=True,
        )
        return BuildResult(BuildOutcome.BUILT.value, target_id=key)


def projected_fields(canonical: CanonicalObservation) -> dict[str, Any]:
    """The projection, as a pure function of the canonical input.

    Deterministic by construction — no clock, no randomness, no model call.
    That is what lets the same event replay to the same output hash months
    later, and it is the reason the comparator can treat a difference as a
    real signal rather than noise.
    """
    return {
        "memory_type": MemoryType.EPISODIC.value,
        "content": canonical.content,
        "confidence": 0.6,
        "epistemic_status": canonical.epistemic_status,
        "security_label": canonical.security_label,
        "valid_from": canonical.effective_at,
        "valid_to": None,
        "tenant_id": canonical.tenant_id,
        "workspace_id": canonical.workspace_id,
        "source_event_id": canonical.event_id,
        "projection_version": PROJECTION_VERSION,
    }


# --------------------------------------------------------------------------
# comparator
# --------------------------------------------------------------------------


class ComparisonStatus(str, Enum):
    MATCH = "MATCH"
    MATCH_NORMALIZED = "MATCH_NORMALIZED"
    MISSING_LEGACY = "MISSING_LEGACY"
    MISSING_SHADOW = "MISSING_SHADOW"
    CONTENT_MISMATCH = "CONTENT_MISMATCH"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    EPISTEMIC_MISMATCH = "EPISTEMIC_MISMATCH"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
    SECURITY_MISMATCH = "SECURITY_MISMATCH"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    CONFIDENCE_MISMATCH = "CONFIDENCE_MISMATCH"
    NON_DETERMINISTIC_OUTPUT = "NON_DETERMINISTIC_OUTPUT"
    BUILDER_UNSUPPORTED = "BUILDER_UNSUPPORTED"


#: Normalisations applied before declaring a content difference. Each is
#: documented in docs/v082/SHADOW_MODE.md; anything not listed here is a real
#: difference and is reported as one.
NORMALISATIONS = (
    "unicode NFC",
    "leading and trailing whitespace",
    "internal whitespace runs collapsed to one space",
)


def normalise_text(value: str) -> str:
    collapsed = " ".join(str(value).split())
    return unicodedata.normalize("NFC", collapsed)


@dataclass(slots=True)
class Comparison:
    event_id: str
    status: str
    tenant_id: str | None = None
    field: str | None = None
    legacy_value: Any = None
    shadow_value: Any = None
    canonical_input_hash: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (
            ComparisonStatus.MATCH.value, ComparisonStatus.MATCH_NORMALIZED.value
        )


def compare(
    *,
    event_id: str,
    legacy: dict[str, Any] | None,
    shadow: dict[str, Any] | None,
    canonical_input_hash: str | None = None,
) -> Comparison:
    """Compare one legacy projection against its shadow, field by field.

    Semantic comparison rather than a JSON blob diff: a blob comparison would
    fail on key order and pass on a swapped tenant, which is precisely
    backwards.
    """
    if legacy is None and shadow is None:
        return Comparison(event_id, ComparisonStatus.MISSING_LEGACY.value,
                          detail="neither path produced a projection")
    if shadow is None:
        return Comparison(event_id, ComparisonStatus.MISSING_SHADOW.value,
                          tenant_id=(legacy or {}).get("tenant_id"))
    if legacy is None:
        return Comparison(event_id, ComparisonStatus.MISSING_LEGACY.value,
                          tenant_id=shadow.get("tenant_id"))

    tenant = legacy.get("tenant_id")
    checks: tuple[tuple[str, str], ...] = (
        ("tenant_id", ComparisonStatus.TENANT_MISMATCH.value),
        ("workspace_id", ComparisonStatus.WORKSPACE_MISMATCH.value),
        ("memory_type", ComparisonStatus.TYPE_MISMATCH.value),
        ("epistemic_status", ComparisonStatus.EPISTEMIC_MISMATCH.value),
        ("security_label", ComparisonStatus.SECURITY_MISMATCH.value),
        ("valid_from", ComparisonStatus.TEMPORAL_MISMATCH.value),
        ("valid_to", ComparisonStatus.TEMPORAL_MISMATCH.value),
    )
    for field_name, status in checks:
        if legacy.get(field_name) != shadow.get(field_name):
            return Comparison(event_id, status, tenant_id=tenant, field=field_name,
                              legacy_value=legacy.get(field_name),
                              shadow_value=shadow.get(field_name),
                              canonical_input_hash=canonical_input_hash)

    if legacy.get("source_event_id") != shadow.get("source_event_id"):
        return Comparison(event_id, ComparisonStatus.PROVENANCE_MISMATCH.value,
                          tenant_id=tenant, field="source_event_id",
                          legacy_value=legacy.get("source_event_id"),
                          shadow_value=shadow.get("source_event_id"))

    lc, sc = float(legacy.get("confidence", 0)), float(shadow.get("confidence", 0))
    if abs(lc - sc) > 1e-9:
        return Comparison(event_id, ComparisonStatus.CONFIDENCE_MISMATCH.value,
                          tenant_id=tenant, field="confidence",
                          legacy_value=lc, shadow_value=sc)

    legacy_text, shadow_text = str(legacy.get("content", "")), str(shadow.get("content", ""))
    if legacy_text == shadow_text:
        return Comparison(event_id, ComparisonStatus.MATCH.value, tenant_id=tenant,
                          canonical_input_hash=canonical_input_hash)
    if normalise_text(legacy_text) == normalise_text(shadow_text):
        return Comparison(event_id, ComparisonStatus.MATCH_NORMALIZED.value, tenant_id=tenant,
                          field="content", canonical_input_hash=canonical_input_hash,
                          detail="; ".join(NORMALISATIONS))
    return Comparison(event_id, ComparisonStatus.CONTENT_MISMATCH.value, tenant_id=tenant,
                      field="content", legacy_value=legacy_text[:120],
                      shadow_value=shadow_text[:120],
                      canonical_input_hash=canonical_input_hash)


@dataclass(slots=True)
class ShadowReport:
    observations: int = 0
    legacy_projections: int = 0
    shadow_projections: int = 0
    comparisons: list[Comparison] = field(default_factory=list)
    unsupported: dict[str, str] = field(default_factory=dict)
    legacy_latency_ms: list[float] = field(default_factory=list)
    shadow_latency_ms: list[float] = field(default_factory=list)
    non_deterministic: list[str] = field(default_factory=list)

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.comparisons:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    @property
    def matches(self) -> int:
        return sum(1 for c in self.comparisons if c.ok)

    @property
    def match_rate(self) -> float:
        return self.matches / len(self.comparisons) if self.comparisons else 0.0

    @property
    def clean(self) -> bool:
        """The bar from the plan: every listed mismatch class must be zero."""
        return (
            bool(self.comparisons)
            and self.matches == len(self.comparisons)
            and not self.non_deterministic
        )

    @staticmethod
    def _pct(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(q * len(ordered)))
        return round(ordered[idx], 4)

    def latency(self) -> dict[str, dict[str, float]]:
        return {
            "legacy_observe_ms": {
                "p50": self._pct(self.legacy_latency_ms, 0.50),
                "p95": self._pct(self.legacy_latency_ms, 0.95),
                "p99": self._pct(self.legacy_latency_ms, 0.99),
            },
            "shadow_projection_ms": {
                "p50": self._pct(self.shadow_latency_ms, 0.50),
                "p95": self._pct(self.shadow_latency_ms, 0.95),
                "p99": self._pct(self.shadow_latency_ms, 0.99),
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "legacy_projections": self.legacy_projections,
            "shadow_projections": self.shadow_projections,
            "comparisons": len(self.comparisons),
            "matches": self.matches,
            "match_rate": round(self.match_rate, 6),
            "by_status": self.by_status(),
            "non_deterministic": self.non_deterministic,
            "unsupported_projection_types": self.unsupported,
            "normalisation_rules": list(NORMALISATIONS),
            "latency": self.latency(),
            "clean": self.clean,
        }

    def render(self, width: int = 78) -> str:
        lines = [
            "=" * width,
            "  SHADOW COMPARISON — cognitive_memory",
            "=" * width,
            f"  observations         : {self.observations:>8,}",
            f"  legacy projections   : {self.legacy_projections:>8,}",
            f"  shadow projections   : {self.shadow_projections:>8,}",
            f"  comparisons          : {len(self.comparisons):>8,}",
            f"  matches              : {self.matches:>8,}  ({self.match_rate:.2%})",
            "  " + "-" * (width - 4),
        ]
        for status, n in sorted(self.by_status().items(), key=lambda x: -x[1]):
            lines.append(f"    {status:<28} {n:>6,}")
        lat = self.latency()
        lines.append("  " + "-" * (width - 4))
        for name, stats in lat.items():
            lines.append(
                f"  {name:<22} p50={stats['p50']:>8.3f}  "
                f"p95={stats['p95']:>8.3f}  p99={stats['p99']:>8.3f}"
            )
        if self.unsupported:
            lines.append("  " + "-" * (width - 4))
            lines.append("  UNSUPPORTED (not tested, not passed)")
            for ptype, reason in sorted(self.unsupported.items()):
                lines.append(f"    {ptype:<26} {reason}")
        lines.append("=" * width)
        lines.append(f"  verdict: {'CLEAN' if self.clean else 'NOT CLEAN'}")
        lines.append("=" * width)
        return "\n".join(lines)


__all__ = [
    "COGNITIVE_MEMORY",
    "MODE_ENV",
    "NORMALISATIONS",
    "CanonicalObservation",
    "Comparison",
    "ComparisonStatus",
    "ProjectionMode",
    "ShadowMemoryBuilder",
    "ShadowMemoryStore",
    "ShadowReport",
    "compare",
    "current_mode",
    "normalise_text",
    "output_hash",
    "projected_fields",
    "shadow_key",
]
