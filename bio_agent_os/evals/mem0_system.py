"""mem0 as a fourth system in the LoCoMo harness.

The point is a comparison that cannot be argued with. Quoting mem0's published
LoCoMo numbers against ours would compare two different experiments: they answer
with GPT-4o-mini, this repo answers with gemma4:12b, and the answering model
moves the score more than the memory does. So mem0 runs here, on the same
questions, through the same runner, and — critically — **answers through the
same engine as every other system**.

That last part is what makes it fair, and it is also what makes it honest in the
other direction: mem0 is being used for what it is for, storing and retrieving
memories, and is not being handicapped by having a weaker model write its
answers than ours gets.

Two configurations, and both should be run:

    local   mem0 driven by Ollama, nothing leaves the machine, costs nothing.
            The result is contestable — mem0 will reasonably say they were not
            run the way they recommend.
    cloud   mem0 driven by the models they document. Costs money, and the
            result is the one that survives an argument.

Telemetry is switched off. mem0 ships a posthog client, and these are somebody's
conversations.
"""

from __future__ import annotations

import os
from typing import Any, List

from bio_agent_os.evals.locomo import LocomoTurn
from bio_agent_os.evals.systems import ANSWER_INSTRUCTION, format_turn

# Set before mem0 is imported anywhere: it reads these at import time.
os.environ.setdefault("MEM0_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "1")


def local_config(*, ollama_url: str = "http://localhost:11434",
                 model: str = "gemma4:12b",
                 embed_model: str = "nomic-embed-text",
                 embed_dims: int = 768,
                 store_dir: str = "data/evals/mem0") -> dict[str, Any]:
    """mem0 with nothing leaving the machine."""
    return {
        "llm": {
            "provider": "ollama",
            "config": {"model": model, "temperature": 0.1,
                       "ollama_base_url": ollama_url},
        },
        "embedder": {
            "provider": "ollama",
            "config": {"model": embed_model, "ollama_base_url": ollama_url},
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": store_dir, "on_disk": True,
                       "embedding_model_dims": embed_dims},
        },
    }


def gemini_config(*, model: str = "gemini-2.5-flash",
                  embed_model: str = "models/gemini-embedding-001",
                  embed_dims: int = 3072,
                  store_dir: str = "data/evals/mem0-gemini") -> dict[str, Any]:
    """mem0 on Gemini — a real hosted model, and no GPU.

    Worth having as its own profile rather than folding into `cloud_config`:
    it is the configuration this project can actually run today, it keeps the
    machine's GPU (and its unreplaced power supply) out of the experiment
    entirely, and it is close enough to how mem0 is meant to be run that the
    result is much harder to wave away than the Ollama one.

    Model names are pinned to what this key actually serves, checked against
    `models.list()` rather than taken from documentation: `gemini-2.0-flash` and
    `text-embedding-004` both 404 as of 2026-08-12 — retired — and the failure
    is silent in the place it matters, because mem0 catches the embedding error
    and simply stores nothing. Two turns went in, zero memories came out, and
    the benchmark would have scored mem0 at zero for a dead model name.

    Stable releases on purpose, not `-latest` or `-preview`: a number that
    cannot be reproduced in six months is not evidence.

    Needs GEMINI_API_KEY.
    """
    return {
        "llm": {"provider": "gemini",
                "config": {"model": model, "temperature": 0.1}},
        "embedder": {"provider": "gemini",
                     "config": {"model": embed_model,
                                "embedding_dims": embed_dims}},
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": store_dir, "on_disk": True,
                       "embedding_model_dims": embed_dims},
        },
    }


def cloud_config(*, model: str = "gpt-4o-mini",
                 embed_model: str = "text-embedding-3-small",
                 embed_dims: int = 1536,
                 store_dir: str = "data/evals/mem0-cloud") -> dict[str, Any]:
    """mem0 the way mem0 documents it. Needs OPENAI_API_KEY."""
    return {
        "llm": {"provider": "openai", "config": {"model": model, "temperature": 0.1}},
        "embedder": {"provider": "openai", "config": {"model": embed_model}},
        "vector_store": {
            "provider": "qdrant",
            "config": {"path": store_dir, "on_disk": True,
                       "embedding_model_dims": embed_dims},
        },
    }


class Mem0System:
    """Stores each turn in mem0; answers through the shared engine.

    `user_id` is the conversation, so retrieval cannot reach across
    conversations — the same isolation `BioMemorySystem` gets from
    `workspace_id`. Without it every system would be scored on a corpus the
    others could not see.
    """

    name = "mem0"

    def __init__(self, engine, *, user_id: str, config: dict[str, Any],
                 top_k: int = 10, batch: int = 1) -> None:
        from mem0 import Memory

        self._engine = engine
        self._user_id = user_id
        self._top_k = top_k
        self._memory = Memory.from_config(config)
        self._batch = max(1, batch)
        self._buffer: List[str] = []
        self.errors: List[str] = []

    async def _flush(self) -> None:
        if not self._buffer:
            return
        text = "\n".join(self._buffer)
        self._buffer = []
        try:
            # mem0's add() is synchronous and does its own LLM extraction.
            self._memory.add(text, user_id=self._user_id)
        except Exception as exc:                      # a failure is a result
            self.errors.append(f"add: {type(exc).__name__}: {exc}"[:300])

    async def ingest_turn(self, turn: LocomoTurn) -> None:
        self._buffer.append(format_turn(turn))
        if len(self._buffer) >= self._batch:
            await self._flush()

    async def finalize_ingest(self) -> None:
        await self._flush()

    def _retrieve_context(self, question: str) -> str:
        try:
            # mem0 2.x moved scoping out of kwargs and into `filters`. The old
            # form does not warn — it raises, and a caught exception returns an
            # empty context, which scores mem0 at zero for a reason that has
            # nothing to do with mem0. Caught by a two-turn smoke test before
            # any benchmark ran.
            found = self._memory.search(
                question, filters={"user_id": self._user_id}, limit=self._top_k
            )
        except TypeError:
            # Older mem0 releases take the keyword form.
            found = self._memory.search(question, user_id=self._user_id,
                                        limit=self._top_k)
        except Exception as exc:
            self.errors.append(f"search: {type(exc).__name__}: {exc}"[:300])
            return ""
        results = found.get("results", found) if isinstance(found, dict) else found
        lines = []
        for item in (results or []):
            memory = item.get("memory") if isinstance(item, dict) else str(item)
            if memory:
                lines.append(f"Memory: {memory}")
        return "\n".join(lines)

    async def answer(self, question: str) -> str:
        context = self._retrieve_context(question)
        prompt = (
            f"Context:\n{context or '(no memories retrieved)'}\n\n"
            f"Question: {question}\n\n{ANSWER_INSTRUCTION}"
        )
        return await self._engine.generate(prompt, temperature=0.0)


__all__ = ["Mem0System", "cloud_config", "gemini_config", "local_config"]
