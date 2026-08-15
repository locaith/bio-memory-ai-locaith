"""The seam between the lifetime benchmark and a memory system.

Five methods. The list is short because every extra one is another way for the
answer key to reach the system being measured:

    ingest(event)          one world event, at its tick
    query(question, tick)  what the system says
    forget(scope)          a deletion request
    snapshot()             size and cost, for the curve
    reset()                a clean store

The benchmark keeps its ledger entirely to itself. Nothing that crosses this
seam carries the truth, the expected answer, or even which *kind* of event is
being handed over — a system told "this one is a SUPERSEDE" has been given the
distinction it was supposed to work out. `test_the_adapter_is_never_told_the_
answer` records everything that crosses and checks, because a promise in a
docstring is not a boundary.

**Two scores, not one.** Every question is graded twice:

    retrieval_hit    did the right value appear in what was retrieved
    answer_correct   did the model's answer contain it

One end-to-end number cannot separate "the memory did not find it" from "the
model was handed it and answered badly", and those need different fixes. It
also means the first look at a system costs nothing: with no engine configured,
`query()` returns what was retrieved and an empty answer, and the retrieval
column is fully scorable offline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .lifetime_world import WorldEvent, tick_to_iso


def _normalise(text: str) -> str:
    import re
    import unicodedata

    return " ".join(
        unicodedata.normalize("NFC", str(text or "")).lower().split())


def _covered(content: str, words: list[str], threshold: float = 0.5) -> bool:
    """Does this memory fall under a request naming these words?

    The same rule `behaviour_benchmark.matches_delete_request` uses. Stated
    once, in one place, because a deletion and its verification disagreeing
    about scope is how a leak survives a clean report.
    """
    low = _normalise(content)
    return sum(1 for w in words if w in low) / len(words) >= threshold


@dataclass
class QueryResult:
    retrieved: list[str] = field(default_factory=list)
    answer: str = ""
    route: str = ""
    query_kind: str = ""
    latency_ms: float = 0.0
    prompt_chars: int = 0


class MemorySystem(Protocol):
    """What the benchmark is allowed to ask of a system under test."""

    def ingest(self, event: WorldEvent) -> None: ...

    def query(self, question: str, tick: int) -> QueryResult: ...

    def forget(self, scope: str) -> dict[str, Any]: ...

    def snapshot(self) -> dict[str, Any]: ...

    def reset(self) -> None: ...


class CognitiveAdapter:
    """`bio_agent_os.cognitive` behind the five methods.

    Holds no reference to the ledger and is never handed one.
    """

    def __init__(self, db_path: Path | str, *, embedder: Any = None,
                 engine: Any = None, tenant: str = "bench",
                 workspace: str = "life") -> None:
        from bio_agent_os.cognitive.facade import MemoryOS

        self.db_path = Path(db_path)
        self.tenant = tenant
        self.workspace = workspace
        self.embedder = embedder
        self.engine = engine
        self.memory_os = MemoryOS(self.db_path, embedder=embedder)

    # -- the five ----------------------------------------------------------

    def ingest(self, event: WorldEvent) -> None:
        """Store one world event.

        Only the text and its world time cross. Not `event.kind`, not
        `event.value`, not the subject id — those are the ledger's way of
        describing what happened, and handing them over would let the system
        skip the part it is being measured on.

        `observed_at` comes from the tick, never the wall clock: a thousand
        events across two simulated years otherwise all land aged zero, and
        every age-dependent mechanism measures nothing.
        """
        from bio_agent_os.cognitive.models import MemoryType

        text = str(event.text or "").strip()
        if not text:
            return
        stamp = tick_to_iso(event.tick)
        record = self.memory_os.observe(
            tenant_id=self.tenant, actor="world", source="lifetime",
            content=text, workspace_id=self.workspace, observed_at=stamp,
        )
        self.memory_os.remember(
            event=record, memory_type=MemoryType.EPISODIC, content=text,
            confidence=0.8,
        )

    def query(self, question: str, tick: int) -> QueryResult:
        """Ask, and report both what was retrieved and what was said.

        With no engine configured the answer stays empty and only the retrieval
        column is scorable — which is the cheap, model-free first look.
        """
        import time

        from bio_agent_os.cognitive.models import AccessContext
        from bio_agent_os.cognitive.query_planner import plan

        context = AccessContext(tenant_id=self.tenant,
                                workspace_id=self.workspace)
        started = time.perf_counter()
        planned = plan(self.memory_os, question, context=context, limit=6)
        contents = [str(getattr(m, "content", "")) for m in planned.memories]
        result = QueryResult(
            retrieved=contents,
            route=planned.used,
            query_kind=planned.kind.value,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        if self.engine is None:
            return result

        prompt = self._prompt(question, contents)
        result.prompt_chars = len(prompt)
        result.answer = self._generate(prompt)
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result

    def forget(self, scope: str) -> dict[str, Any]:
        """A deletion request, phrased the way a person phrases one.

        Goes through `forget_scope`, which resolves the request into a subject
        and optionally a topic before touching anything. The literal matcher it
        replaces found nothing on fifteen of eighteen requests in the first
        lifetime run, because "Hãy quên thông tin về phone của Trần An" is not
        a substring of "Số điện thoại của Trần An là 0912345678".

        `memories_deleted` is kept in the returned dict under its old name so
        the runner's counter keeps meaning the same thing across the before and
        after runs — a metric that changes definition between two measurements
        cannot be compared.
        """
        from bio_agent_os.cognitive.forget_scope import forget_scoped

        result = forget_scoped(self.memory_os, scope, actor="benchmark")
        payload = result.as_dict()
        payload["memories_deleted"] = result.deleted_claims
        return payload

    def snapshot(self) -> dict[str, Any]:
        """Size and cost. `memory_bloat` and `retrieval_cost` come from here."""
        conn = self.memory_os.memories.conn
        memories = conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        try:
            embedded = conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        except sqlite3.OperationalError:
            embedded = 0
        size = 0
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                size += path.stat().st_size
        return {"memories": int(memories), "embedded": int(embedded),
                "bytes": int(size)}

    def reset(self) -> None:
        """A clean store. Checkpoints re-run from scratch, and a leftover row
        would score for work this run did not do."""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                path.unlink()
        from bio_agent_os.cognitive.facade import MemoryOS

        self.memory_os = MemoryOS(self.db_path, embedder=self.embedder)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _prompt(question: str, memories: list[str]) -> str:
        from bio_agent_os.evals.systems import ANSWER_INSTRUCTION

        block = "\n".join(f"Memory: {m}" for m in memories) or "(no memories)"
        return f"Context:\n{block}\n\nQuestion: {question}\n\n{ANSWER_INSTRUCTION}"

    def _generate(self, prompt: str) -> str:
        import asyncio
        import inspect

        try:
            out = self.engine.generate(prompt, temperature=0.0)
            if inspect.isawaitable(out):
                out = asyncio.run(out)
            return str(out).strip()
        except Exception as exc:                       # noqa: BLE001
            # A model failure is a data point, not a crash: the run continues
            # and the trace records why this question has no answer.
            return f"[engine error: {type(exc).__name__}]"

    def close(self) -> None:
        try:
            self.memory_os.close()
        except Exception:                              # noqa: BLE001
            pass


__all__ = ["CognitiveAdapter", "MemorySystem", "QueryResult"]
