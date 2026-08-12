"""What has this key actually spent today?

The budget is five dollars of somebody's money, so "roughly" is not good enough
and neither is a guess derived from token counts I compute myself. OpenAI's
`/v1/usage` endpoint reports the tokens it actually billed, per model, per
snapshot — that is the number to work from.

    python scripts/openai_spend.py                 # today
    python scripts/openai_spend.py --mark before   # save a baseline
    python scripts/openai_spend.py --since before  # spend since that baseline

Prices are a table in this file rather than something fetched, because OpenAI
does not serve them. They are stated so they can be checked and corrected, and
every figure printed says which table produced it.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
STATE = _REPO / ".staging" / "openai_spend_marks.json"

#: USD per 1M tokens. Checked 2026-08-12 against OpenAI's public pricing page.
#: If a figure here is wrong, every cost below is wrong by the same factor —
#: which is why the token counts are printed too.
PRICES = {
    "gpt-4o-mini":            {"in": 0.15,  "out": 0.60},
    "gpt-4.1-mini":           {"in": 0.40,  "out": 1.60},
    "text-embedding-3-small": {"in": 0.02,  "out": 0.0},
    "text-embedding-3-large": {"in": 0.13,  "out": 0.0},
}


def _price_for(model: str) -> dict[str, float]:
    for key, value in PRICES.items():
        if model.startswith(key):
            return value
    return {"in": 0.0, "out": 0.0, "unknown": True}   # counted, not costed


def fetch(date: str, api_key: str) -> dict:
    req = urllib.request.Request(
        f"https://api.openai.com/v1/usage?date={date}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def totals(payload: dict) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in payload.get("data", []):
        model = row.get("snapshot_id") or row.get("model") or "unknown"
        bucket = out.setdefault(model, {"in": 0, "out": 0, "requests": 0})
        bucket["in"] += int(row.get("n_context_tokens_total", 0) or 0)
        bucket["out"] += int(row.get("n_generated_tokens_total", 0) or 0)
        bucket["requests"] += int(row.get("n_requests", 0) or 0)
    return out


def cost(counts: dict[str, dict[str, int]]) -> tuple[float, list[str]]:
    total = 0.0
    unknown: list[str] = []
    for model, c in counts.items():
        p = _price_for(model)
        if p.get("unknown"):
            unknown.append(model)
        total += c["in"] / 1e6 * p["in"] + c["out"] / 1e6 * p["out"]
    return total, unknown


def _load_marks() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(prog="openai_spend")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--mark", default=None, help="save current totals under this name")
    ap.add_argument("--since", default=None, help="report the delta since a saved mark")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")

    now = totals(fetch(args.date, api_key))

    if args.mark:
        marks = _load_marks()
        marks[args.mark] = {"date": args.date, "at": time.strftime("%H:%M:%S"), "totals": now}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(marks, indent=2), encoding="utf-8")
        spent, _ = cost(now)
        print(f"  moc '{args.mark}' da luu — tong hom nay den gio: ${spent:.4f}")
        return 0

    baseline: dict[str, dict[str, int]] = {}
    label = "hom nay"
    if args.since:
        marks = _load_marks()
        if args.since not in marks:
            raise SystemExit(f"khong co moc ten '{args.since}'")
        baseline = marks[args.since]["totals"]
        label = f"tu moc '{args.since}' ({marks[args.since]['at']})"

    delta = {}
    for model, c in now.items():
        b = baseline.get(model, {"in": 0, "out": 0, "requests": 0})
        d = {k: c[k] - b.get(k, 0) for k in ("in", "out", "requests")}
        if any(d.values()):
            delta[model] = d

    spent, unknown = cost(delta)
    print(f"  CHI TIEU {label}")
    if not delta:
        print("    (chua co gi)")
    for model, c in sorted(delta.items()):
        p = _price_for(model)
        line_cost = c["in"] / 1e6 * p["in"] + c["out"] / 1e6 * p["out"]
        flag = "  <- KHONG CO GIA, chua tinh vao tong" if p.get("unknown") else ""
        print(f"    {model:<34}{c['in']:>10,} in{c['out']:>9,} out"
              f"{c['requests']:>7,} req   ${line_cost:>7.4f}{flag}")
    print(f"    {'TONG':<34}{'':>10}{'':>9}{'':>7}      ${spent:>7.4f}")
    if unknown:
        print(f"    chua co gia cho: {', '.join(unknown)} — tong tren BI THIEU")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
