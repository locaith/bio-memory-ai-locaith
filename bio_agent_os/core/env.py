"""Load `.env` once, from the places that read `os.getenv` to decide things.

This exists because the same defect landed three times in one day, each time
looking like a different bug:

* `ingest_learning_inbox --embed` picked the local embedder instead of the
  configured one, because `Embedder()` chooses its backend from the *process*
  environment and nothing had loaded `.env`. It wrote 60 vectors into a
  384-dimension space beside 353 in a 3072-dimension one and split the store in
  two — silently, since `cosine` scores mismatched dimensions 0.0 and retrieval
  reads 0.0 as "unrelated".
* `behaviour_benchmark` ran against `gemini-3-flash-preview` with no Gemini key
  and crashed, after `LLM_BACKEND=openai` had been written to `.env`.
* the same for anything else calling `LLMEngine.from_env()` from a script.

The shape is always: configuration exists on disk, the code reads the
environment, and nobody joined the two. Adding `load_dotenv` to each new script
fixes the script that noticed; loading it where the decision is *made* fixes
the class.

Idempotent and never overrides what is already set, so an explicit
`FOO=bar python ...` still wins over the file — the caller who said something
out loud should not be overruled by a file they may not have read.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root: this file is `<root>/bio_agent_os/core/env.py`.
_ROOT = Path(__file__).resolve().parents[2]

_loaded = False


def load_project_env(*, force: bool = False) -> bool:
    """Load `<repo>/.env` into the process environment. Returns whether it ran.

    Called from `LLMEngine.from_env()` and `Embedder.__init__`, the two places
    that read the environment to choose a backend. Cheap after the first call.
    """
    global _loaded
    if _loaded and not force:
        return False

    path = _ROOT / ".env"
    if not path.exists():
        _loaded = True
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:          # pragma: no cover - dotenv is a hard dep today
        _loaded = True
        return False

    # `override=False`: an explicitly exported variable beats the file.
    load_dotenv(path, override=False)
    _loaded = True
    return True


def env_path() -> Path:
    return _ROOT / ".env"


__all__ = ["env_path", "load_project_env"]
