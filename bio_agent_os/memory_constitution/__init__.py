"""Laws a memory may not learn its way out of.

A memory system that learns from everything eventually learns the wrong
things, and the failures are not exotic — they are the failures a human brain
already has. Hear something a thousand times and it starts to feel true. Get
away with something dangerous once and it becomes a habit. Feel certain and
mistake that for having evidence.

This layer is the part of the design that says: keep the useful mechanisms of
biological memory, and put them under rules biology does not have.

Each law carries three things, and all three are required:

    statement   what it means, in words, with the reason it exists
    invariant   an executable check against the running system
    mutant      an implementation that deliberately breaks it

The mutant is not decoration. A check that nothing can fail proves nothing —
this project has shipped two of those already — so every law is tested twice:
the real system must satisfy it, and the mutant must violate it. A law whose
mutant passes is a law that is not being enforced, and the test says so.

These are not guardrails bolted on the outside. They constrain how a memory is
formed in the first place, which is why they live next to the mechanisms rather
than in front of them.
"""

from .laws import (
    LAWS,
    Law,
    LawId,
    Verdict,
    check_all,
    law,
)

__all__ = ["LAWS", "Law", "LawId", "Verdict", "check_all", "law"]
