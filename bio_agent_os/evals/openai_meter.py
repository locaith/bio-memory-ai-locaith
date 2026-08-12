"""Count OpenAI tokens as they are spent, not hours later.

`/v1/usage` is the authoritative number but it lags — it reported $0.0000 for a
run that had demonstrably just made hundreds of calls. That is fine for an
invoice and useless for a budget of five dollars, where the question is "stop
now?" and the answer has to arrive before the money is gone.

Every OpenAI response carries `usage`. This patches the SDK at the two places
that spend money, accumulates what comes back, and writes a running total to
disk after each call. Patching the SDK rather than this project's call sites is
deliberate: **mem0 builds its own client**, and a meter that missed mem0 would
under-report exactly the half of the experiment that is not ours.

    from bio_agent_os.evals.openai_meter import install
    install()                       # before anything constructs a client

    python scripts/openai_meter_report.py        # read the total any time

A hard ceiling can be set, and it raises rather than warns:

    install(budget_usd=4.50)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
LEDGER = _REPO / ".staging" / "openai_meter.json"

#: USD per 1M tokens, checked 2026-08-12. Wrong prices make every total wrong by
#: the same factor, so the raw token counts are always written beside the cost.
PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini":            {"in": 0.15,  "out": 0.60},
    "gpt-4o":                 {"in": 2.50,  "out": 10.00},
    "gpt-4.1-mini":           {"in": 0.40,  "out": 1.60},
    "gpt-4.1-nano":           {"in": 0.10,  "out": 0.40},
    "gpt-4.1":                {"in": 2.00,  "out": 8.00},
    "text-embedding-3-small": {"in": 0.02,  "out": 0.0},
    "text-embedding-3-large": {"in": 0.13,  "out": 0.0},
}

_lock = threading.Lock()
_state: dict[str, Any] = {"models": {}, "total_usd": 0.0, "calls": 0,
                          "budget_usd": None, "unpriced": []}
_installed = False


class BudgetExceeded(RuntimeError):
    """Raised the moment the ceiling is crossed, mid-run, on purpose."""


def _price(model: str) -> dict[str, float] | None:
    best = None
    for key, value in PRICES.items():
        if model.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), value)
    return best[1] if best else None


def _record(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    with _lock:
        bucket = _state["models"].setdefault(
            model, {"in": 0, "out": 0, "calls": 0, "usd": 0.0}
        )
        bucket["in"] += prompt_tokens
        bucket["out"] += completion_tokens
        bucket["calls"] += 1
        _state["calls"] += 1

        p = _price(model)
        if p is None:
            if model not in _state["unpriced"]:
                _state["unpriced"].append(model)
        else:
            cost = prompt_tokens / 1e6 * p["in"] + completion_tokens / 1e6 * p["out"]
            bucket["usd"] += cost
            _state["total_usd"] += cost

        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(_state, indent=2), encoding="utf-8")
        budget = _state.get("budget_usd")
        over = budget is not None and _state["total_usd"] >= budget

    if over:
        raise BudgetExceeded(
            f"da tieu ${_state['total_usd']:.4f}, tran ${budget:.2f} — dung lai"
        )


def _extract(result: Any) -> tuple[str, int, int] | None:
    usage = getattr(result, "usage", None)
    if usage is None:
        return None
    model = getattr(result, "model", None) or "unknown"
    return (
        model,
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def install(budget_usd: float | None = None, reset: bool = True) -> None:
    """Patch the SDK. Safe to call twice; only the first call patches."""
    global _installed

    with _lock:
        if reset:
            _state.update({"models": {}, "total_usd": 0.0, "calls": 0, "unpriced": []})
        _state["budget_usd"] = budget_usd

    if _installed:
        return

    from openai.resources.chat import completions as chat_completions
    from openai.resources import embeddings as embeddings_module

    def wrap(cls: type, name: str, is_async: bool) -> None:
        original = getattr(cls, name)

        if is_async:
            async def patched(self, *args, **kwargs):          # type: ignore[no-untyped-def]
                result = await original(self, *args, **kwargs)
                got = _extract(result)
                if got:
                    _record(*got)
                return result
        else:
            def patched(self, *args, **kwargs):                # type: ignore[no-untyped-def]
                result = original(self, *args, **kwargs)
                got = _extract(result)
                if got:
                    _record(*got)
                return result

        setattr(cls, name, patched)

    wrap(chat_completions.Completions, "create", False)
    wrap(chat_completions.AsyncCompletions, "create", True)
    wrap(embeddings_module.Embeddings, "create", False)
    wrap(embeddings_module.AsyncEmbeddings, "create", True)
    _installed = True


def snapshot() -> dict[str, Any]:
    with _lock:
        return json.loads(json.dumps(_state))


def render() -> str:
    s = snapshot()
    lines = [f"  {'model':<26}{'in':>12}{'out':>10}{'calls':>8}{'USD':>10}"]
    for model, b in sorted(s["models"].items()):
        lines.append(f"  {model:<26}{b['in']:>12,}{b['out']:>10,}"
                     f"{b['calls']:>8,}{b['usd']:>10.4f}")
    lines.append(f"  {'TONG':<26}{'':>12}{'':>10}{s['calls']:>8,}"
                 f"{s['total_usd']:>10.4f}")
    if s["unpriced"]:
        lines.append(f"  chua co gia: {', '.join(s['unpriced'])} — tong BI THIEU")
    if s.get("budget_usd"):
        left = s["budget_usd"] - s["total_usd"]
        lines.append(f"  con lai trong tran ${s['budget_usd']:.2f}: ${left:.4f}")
    return "\n".join(lines)


__all__ = ["BudgetExceeded", "LEDGER", "PRICES", "install", "render", "snapshot"]
