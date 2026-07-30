"""
Memory systems under evaluation.

Every system implements the same tiny interface so the runner treats
Bio-Agent OS and the baselines identically:

- ``NoMemorySystem`` — the LLM answers from the question alone (floor).
- ``NaiveRagSystem`` — embed every turn, cosine top-k into the prompt
  (the "vector DB as a junk drawer" approach Bio-Agent OS argues against).
- ``BioMemorySystem`` — the full pipeline: hippocampus labeling per turn,
  periodic sleep-cycle consolidation, hybrid retrieval at answer time.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bio_agent_os.evals.locomo import LocomoTurn

ANSWER_INSTRUCTION = (
    "Answer the question using ONLY the context above. Reply with a short, "
    "direct answer of a few words (no explanation). If the context does not "
    "contain the answer, reply exactly: No information available."
)

NO_CONTEXT_INSTRUCTION = (
    "Answer the question with a short, direct answer of a few words. "
    "If you do not know, reply exactly: No information available."
)


def format_turn(turn: LocomoTurn) -> str:
    prefix = f"[{turn.session_datetime}] " if turn.session_datetime else ""
    return f"{prefix}{turn.speaker}: {turn.text}"


class NoMemorySystem:
    """Floor baseline: no access to the conversation at all."""

    name = "no-memory"

    def __init__(self, engine):
        self._engine = engine

    async def ingest_turn(self, turn: LocomoTurn) -> None:
        return None

    async def finalize_ingest(self) -> None:
        return None

    async def answer(self, question: str) -> str:
        prompt = f"Question: {question}\n\n{NO_CONTEXT_INSTRUCTION}"
        return await self._engine.generate(prompt, temperature=0.0)


class NaiveRagSystem:
    """Standard RAG baseline: embed every turn, stuff top-k into the prompt."""

    name = "naive-rag"

    def __init__(self, engine, embedder, top_k: int = 10):
        self._engine = engine
        self._embedder = embedder
        self._top_k = top_k
        self._entries: List[Dict[str, Any]] = []

    async def ingest_turn(self, turn: LocomoTurn) -> None:
        text = format_turn(turn)
        self._entries.append({"text": text, "vector": self._embedder.embed(text)})

    async def finalize_ingest(self) -> None:
        return None

    @staticmethod
    def _dot(left: List[float], right: List[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    async def answer(self, question: str) -> str:
        query_vector = self._embedder.embed(question)
        ranked = sorted(
            self._entries,
            key=lambda entry: self._dot(query_vector, entry["vector"]),
            reverse=True,
        )[: self._top_k]
        context = "\n".join(entry["text"] for entry in ranked)
        prompt = (
            f"Conversation excerpts:\n{context}\n\n"
            f"Question: {question}\n\n{ANSWER_INSTRUCTION}"
        )
        return await self._engine.generate(prompt, temperature=0.0)


class BioMemorySystem:
    """
    The full Bio-Agent OS pipeline, isolated per conversation by workspace.

    LoCoMo sessions happen on different days, so each session boundary maps
    to one sleep cycle ("night"): garbage collection + consolidation. This
    matches the production design where consolidation eligibility is
    measured in nights survived, not turns ingested. ``sleep_every`` adds
    optional intra-session micro-sleeps on top.
    """

    name = "bio-memory"

    def __init__(self, runtime, workspace_id: str, sleep_every: int = 0, top_k: int = 10):
        self._runtime = runtime
        self._workspace_id = workspace_id
        self._sleep_every = max(0, sleep_every)
        self._top_k = top_k
        self._turn_count = 0
        self._current_session: int | None = None

    async def _sleep_cycle(self) -> None:
        self._runtime.gc.run()
        await self._runtime.hippo.consolidate()

    async def ingest_turn(self, turn: LocomoTurn) -> None:
        if self._current_session is not None and turn.session_id != self._current_session:
            await self._sleep_cycle()
        self._current_session = turn.session_id

        await self._runtime.hippo.label_and_store(
            format_turn(turn),
            source=turn.speaker,
            workspace_id=self._workspace_id,
            observation_type="conversation",
        )
        self._turn_count += 1
        if self._sleep_every and self._turn_count % self._sleep_every == 0:
            await self._sleep_cycle()

    async def finalize_ingest(self) -> None:
        await self._sleep_cycle()

    def _retrieve_context(self, question: str):
        retrieval_state = self._runtime.retrieval_service.build_retrieval_state(
            {"workspace_id": self._workspace_id, "query": question}
        )
        retrieval_state["allowed_workspaces"] = [self._workspace_id]
        bundle = self._runtime.retrieval_service.hybrid_retrieve(
            question, retrieval_state, top_k=self._top_k
        )
        sections: List[str] = []
        exact = bundle.get("exact_facts") or {}
        for fact in (exact.get("facts") or [])[:3]:
            sections.append(f"Exact fact [{fact.get('fact_kind')}]: {fact.get('fact_value')}")
        l2_results = bundle.get("l2_results") or []
        for item in l2_results:
            sections.append(f"Memory [{item.get('memory_type')}]: {item.get('content')}")
        # Verbatim episodes, untruncated. Clipping them at 400 chars threw away
        # the tail of longer turns — where a bridging fact often sits — while
        # the naive-RAG baseline was handed every turn in full. That handicap
        # was ours alone, and it hurt exactly the multi-hop chains.
        for episode in (bundle.get("anchor_episodes") or [])[: self._top_k]:
            sections.append(f"Episode: {str(episode.get('raw_payload', ''))}")
        return "\n".join(sections), l2_results

    @staticmethod
    def _content_tokens(text: str) -> set:
        return {tok for tok in str(text).lower().split() if len(tok) >= 4}

    async def answer(self, question: str) -> str:
        context, l2_results = self._retrieve_context(question)
        if not context.strip():
            context = "(no relevant memories found)"
        prompt = (
            f"Recalled memories:\n{context}\n\n"
            f"Question: {question}\n\n{ANSWER_INSTRUCTION}"
        )
        response = await self._runtime.engine.generate(prompt, temperature=0.0)

        # Retrieval-aware forgetting: tag the L2 memories that were returned,
        # and credit (success-gate) those whose content actually surfaced in
        # the answer. Lexical-only — no extra LLM call on the hot path.
        if l2_results:
            answer_tokens = self._content_tokens(response)
            abstained = "no information" in response.lower()
            returned_ids = [item.get("entry_id") for item in l2_results if item.get("entry_id")]
            helped_ids = []
            if not abstained and answer_tokens:
                for item in l2_results:
                    eid = item.get("entry_id")
                    if eid and self._content_tokens(item.get("content", "")) & answer_tokens:
                        helped_ids.append(eid)
            self._runtime.l2.note_access(returned_ids, helped_ids)
        return response
