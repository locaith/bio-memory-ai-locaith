"""The layer that actually ships, on the same benchmark as everything else.

Until now LoCoMo measured `BioMemorySystem` — the biological stack, with L1/L2,
episodes and a real embedder. That is not what a user gets. What Claude Code
runs is `cognitive/`: the transactional layer, the one nine canary runs hardened,
and until today it had no semantic representation at all.

So every published number described a layer nobody was running, and the layer
everybody was running had never been scored. That gap is the credibility
problem, not the scores themselves.

This closes it. Same harness, same questions, same answering engine, same
embedder — the difference is only which memory is under test.

    naive-rag           embed every turn, stuff top-k in the prompt
    bio-memory          the biological stack
    cognitive           what ships
    mem0                the competitor

Embeddings are backfilled between ingest and answering, never during it, which
is the same rule the write path keeps in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.semantic_index import backfill_embeddings, calibrate, coverage
from bio_agent_os.evals.locomo import LocomoTurn
from bio_agent_os.evals.systems import ANSWER_INSTRUCTION, format_turn


class CognitiveMemorySystem:
    """`cognitive/` as a benchmark subject: observe, remember, backfill, recall."""

    name = "cognitive"

    def __init__(self, engine, embedder, *, db_path: str | Path,
                 workspace_id: str, top_k: int = 10,
                 backfill_batch: int = 200) -> None:
        self._engine = engine
        self._embedder = embedder
        self._workspace_id = workspace_id
        self._top_k = top_k
        self._batch = backfill_batch
        path = Path(db_path)
        # SQLite will not create the directory for you; it reports "unable to
        # open database file", which reads like a permissions problem.
        path.parent.mkdir(parents=True, exist_ok=True)
        self._os = MemoryOS(path, embedder=embedder)
        self._ctx = AccessContext(tenant_id="locomo", workspace_id=workspace_id)
        self.stats: dict[str, Any] = {}

    async def ingest_turn(self, turn: LocomoTurn) -> None:
        text = format_turn(turn)
        event = self._os.observe(
            tenant_id="locomo", actor=turn.speaker, source="conversation",
            content=text, workspace_id=self._workspace_id,
        )
        # Episodic: a conversation turn is something that happened, not a
        # standing fact. The distinction matters to the temporal scoring this
        # layer already does.
        self._os.remember(event=event, memory_type=MemoryType.EPISODIC,
                          content=text, confidence=0.8)

    async def finalize_ingest(self) -> None:
        """Vectors are produced here — after the writes, before any question.

        Deliberately not during `ingest_turn`: the whole design keeps model
        calls off the write path, and a benchmark that quietly broke that rule
        would be measuring a system nobody would deploy.
        """
        total = 0
        while True:
            written = backfill_embeddings(
                self._os.memories.conn, self._embedder, limit=self._batch
            )
            total += written
            if written == 0:
                break
        # Measure this embedder's own notion of "unrelated" before any question
        # is asked. A floor written down for one model destroys another: 0.64
        # was derived on gemini-embedding-001, whose unrelated baseline is
        # ~0.58, and applied unchanged to text-embedding-3-small, whose
        # baseline is ~0.08. It rejected genuine matches at 0.574 and scored
        # this system 0.084 on LoCoMo against 0.41 for the same content with no
        # floor at all.
        calibration = calibrate(self._os.memories.conn)
        self.stats = {"embedded": total, "calibration": calibration,
                      **coverage(self._os.memories.conn)}

    def _retrieve_context(self, question: str) -> str:
        results = self._os.recall(question, context=self._ctx, limit=self._top_k)
        lines: List[str] = []
        for item in results:
            lines.append(f"Memory: {item.memory.content}")
        return "\n".join(lines)

    async def answer(self, question: str) -> str:
        context = self._retrieve_context(question)
        prompt = (
            f"Context:\n{context or '(no memories retrieved)'}\n\n"
            f"Question: {question}\n\n{ANSWER_INSTRUCTION}"
        )
        return await self._engine.generate(prompt, temperature=0.0)

    def close(self) -> None:
        self._os.close()


__all__ = ["CognitiveMemorySystem"]
