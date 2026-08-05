"""Deterministic fault points for crash-recovery testing.

Crash tests that use `sleep()` and then kill a process are guessing about
where that process got to. When they pass they prove nothing in particular,
and when they fail nobody can tell whether the code or the timing was wrong.

Instead the pipeline calls `fire()` at named points. A test installs a hook
that signals the parent and then kills the process *at that exact instruction*.
Nothing is timed and nothing is assumed.

Production cost is one `is None` check per point. No hook is installed unless a
test installs one, and `armed()` reports whether any is — used to assert that
production paths run unarmed.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable


class ProjectionFaultPoint(str, Enum):
    """Every place a crash would leave a different state behind."""

    # event + outbox, one transaction
    BEFORE_EVENT_TRANSACTION = "before_event_transaction"
    AFTER_EVENT_INSERT = "after_event_insert"
    AFTER_OUTBOX_INSERT = "after_outbox_insert"
    AFTER_EVENT_COMMIT = "after_event_commit"

    # worker lifecycle
    BEFORE_CLAIM = "before_claim"
    AFTER_CLAIM = "after_claim"
    AFTER_EVENT_LOAD = "after_event_load"

    # target transaction: ledger and projection commit together
    BEFORE_LEDGER_INSERT = "before_ledger_insert"
    AFTER_LEDGER_INSERT = "after_ledger_insert"
    BEFORE_PROJECTION_WRITE = "before_projection_write"
    AFTER_PROJECTION_WRITE_BEFORE_COMMIT = "after_projection_write_before_commit"
    AFTER_PROJECTION_COMMIT = "after_projection_commit"

    # completion
    BEFORE_OUTBOX_COMPLETE = "before_outbox_complete"
    AFTER_OUTBOX_COMPLETE = "after_outbox_complete"


#: Installed only by tests. `None` in every production path.
_HOOK: Callable[[str], None] | None = None


def install(hook: Callable[[str], None] | None) -> None:
    global _HOOK
    _HOOK = hook


def clear() -> None:
    install(None)


def armed() -> bool:
    """Whether a fault hook is active. Production asserts this stays False."""
    return _HOOK is not None


def fire(point: ProjectionFaultPoint | str) -> None:
    """Reached a named point. Does nothing unless a test armed a hook."""
    if _HOOK is None:
        return
    _HOOK(point.value if isinstance(point, ProjectionFaultPoint) else str(point))


__all__ = ["ProjectionFaultPoint", "armed", "clear", "fire", "install"]
