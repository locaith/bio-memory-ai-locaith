"""What `Hippocampus.label()` promises when the model misbehaves.

Every test here pins a defect that was measured, not imagined — see
`reports/hippocampus_characterisation.md` and the raw numbers beside it. They
exist because this component is about to be put on a write path, and all three
faults are the kind that produce confident, well-formed, wrong data rather than
an error anyone would notice.

The stubs replace `generate()` — the raw model text — and not
`generate_structured()`. That distinction is the point: JSON extraction, the
repair retry and pydantic validation all live above `generate()`, and the first
version of the characterisation harness stubbed above them, which reported
defects the engine actually catches. A test that stubs too high tests itself.
"""

from __future__ import annotations

import asyncio
import tempfile

import pytest

from bio_agent_os.background_jobs.hippocampus import Hippocampus, MemoryLabel
from bio_agent_os.core.llm_engine import LLMEngine


class _ScratchPersona:
    name = "label-contract"

    def __init__(self) -> None:
        self.storage_dir = tempfile.mkdtemp(prefix="hippo-label-contract-")


def _engine_saying(raw: str) -> LLMEngine:
    """A real engine whose text output is canned.

    `ollama` explicitly rather than `from_env()`: the default backend is gemini,
    whose client may not construct in a test environment, and every case would
    then fail for that reason instead of the one under test. Ollama is also the
    backend that routes through `_structured_fallback`, where the validation
    being tested lives.
    """
    engine = LLMEngine(backend="ollama", model_id="stub")

    async def generate(prompt: str, temperature: float = 0.1) -> str:
        return raw

    engine.generate = generate  # type: ignore[method-assign]
    return engine


def _hippo(engine) -> Hippocampus:
    return Hippocampus(engine=engine, l1=None, persona=_ScratchPersona())


def _label(engine) -> dict:
    return asyncio.run(_hippo(engine).label("nội dung thử", source="test"))


# --------------------------------------------------------------------------
# 1. importance_score is a gate, so it must be bounded
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [99, 11, 0, -3])
def test_importance_outside_one_to_ten_never_reaches_the_caller(value):
    """`importance_score >= 8` promotes a rule into the self-model and raises
    reasoning effort. An unbounded 99 clears every gate that exists, for ever,
    and a -3 fails all of them. The range used to be stated only in the field
    description — prose sent to the model, not a constraint."""
    result = _label(_engine_saying(
        f'{{"topic":"x","importance_score":{value},'
        f'"is_junk_or_transient":false,"user_state":"ok"}}'
    ))
    assert result["importance_score"] != value
    assert 1 <= result["importance_score"] <= 10


def test_a_valid_score_is_passed_through_untouched():
    """The bound must reject the out-of-range answer, not flatten every answer.
    Without this, a fix that returned the fallback unconditionally would pass
    every other test in this file."""
    result = _label(_engine_saying(
        '{"topic":"hợp đồng","importance_score":9,'
        '"is_junk_or_transient":false,"user_state":"focused"}'
    ))
    assert result["importance_score"] == 9
    assert result["topic"] == "hợp đồng"
    assert "label_failed" not in result


def test_the_schema_itself_carries_the_bound():
    """Belt and braces: `relabel_pending()` validates through MemoryLabelBatch,
    which nests this same model, so the constraint has to live on the field
    rather than in a check inside `label()`."""
    with pytest.raises(Exception):
        MemoryLabel(topic="x", importance_score=99,
                    is_junk_or_transient=False, user_state="ok")


# --------------------------------------------------------------------------
# 2. a failed label must be distinguishable from a real one
# --------------------------------------------------------------------------

def test_the_failure_fallback_is_marked():
    """The fallback returns a well-formed dict with importance 5. Unmarked, a
    stretch with the model down becomes a stretch of memories quietly scored 5
    that nothing can later identify to relabel — while `_cheap_label`, the
    deliberate no-model path, sets `label_pending` for exactly that reason."""
    result = _label(_engine_saying("I'm afraid I can't do that."))
    assert result["importance_score"] == 5
    assert result["label_failed"], "a fabricated label must say that it is one"
    assert result["label_pending"] is True, (
        "must enter the backlog relabel_pending() already drains"
    )


def test_the_marker_uses_the_same_key_consolidation_looks_for():
    """`relabel_pending()` selects on `label_pending`. A failed label that used
    a different key would be marked and still never retried."""
    import inspect

    source = inspect.getsource(Hippocampus.relabel_pending)
    assert "label_pending" in source
    assert _label(_engine_saying("nonsense"))["label_pending"] is True


# --------------------------------------------------------------------------
# 3. a hanging model must not hang the caller
# --------------------------------------------------------------------------

def test_label_gives_up_on_a_model_that_never_answers():
    """Measured: 30 s of stub delay produced 30 s of waiting, because there was
    no timeout anywhere in the call. On a write path that is a write with no
    upper bound — close to the shape of the defect that ended canary Run 8, a
    scheduled checkpoint with no time budget holding every writer for 23 s."""

    class _Hangs:
        backend = "stub"
        model_id = "hangs"

        async def generate_structured(self, prompt, schema, temperature=0.1, effort=None):
            await asyncio.sleep(30)
            raise AssertionError("should have been abandoned long before this")

    hippo = _hippo(_Hangs())
    hippo.LABEL_TIMEOUT_SECONDS = 0.2

    async def run():
        started = asyncio.get_running_loop().time()
        result = await hippo.label("nội dung thử", source="test")
        return result, asyncio.get_running_loop().time() - started

    result, elapsed = asyncio.run(run())
    assert elapsed < 5, f"waited {elapsed:.1f}s; the timeout did not fire"
    assert result["label_failed"], "a timeout is a failure and must be marked"


def test_the_timeout_is_configurable_from_the_environment(monkeypatch):
    """60 s is provisional — the real per-event p95 has not been measured yet.
    Whoever tightens it after the latency half of Phase 1 lands should not have
    to edit code to do it."""
    import importlib

    monkeypatch.setenv("BIO_HIPPOCAMPUS_LABEL_TIMEOUT_S", "7.5")
    module = importlib.reload(
        importlib.import_module("bio_agent_os.background_jobs.hippocampus")
    )
    try:
        assert module.Hippocampus.LABEL_TIMEOUT_SECONDS == 7.5
    finally:
        monkeypatch.delenv("BIO_HIPPOCAMPUS_LABEL_TIMEOUT_S", raising=False)
        importlib.reload(module)
