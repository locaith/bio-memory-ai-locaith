"""What the projection pipeline can actually do, declared rather than assumed.

The fault matrix exposed a gap it would have been easy to dress up: four of the
five projection types have no builder, so a job for one of them dead-lettered
with "no builder registered". That is the right *runtime* behaviour for a job
that should never have existed, but dead-letter is the wrong *word* — it reads
as a failure when the truth is a missing capability.

This registry states the difference. Nothing enqueues work for a type that
cannot be built, and every report says `unsupported` rather than `passed`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .projection_registry import REGISTRY, ProjectionType, spec


@dataclass(frozen=True, slots=True)
class ProjectionCapability:
    projection_type: str
    builder_available: bool
    replayable: bool
    shadow_supported: bool
    target_store: str | None
    projection_version: int | None
    reason: str | None = None

    @property
    def status(self) -> str:
        return "supported" if self.builder_available else "unsupported"


def _capability(ptype: ProjectionType, *, builder: bool, shadow: bool,
                reason: str | None) -> ProjectionCapability:
    declared = spec(ptype)
    return ProjectionCapability(
        projection_type=ptype.value,
        builder_available=builder,
        replayable=declared.replayable if builder else False,
        shadow_supported=shadow,
        target_store=declared.target_store if builder else None,
        projection_version=declared.version if builder else None,
        reason=reason,
    )


#: The honest state of the system as of v0.8.2.
#:
#: `replayable` is reported False for every type without a builder: a type that
#: cannot be built cannot be rebuilt either, whatever the dependency registry
#: declares about its semantics. checkpoint_reference stays False for its own
#: separate reason — it pins a memory version.
CAPABILITIES: dict[str, ProjectionCapability] = {
    ProjectionType.COGNITIVE_MEMORY.value: _capability(
        ProjectionType.COGNITIVE_MEMORY, builder=True, shadow=True, reason=None
    ),
    ProjectionType.SELF_MODEL_UPDATE.value: _capability(
        ProjectionType.SELF_MODEL_UPDATE, builder=False, shadow=False,
        reason="no builder implemented; scheduled for v0.8.3",
    ),
    ProjectionType.PROSPECTIVE_MEMORY.value: _capability(
        ProjectionType.PROSPECTIVE_MEMORY, builder=False, shadow=False,
        reason="no builder implemented; scheduled for v0.8.3",
    ),
    ProjectionType.CONTEXT_BLOCK.value: _capability(
        ProjectionType.CONTEXT_BLOCK, builder=False, shadow=False,
        reason="no builder implemented; depends on cognitive_memory",
    ),
    ProjectionType.CHECKPOINT_REFERENCE.value: _capability(
        ProjectionType.CHECKPOINT_REFERENCE, builder=False, shadow=False,
        reason="no builder implemented; pins a memory version and is not replayable",
    ),
    #: `shadow_supported=False` is the honest value, and it does **not** mean
    #: what the join plan calls "running in the shadow". Shadow mode here
    #: compares a projection against a legacy implementation of the same thing;
    #: there has never been a legacy hippocampus label, so there is nothing to
    #: compare against and claiming otherwise would fabricate a comparison.
    #: The plan's sense — written, but never read for ranking — is enforced by
    #: nothing in retrieval reading `hippocampus_labels`, and holds until
    #: Phase 3 shows the labels make answers better.
    ProjectionType.HIPPOCAMPUS_LABEL.value: _capability(
        ProjectionType.HIPPOCAMPUS_LABEL, builder=True, shadow=False,
        reason="deterministic builder only; the model upgrades labels "
               "asynchronously and is never on the write path",
    ),
}


def capability(projection_type: str) -> ProjectionCapability | None:
    return CAPABILITIES.get(projection_type)


def is_supported(projection_type: str) -> bool:
    cap = capability(projection_type)
    return bool(cap and cap.builder_available)


def shadow_supported(projection_type: str) -> bool:
    cap = capability(projection_type)
    return bool(cap and cap.shadow_supported)


def supported_types() -> tuple[str, ...]:
    return tuple(k for k, v in CAPABILITIES.items() if v.builder_available)


def unsupported_types() -> tuple[str, ...]:
    return tuple(k for k, v in CAPABILITIES.items() if not v.builder_available)


def shadow_types() -> tuple[str, ...]:
    return tuple(k for k, v in CAPABILITIES.items() if v.shadow_supported)


def enqueueable(requested: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a request into what can be built and what cannot.

    Returns `(supported, skipped)`. The caller enqueues the first and reports
    the second — creating a job that can only dead-letter is noise, not safety.
    """
    supported, skipped = [], []
    for ptype in requested:
        (supported if is_supported(ptype) else skipped).append(ptype)
    return tuple(supported), tuple(skipped)


def report() -> list[dict[str, object]]:
    """Machine-readable capability table for the CLI, doctor and docs."""
    return [
        {
            "projection_type": c.projection_type,
            "status": c.status,
            "builder_available": c.builder_available,
            "replayable": c.replayable,
            "shadow_supported": c.shadow_supported,
            "target_store": c.target_store,
            "projection_version": c.projection_version,
            "reason": c.reason,
        }
        for c in sorted(CAPABILITIES.values(), key=lambda x: x.projection_type)
    ]


def render(width: int = 78) -> str:
    lines = [
        "=" * width,
        "  PROJECTION CAPABILITIES",
        "=" * width,
        f"  {'type':<24} {'status':<12} {'shadow':<8} {'replay':<7} store",
        "  " + "-" * (width - 4),
    ]
    for c in sorted(CAPABILITIES.values(), key=lambda x: x.projection_type):
        lines.append(
            f"  {c.projection_type:<24} {c.status:<12} "
            f"{'yes' if c.shadow_supported else 'no':<8} "
            f"{'yes' if c.replayable else 'no':<7} {c.target_store or '-'}"
        )
        if c.reason:
            lines.append(f"      {c.reason}")
    lines.append("=" * width)
    lines.append(
        f"  {len(supported_types())} supported, {len(unsupported_types())} unsupported"
    )
    lines.append("=" * width)
    return "\n".join(lines)


__all__ = [
    "CAPABILITIES",
    "ProjectionCapability",
    "capability",
    "enqueueable",
    "is_supported",
    "render",
    "report",
    "shadow_supported",
    "shadow_types",
    "supported_types",
    "unsupported_types",
]
