"""
Embedding backends for production-ready retrieval.

Design rule (local-first, no silent hallucination):
- The chat LLM endpoint (LLM_BASE_URL / LLM_API_KEY) is NEVER treated as an
  embeddings endpoint. Conflating them silently routed local-first setups into
  a dead "hash" fallback whose vectors are semantically meaningless, which made
  retrieval return near-random results while *looking* healthy.
- Embeddings are configured independently via EMBEDDING_* env vars, with an
  on-device model preferred when available.
- When no real backend can serve embeddings we still return a deterministic
  hash vector so the process keeps running, but we say so LOUDLY (logging +
  the `degraded` flag + `effective_backend`), never silently.
"""

import hashlib
import logging
import math
import os
from collections import OrderedDict
from typing import List, Optional

logger = logging.getLogger("bio_agent_os.embedder")


class Embedder:
    def __init__(self):
        self.backend = self._detect_backend()
        self.model_id = self._default_model()
        self._client = None
        self._model = None
        self._dimensions = self._default_dimensions()
        self._fallback_backend = "hash"
        self._fallback_active = False
        self._degraded_warned = False
        # Small LRU over embeddings. A single recall fans out across episodes,
        # L2 and the coverage index — all of which embed the SAME query text.
        # Without this, one recall pays 3 sequential round trips to the
        # embedding backend (they serialize behind one model), which dominates
        # recall latency; with it, only the first call is paid.
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._cache_max = int(os.getenv("EMBEDDING_CACHE_SIZE", "512"))
        self._init_backend()
        if self.backend == "hash" or self._fallback_active:
            logger.warning(
                "[Embedder] No real embedding backend active — semantic search "
                "is DEGRADED (hash mode, near-random recall). Fix one of: "
                "`pip install sentence-transformers` for on-device embeddings, "
                "or set EMBEDDING_BASE_URL / EMBEDDING_API_KEY for an "
                "OpenAI-compatible embeddings endpoint."
            )

    def _detect_backend(self) -> str:
        backend = os.getenv("EMBEDDING_BACKEND", "").strip().lower()
        if backend:
            return backend
        # Dedicated embeddings config → OpenAI-compatible embeddings endpoint.
        # This covers both api.openai.com and a local embeddings server (e.g.
        # LM Studio / vLLM serving an /embeddings route).
        if os.getenv("EMBEDDING_BASE_URL") or os.getenv("EMBEDDING_API_KEY"):
            return "openai"
        # LOCAL-FIRST: prefer an on-device model when available, BEFORE guessing
        # any cloud provider. This is what keeps an offline box semantically
        # real instead of silently degrading to hash.
        try:
            import sentence_transformers  # noqa: F401

            return "sentence-transformers"
        except Exception:
            pass
        # Real cloud keys only. NOTE: LLM_BASE_URL / LLM_API_KEY are intentionally
        # NOT consulted here — that conflation was the silent-degradation trap.
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "hash"

    def _default_model(self) -> str:
        if self.backend == "openai":
            return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        if self.backend == "gemini":
            return os.getenv("EMBEDDING_MODEL", "text-embedding-004")
        if self.backend == "sentence-transformers":
            # Default chosen for the local-first product: multilingual (strong on
            # Vietnamese), 384-dim, ~470MB → runs on weak machines and CPU.
            # Upgrade for max quality on a real GPU (e.g. RTX 3060 12GB):
            #   EMBEDDING_MODEL=BAAI/bge-m3   (1024-dim, ~2GB, best multilingual).
            return os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
        return "hash-384"

    def _default_dimensions(self) -> int:
        override = os.getenv("EMBEDDING_DIMENSIONS")
        if override:
            try:
                return int(override)
            except ValueError:
                pass
        if self.backend == "openai":
            return 1536
        if self.backend == "gemini":
            return 768
        return 384

    def _init_backend(self):
        # Any init failure degrades LOUDLY to hash rather than crashing the
        # whole process (a memory layer that can't boot is worse than one that
        # boots in a clearly-marked degraded mode).
        try:
            if self.backend == "sentence-transformers":
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_id)
                # Pin dimensions from the real model so the vector index is
                # sized correctly before the first embed() call.
                dims = self._model.get_sentence_embedding_dimension()
                if dims:
                    self._dimensions = int(dims)
                return
            if self.backend == "openai":
                from openai import OpenAI

                # Embeddings config is independent from the chat LLM config.
                base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL")
                api_key = (
                    os.getenv("EMBEDDING_API_KEY")
                    or os.getenv("OPENAI_API_KEY")
                    or "local-dev-key"
                )
                self._client = OpenAI(api_key=api_key, base_url=base_url)
                return
            if self.backend == "gemini":
                from google import genai

                api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("GEMINI_API_KEY")
                self._client = genai.Client(api_key=api_key) if api_key else genai.Client()
                return
        except Exception as exc:
            logger.critical(
                "[Embedder] Failed to initialize embedding backend '%s' (%s) → "
                "degrading to hash mode. Recall will be near-random until fixed.",
                self.backend,
                exc,
            )
            self._activate_hash_fallback()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def effective_backend(self) -> str:
        if self._fallback_active:
            return f"{self.backend}->{self._fallback_backend}"
        return self.backend

    @property
    def degraded(self) -> bool:
        """True when retrieval vectors are NOT real semantic embeddings."""
        return self._fallback_active or self.backend == "hash"

    def _activate_hash_fallback(self) -> List[float]:
        self._fallback_active = True
        # Warn once, loudly. getattr guards keep this safe for objects built via
        # Embedder.__new__ in unit tests (which bypass __init__).
        if not getattr(self, "_degraded_warned", False):
            logger.critical(
                "[Embedder] Real embedding backend '%s' FAILED at query time → "
                "falling back to HASH embeddings. Semantic search is now "
                "semantically meaningless (near-random recall). Do not trust "
                "memory recall until the embedding backend is fixed.",
                getattr(self, "backend", "?"),
            )
            self._degraded_warned = True
        self._dimensions = self._default_dimensions() if self._fallback_backend != "hash" else self._dimensions
        return []

    # getattr guards keep these safe for objects built via Embedder.__new__
    # (unit tests bypass __init__), matching _activate_hash_fallback's style.
    def _cache_get(self, key: str) -> Optional[List[float]]:
        cache = getattr(self, "_cache", None)
        if not cache:
            return None
        cached = cache.get(key)
        if cached is None:
            return None
        cache.move_to_end(key)
        return list(cached)

    def _cache_put(self, key: str, vector: List[float]) -> None:
        cache = getattr(self, "_cache", None)
        if cache is None or not vector:
            return
        cache[key] = list(vector)
        cache.move_to_end(key)
        while len(cache) > getattr(self, "_cache_max", 512):
            cache.popitem(last=False)

    def embed(self, text: str) -> List[float]:
        cached = self._cache_get(text)
        if cached is not None:
            return cached
        vector = self._embed_uncached(text)
        self._cache_put(text, vector)
        return vector

    def _embed_uncached(self, text: str) -> List[float]:
        if not self._fallback_active:
            try:
                if self.backend == "sentence-transformers" and self._model is not None:
                    vector = self._model.encode(text, normalize_embeddings=True)
                    values = [float(value) for value in vector.tolist()]
                    self._dimensions = len(values)
                    return values
                if self.backend == "openai" and self._client is not None:
                    response = self._client.embeddings.create(model=self.model_id, input=text)
                    values = [float(value) for value in response.data[0].embedding]
                    self._dimensions = len(values)
                    return values
                if self.backend == "gemini" and self._client is not None:
                    response = self._client.models.embed_content(model=self.model_id, contents=text)
                    values = getattr(response, "embeddings", None) or []
                    if values:
                        vector = [float(value) for value in values[0].values]
                        self._dimensions = len(vector)
                        return vector
            except Exception:
                self._activate_hash_fallback()
        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> List[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector: List[float] = []
        seed = digest
        while len(vector) < self.dimensions:
            seed = hashlib.sha256(seed + text.encode("utf-8")).digest()
            for index in range(0, len(seed), 2):
                chunk = seed[index:index + 2]
                if len(chunk) < 2:
                    continue
                value = int.from_bytes(chunk, "big") / 65535.0
                vector.append((value * 2.0) - 1.0)
                if len(vector) >= self.dimensions:
                    break
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
