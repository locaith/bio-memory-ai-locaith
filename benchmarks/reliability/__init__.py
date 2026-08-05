"""Reliability benchmark for the v0.8.2 projection pipeline.

Measures the path the fault matrix and shadow mode proved *correct*, under
load it has never carried:

    event + outbox → leased worker → target-local ledger
                   → cognitive projection → outbox completion → doctor

Kept out of `bio_agent_os/` on purpose. Nothing here is imported by the
runtime, nothing here adds a dependency, and the instrumented worker is a
subclass rather than a hook in the production one — measurement must not
change what it measures.
"""

from __future__ import annotations

__all__ = ["__doc__"]
