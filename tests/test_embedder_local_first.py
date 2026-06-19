"""
Local-first / no-silent-hallucination guarantees for the Embedder.

These pin the regression fix for the silent hash-fallback trap: the chat LLM
endpoint must never be mistaken for an embeddings endpoint, embeddings config is
independent, and degraded mode is observable (not silent).
"""

import math

from bio_agent_os.core.embedder import Embedder

_EMBED_ENV = [
    "EMBEDDING_BACKEND",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GEMINI_API_KEY",
    "LLM_BASE_URL",
    "LLM_API_KEY",
]


def _clear_embed_env(monkeypatch):
    for key in _EMBED_ENV:
        monkeypatch.delenv(key, raising=False)


def test_llm_base_url_never_selects_cloud_embeddings(monkeypatch):
    """The trap: a local chat LLM at LLM_BASE_URL must not route embeddings to
    the OpenAI backend (whose /embeddings the local server usually lacks)."""
    _clear_embed_env(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "local-dev-key")

    detected = Embedder.__new__(Embedder)._detect_backend()

    # Allowed: on-device model if installed, else degraded hash. Never the
    # cloud "openai" backend inferred from the *chat* endpoint.
    assert detected in {"sentence-transformers", "hash"}
    assert detected != "openai"


def test_embedding_base_url_selects_openai(monkeypatch):
    """A *dedicated* embeddings endpoint is honored (intentional config)."""
    _clear_embed_env(monkeypatch)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1234/v1")

    assert Embedder.__new__(Embedder)._detect_backend() == "openai"


def test_explicit_backend_override_wins(monkeypatch):
    _clear_embed_env(monkeypatch)
    monkeypatch.setenv("EMBEDDING_BACKEND", "hash")

    assert Embedder.__new__(Embedder)._detect_backend() == "hash"


def test_hash_mode_is_degraded_and_observable(monkeypatch):
    """Degraded mode must be loud/observable, not silent."""
    _clear_embed_env(monkeypatch)
    monkeypatch.setenv("EMBEDDING_BACKEND", "hash")

    embedder = Embedder()

    assert embedder.backend == "hash"
    assert embedder.degraded is True


def test_hash_embed_is_deterministic_and_normalized(monkeypatch):
    """Reconsolidation merge relies on identical content → identical vector."""
    _clear_embed_env(monkeypatch)
    monkeypatch.setenv("EMBEDDING_BACKEND", "hash")

    embedder = Embedder()
    a = embedder.embed("xin chào thế giới")
    b = embedder.embed("xin chào thế giới")
    c = embedder.embed("một câu hoàn toàn khác")

    assert a == b
    assert a != c
    assert len(a) == 384
    norm = math.sqrt(sum(value * value for value in a))
    assert abs(norm - 1.0) < 1e-6


def test_runtime_provider_failure_falls_back_loudly(monkeypatch):
    """If a real backend dies at query time, we degrade (observably), not crash."""
    _clear_embed_env(monkeypatch)
    embedder = Embedder.__new__(Embedder)
    embedder.backend = "gemini"
    embedder.model_id = "broken-model"
    embedder._model = None
    embedder._dimensions = 384
    embedder._fallback_backend = "hash"
    embedder._fallback_active = False

    class FailingClient:
        class models:  # noqa: N801 - mimic genai client shape
            @staticmethod
            def embed_content(model, contents):
                raise RuntimeError("embedding unavailable")

    embedder._client = FailingClient()

    vector = Embedder.embed(embedder, "fallback me")

    assert len(vector) == 384
    assert embedder.degraded is True
    assert embedder.effective_backend == "gemini->hash"
