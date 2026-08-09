"""Phase 1 of the join plan: find out what the hippocampus actually does.

Nobody has ever measured it. There is no benchmark, no cost figure, no latency
figure, and no evidence about the quality of the labels it assigns — and the
plan is to wire it into a write path that took nine canary runs to make clean.
Joining an unmeasured component to that is backwards, so this measures first.

Four questions, and the answers decide the architecture rather than the other
way round:

    1. stability   same input twice -> same label? at temperature 0.1 it
                   should be close, and if it is not, every downstream
                   number inherits that spread
    2. spread      how is importance_score distributed? if everything lands
                   on 7-8 the scale carries no information
    3. cost        p50 / p95 / max per event. Above a few hundred ms the
                   hippocampus CANNOT sit on the write path — that single
                   number decides the whole of Phase 2
    4. failure     model dead, model slow, model returns garbage. Measured
                   with stub engines, so these need no GPU at all

`--offline` runs only (4). It is the honest thing to run first: it costs
nothing, needs no GPU, and it is where the sharper findings tend to be.

The machine has not had its power supply replaced, so the online path runs in
short batches with a rest between them and stops on the first sign of trouble.

    python scripts/characterise_hippocampus.py --offline
    python scripts/characterise_hippocampus.py --repeats 3 --batch 6 --rest 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pydantic import BaseModel  # noqa: E402

from bio_agent_os.background_jobs.hippocampus import Hippocampus  # noqa: E402

# Under tests/, not data/: `data/` is gitignored, and a fixture that is not in
# the repo cannot make a measurement repeatable six months from now.
CORPUS = _REPO / "tests" / "fixtures" / "hippocampus_corpus.json"


# --------------------------------------------------------------------------
# Stubs. The failure scenarios must be deterministic and instant, so they use
# fake engines rather than a real backend that has been broken on purpose.
# --------------------------------------------------------------------------

class _DeadEngine:
    """The model is not there at all."""

    backend = "stub"
    model_id = "dead"

    async def generate_structured(self, prompt: str, schema: type[BaseModel],
                                  temperature: float = 0.1, effort: str | None = None) -> dict:
        raise ConnectionError("connection refused")


class _SlowEngine:
    """The model answers, thirty seconds later. Is there a timeout?"""

    backend = "stub"
    model_id = "slow"

    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def generate_structured(self, prompt: str, schema: type[BaseModel],
                                  temperature: float = 0.1, effort: str | None = None) -> dict:
        await asyncio.sleep(self.delay)
        return {"topic": "late", "importance_score": 5,
                "is_junk_or_transient": False, "user_state": "unknown"}


def _engine_returning(raw: str) -> Any:
    """A real LLMEngine whose *text* generation is canned.

    The first version of this stubbed `generate_structured` itself, which was
    wrong: `_structured_fallback` runs `schema.model_validate()` on whatever the
    model said, so stubbing above it measured a path that cannot happen and
    would have reported defects the engine actually catches. Stubbing `generate`
    — the raw text — leaves JSON extraction, the repair retry and pydantic
    validation all in place, which is the thing under test.

    Not hypothetical input: a local model asked for an integer 1-10 will return
    "high", 99, or null often enough to matter.
    """
    from bio_agent_os.core.llm_engine import LLMEngine

    # `ollama` explicitly, not `from_env()`. from_env() defaults to gemini, whose
    # client fails to construct here — and then every scenario "failed" for that
    # reason instead of the one being tested, which is the second time in this
    # script that a stub measured itself. Ollama is also the backend that
    # actually matters: it is the one that routes through _structured_fallback,
    # where JSON extraction, the repair retry and validation live.
    engine = LLMEngine(backend="ollama", model_id="stub")

    async def generate(prompt: str, temperature: float = 0.1) -> str:
        return raw

    engine.generate = generate  # type: ignore[method-assign]
    return engine


class _ScratchPersona:
    """Minimal persona. `label()` never touches it, but the constructor builds a
    ContradictionResolver that opens a store under `persona.storage_dir`, so it
    needs a real directory — a throwaway one, outside the repo."""

    name = "characterisation"

    def __init__(self) -> None:
        self.storage_dir = tempfile.mkdtemp(prefix="hippo-characterise-")


def _hippo(engine: Any) -> Hippocampus:
    """The label path needs the engine only; l1 is unused by it."""
    return Hippocampus(engine=engine, l1=None, persona=_ScratchPersona())


def _fell_back(hippo: Hippocampus) -> bool:
    """Did `label()` return the exception fallback rather than a real label?

    The return value cannot answer this — the fallback is a well-formed dict
    with importance 5 and carries no marker. The log line is the only signal
    available from outside, which is itself worth reporting.
    """
    return any("Label failed" in line for line in hippo.logs)


# --------------------------------------------------------------------------
# 4. Failure behaviour  (no GPU)
# --------------------------------------------------------------------------

async def failure_scenarios() -> list[dict[str, Any]]:
    probe = "Hợp đồng ký ngày 12/03/2026, giá trị 84.000.000 đồng."
    out: list[dict[str, Any]] = []

    async def run(name: str, engine: Any, *, timeout: float | None = None) -> dict[str, Any]:
        h = _hippo(engine)
        h.clear_logs()
        t0 = time.perf_counter()
        timed_out = False
        try:
            if timeout is None:
                result = await h.label(probe, source="probe")
            else:
                result = await asyncio.wait_for(h.label(probe, source="probe"), timeout)
        except asyncio.TimeoutError:
            timed_out, result = True, None
        elapsed = (time.perf_counter() - t0) * 1000
        row = {
            "scenario": name,
            "raised": False,
            "timed_out_at_caller": timed_out,
            "elapsed_ms": round(elapsed, 1),
            "logged_failure": _fell_back(h),
            "returned": result,
        }
        out.append(row)
        return row

    await run("model dead (connection refused)", _DeadEngine())
    await run("model slow (30 s), caller waits 2 s", _SlowEngine(30.0), timeout=2.0)
    await run("model says importance 99", _engine_returning(
        '{"topic":"x","importance_score":99,'
        '"is_junk_or_transient":false,"user_state":"ok"}'))
    await run("model says importance 'high'", _engine_returning(
        '{"topic":"x","importance_score":"high",'
        '"is_junk_or_transient":false,"user_state":"ok"}'))
    await run("model says importance -3", _engine_returning(
        '{"topic":"x","importance_score":-3,'
        '"is_junk_or_transient":false,"user_state":"ok"}'))
    await run("model omits a required field", _engine_returning('{"topic":"x"}'))
    await run("model answers prose, no JSON", _engine_returning(
        "Sure! Here is what I think about this memory."))

    # The zero-LLM path the write side is supposed to use.
    h = _hippo(_DeadEngine())
    out.append({
        "scenario": "_cheap_label (no model at all)",
        "raised": False, "timed_out_at_caller": False, "elapsed_ms": 0.0,
        "logged_failure": False,
        "returned": h._cheap_label(probe),
    })
    return out


# --------------------------------------------------------------------------
# 1-3. Behaviour, spread and cost  (needs the GPU)
# --------------------------------------------------------------------------

async def measure_corpus(items: list[dict[str, Any]], repeats: int,
                         batch: int, rest: float) -> dict[str, Any]:
    from bio_agent_os.core.llm_engine import LLMEngine

    engine = LLMEngine.from_env()
    print(f"  engine: backend={engine.backend} model={engine.model_id}", flush=True)

    hippo = _hippo(engine)
    observations: list[dict[str, Any]] = []
    latencies: list[float] = []
    since_rest = 0

    for rep in range(repeats):
        for item in items:
            hippo.clear_logs()
            t0 = time.perf_counter()
            label = await hippo.label(item["content"], source=item["source"])
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
            observations.append({
                "id": item["id"], "repeat": rep, "expect": item["expect"],
                "elapsed_ms": round(elapsed, 1),
                "fell_back": _fell_back(hippo),
                "label": label,
            })
            since_rest += 1
            if batch and since_rest >= batch:
                since_rest = 0
                print(f"    {len(observations)}/{len(items) * repeats}"
                      f"  resting {rest:.0f}s", flush=True)
                await asyncio.sleep(rest)

    # Stability: for each item, how many distinct values did the repeats give?
    by_id: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        by_id.setdefault(obs["id"], []).append(obs)

    stability = []
    for item_id, runs in by_id.items():
        if len(runs) < 2:
            continue
        scores = [r["label"].get("importance_score") for r in runs]
        junk = [bool(r["label"].get("is_junk_or_transient")) for r in runs]
        numeric = [s for s in scores if isinstance(s, (int, float))]
        stability.append({
            "id": item_id,
            "importance_values": scores,
            "importance_spread": (max(numeric) - min(numeric)) if numeric else None,
            "junk_agrees": len(set(junk)) == 1,
            "topics": sorted({str(r["label"].get("topic")) for r in runs}),
        })

    histogram: dict[str, int] = {}
    for obs in observations:
        histogram[str(obs["label"].get("importance_score"))] = \
            histogram.get(str(obs["label"].get("importance_score")), 0) + 1

    ordered = sorted(latencies)
    def pct(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 1)

    return {
        "engine": {"backend": engine.backend, "model": engine.model_id},
        "observations": observations,
        "latency_ms": {
            "p50": pct(0.50), "p95": pct(0.95), "max": round(ordered[-1], 1),
            "mean": round(statistics.fmean(ordered), 1), "n": len(ordered),
        },
        "importance_histogram": dict(sorted(histogram.items())),
        "stability": stability,
        "fallback_rate": round(
            sum(1 for o in observations if o["fell_back"]) / max(1, len(observations)), 4
        ),
    }


def render_offline(rows: list[dict[str, Any]]) -> str:
    lines = ["", "  FAILURE BEHAVIOUR  (stub engines, no GPU)", "  " + "-" * 74,
             f"  {'scenario':<40} {'ms':>8} {'logged':>8}  returned"]
    for r in rows:
        got = r["returned"]
        shown = "TIMEOUT AT CALLER" if r["timed_out_at_caller"] else (
            f"importance={got.get('importance_score')!r} "
            f"junk={got.get('is_junk_or_transient')!r}"
            + (" label_pending=True" if got.get("label_pending") else "")
            if isinstance(got, dict) else str(got)
        )
        lines.append(f"  {r['scenario']:<40} {r['elapsed_ms']:>8.1f} "
                     f"{'yes' if r['logged_failure'] else 'no':>8}  {shown}")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(prog="characterise_hippocampus")
    ap.add_argument("--offline", action="store_true",
                    help="failure scenarios only; no GPU, no model")
    ap.add_argument("--repeats", type=int, default=2,
                    help="times to label each item; 2 is the minimum that shows drift")
    ap.add_argument("--batch", type=int, default=6,
                    help="labels between rests (PSU has not been replaced)")
    ap.add_argument("--rest", type=float, default=15.0)
    ap.add_argument("--limit", type=int, default=None, help="first N corpus items")
    ap.add_argument("--out", default="reports/hippocampus_characterisation.json")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    items = corpus["items"][: args.limit] if args.limit else corpus["items"]

    payload: dict[str, Any] = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus": {"path": str(CORPUS.relative_to(_REPO)),
                   "version": corpus["_version"], "items": len(items)},
        "env": {k: os.environ.get(k, "unset")
                for k in ("LLM_BACKEND", "MODEL_ID", "EMBEDDING_BACKEND", "EMBEDDING_MODEL")},
    }

    failures = await failure_scenarios()
    payload["failure_scenarios"] = failures
    print(render_offline(failures))

    if not args.offline:
        print(f"\n  CORPUS  {len(items)} items x {args.repeats} repeats", flush=True)
        payload["corpus_run"] = await measure_corpus(
            items, args.repeats, args.batch, args.rest
        )
        cr = payload["corpus_run"]
        print(f"\n  latency  p50={cr['latency_ms']['p50']} "
              f"p95={cr['latency_ms']['p95']} max={cr['latency_ms']['max']} ms")
        print(f"  importance histogram: {cr['importance_histogram']}")
        print(f"  fallback rate: {cr['fallback_rate']:.1%}")
        drifted = [s for s in cr["stability"]
                   if (s["importance_spread"] or 0) > 0 or not s["junk_agrees"]]
        print(f"  unstable items: {len(drifted)}/{len(cr['stability'])}")

    out = _REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
