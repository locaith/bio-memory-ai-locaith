"""
Embedding backends for production-ready retrieval.
"""

import hashlib
import math
import os
from typing import List, Optional


class Embedder:
    def __init__(self):
        self.backend = self._detect_backend()
        self.model_id = self._default_model()
        self._client = None
        self._model = None
        self._dimensions = self._default_dimensions()
        self._init_backend()

    def _detect_backend(self) -> str:
        backend = os.getenv("EMBEDDING_BACKEND", "").strip().lower()
        if backend:
            return backend
        if os.getenv("OPENAI_API_KEY") or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"):
            return "openai"
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        try:
            import sentence_transformers  # noqa: F401

            return "sentence-transformers"
        except Exception:
            return "hash"

    def _default_model(self) -> str:
        if self.backend == "openai":
            return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        if self.backend == "gemini":
            return os.getenv("EMBEDDING_MODEL", "text-embedding-004")
        if self.backend == "sentence-transformers":
            return os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
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
        if self.backend == "sentence-transformers":
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_id)
            return
        if self.backend == "openai":
            from openai import OpenAI

            base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL")
            api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or "local-dev-key"
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            return
        if self.backend == "gemini":
            from google import genai

            api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("GEMINI_API_KEY")
            self._client = genai.Client(api_key=api_key) if api_key else genai.Client()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> List[float]:
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
