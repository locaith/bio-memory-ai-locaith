"""What an event owes, and in what order.

`WRITE_PATH_ANALYSIS.md` found six stores behind six connections. Only one of
them — the memory projection — was ever discussed as owing work to an event.
The others owe work too, and two of them owe it *transitively*: a context
block references a memory, a checkpoint references a memory version. Building
those before their parent exists produces a dangling reference that no check
currently reports.

This registry makes the shape explicit:

    event
    ├── cognitive_memory          (no dependency)
    ├── self_model_update         (no dependency)
    ├── prospective_memory        (no dependency)
    ├── context_block             (depends on cognitive_memory)
    └── checkpoint_reference      (depends on cognitive_memory)

A worker consults this before claiming: a job whose parent is not yet complete
is left in `PENDING_DEPENDENCY` rather than attempted and failed. That state
exists so "waiting for something else" is never confused with "broken" or,
worse, with "done".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ProjectionType(str, Enum):
    COGNITIVE_MEMORY = "cognitive_memory"
    SELF_MODEL_UPDATE = "self_model_update"
    PROSPECTIVE_MEMORY = "prospective_memory"
    CONTEXT_BLOCK = "context_block"
    CHECKPOINT_REFERENCE = "checkpoint_reference"


class DependencyState(str, Enum):
    """Why a job is not being worked on right now."""

    READY = "ready"
    #: Parent projection has not completed. Not a failure, not a success.
    PENDING_DEPENDENCY = "pending_dependency"
    #: Parent reached dead-letter, so this can never become ready.
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Everything the pipeline needs to know about one projection type."""

    projection_type: ProjectionType
    version: int
    #: Store that owns the resulting rows, for diagnostics and repair plans.
    target_store: str
    #: Must be COMPLETED before this type may be claimed.
    depends_on: tuple[ProjectionType, ...] = ()
    #: A projection that cannot be rebuilt from the event alone is replay-unsafe
    #: and must be reported rather than silently regenerated.
    replayable: bool = True
    description: str = ""

    @property
    def has_dependencies(self) -> bool:
        return bool(self.depends_on)


#: The registry. Versions are per-type on purpose: changing how context blocks
#: are built should not force every memory projection to be rebuilt.
REGISTRY: dict[ProjectionType, ProjectionSpec] = {
    ProjectionType.COGNITIVE_MEMORY: ProjectionSpec(
        projection_type=ProjectionType.COGNITIVE_MEMORY,
        version=1,
        target_store="memories",
        description="The retrievable memory built from an event's payload.",
    ),
    ProjectionType.SELF_MODEL_UPDATE: ProjectionSpec(
        projection_type=ProjectionType.SELF_MODEL_UPDATE,
        version=1,
        target_store="self_model",
        description="Capability and outcome learning derived from an event.",
    ),
    ProjectionType.PROSPECTIVE_MEMORY: ProjectionSpec(
        projection_type=ProjectionType.PROSPECTIVE_MEMORY,
        version=1,
        target_store="prospective",
        description="Future-intent triggers extracted from an event.",
    ),
    ProjectionType.CONTEXT_BLOCK: ProjectionSpec(
        projection_type=ProjectionType.CONTEXT_BLOCK,
        version=1,
        target_store="context_blocks",
        depends_on=(ProjectionType.COGNITIVE_MEMORY,),
        description="Compiled context referencing a memory; dangles without it.",
    ),
    ProjectionType.CHECKPOINT_REFERENCE: ProjectionSpec(
        projection_type=ProjectionType.CHECKPOINT_REFERENCE,
        version=1,
        target_store="checkpoints",
        depends_on=(ProjectionType.COGNITIVE_MEMORY,),
        #: A checkpoint pins a specific memory *version*. Rebuilding it from
        #: the event alone would silently repoint it at a newer version, which
        #: is not the same checkpoint. Repair must be reported, not automatic.
        replayable=False,
        description="Agent checkpoint pinned to a memory version.",
    ),
}


def spec(projection_type: ProjectionType | str) -> ProjectionSpec:
    key = ProjectionType(projection_type)
    return REGISTRY[key]


def current_version(projection_type: ProjectionType | str) -> int:
    return spec(projection_type).version


def dependencies_of(projection_type: ProjectionType | str) -> tuple[ProjectionType, ...]:
    return spec(projection_type).depends_on


def dependents_of(projection_type: ProjectionType | str) -> tuple[ProjectionType, ...]:
    target = ProjectionType(projection_type)
    return tuple(s.projection_type for s in REGISTRY.values() if target in s.depends_on)


def topological_order() -> list[ProjectionType]:
    """Types in an order where every dependency precedes its dependents.

    Raises on a cycle rather than returning a partial order — a cycle in this
    table is a design error, and silently dropping the tail would produce
    projections that never run and never report why.
    """
    ordered: list[ProjectionType] = []
    visiting: set[ProjectionType] = set()
    done: set[ProjectionType] = set()

    def visit(node: ProjectionType, trail: tuple[ProjectionType, ...]) -> None:
        if node in done:
            return
        if node in visiting:
            cycle = " -> ".join(n.value for n in (*trail, node))
            raise ValueError(f"projection dependency cycle: {cycle}")
        visiting.add(node)
        for parent in REGISTRY[node].depends_on:
            visit(parent, (*trail, node))
        visiting.discard(node)
        done.add(node)
        ordered.append(node)

    for node in REGISTRY:
        visit(node, ())
    return ordered


def detect_cycles() -> list[str]:
    """Cycle descriptions, empty when the registry is well formed. Used by
    `doctor --deep`, which must report rather than raise."""
    try:
        topological_order()
    except ValueError as exc:
        return [str(exc)]
    return []


def default_projection_types() -> tuple[str, ...]:
    """Types enqueued for an ordinary event when the caller does not choose.

    Only the memory projection: the others are opt-in because an event does
    not always imply a self-model update or a prospective trigger, and
    enqueueing work nobody wants would make the queue lag meaningless.
    """
    return (ProjectionType.COGNITIVE_MEMORY.value,)


__all__ = [
    "REGISTRY",
    "DependencyState",
    "ProjectionSpec",
    "ProjectionType",
    "current_version",
    "default_projection_types",
    "dependencies_of",
    "dependents_of",
    "detect_cycles",
    "spec",
    "topological_order",
]
