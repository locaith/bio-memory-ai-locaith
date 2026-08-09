"""What does a durable write on this machine actually cost, at each percentile?

The `observe_p95_ms = 1.0` SLO has been red on every sample of every canary run,
including the first sample of a run against a nearly empty database. A threshold
that cannot be green even at t=0 cannot separate a good run from a bad one, and
eight runs have now carried a permanently red column.

The spec that set it reasons that the baseline is "a few tenths of a
millisecond". That is true of the *median* and false of the *p95*, and the SLO
is written against the p95 — so this measures both, on four layers, and lets the
numbers decide rather than the intuition:

    1. bare SQLite, synchronous=FULL      the hardware floor; no project code
    2. bare SQLite, synchronous=NORMAL    what relaxing durability would buy
    3. observe() alone, legacy mode       the production write path
    4. observe() + remember(), shadow     what the canary actually measures

Run it twice — once on an idle machine, once while the canary runs — because a
floor measured under a load the SLO will always see is the more honest floor.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("BIO_AGENT_TENANT_ID", "bench")
os.environ.setdefault("BIO_AGENT_WORKSPACE_ID", "observe-floor")

from bio_agent_os.cognitive.facade import MemoryOS        # noqa: E402
from bio_agent_os.cognitive.models import MemoryType      # noqa: E402

BODY = "x" * 380


def _stats(xs: list[float]) -> dict[str, float]:
    s = sorted(xs)
    p = lambda q: round(s[min(len(s) - 1, int(len(s) * q))], 3)
    return {"p50": p(0.50), "p95": p(0.95), "p99": p(0.99),
            "max": round(s[-1], 3), "min": round(s[0], 3), "n": len(s)}


def bare_sqlite(n: int, synchronous: str) -> dict[str, float]:
    db = Path(tempfile.mkdtemp()) / "floor.db"
    c = sqlite3.connect(str(db))
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(f"PRAGMA synchronous={synchronous}")
    c.execute("CREATE TABLE t(x TEXT)")
    c.commit()
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        c.execute("INSERT INTO t VALUES(?)", (BODY,))
        c.commit()
        lat.append((time.perf_counter() - t0) * 1000)
    c.close()
    return _stats(lat)


def through_observe(n: int, mode: str, with_remember: bool) -> dict[str, float]:
    db = Path(tempfile.mkdtemp()) / "obs.db"
    os_ = MemoryOS(db, projection_mode=mode)
    for i in range(200):                       # warm, not measured
        os_.observe(tenant_id="bench", actor="a", source="u",
                    content=f"warm {i}", workspace_id="observe-floor")
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        ev = os_.observe(tenant_id="bench", actor="a", source="u",
                         content=f"row {i} {BODY}", workspace_id="observe-floor")
        if with_remember:
            os_.remember(event=ev, memory_type=MemoryType.EPISODIC,
                         content=f"row {i} {BODY}")
        lat.append((time.perf_counter() - t0) * 1000)
    os_.close()
    return _stats(lat)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="measure_observe_floor")
    ap.add_argument("--samples", type=int, default=2_000)
    ap.add_argument("--label", default="unlabelled",
                    help="e.g. 'idle' or 'canary running'")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    n = args.samples

    layers = [
        ("1. bare SQLite, synchronous=FULL", lambda: bare_sqlite(n, "FULL")),
        ("2. bare SQLite, synchronous=NORMAL", lambda: bare_sqlite(n, "NORMAL")),
        ("3. observe() only, legacy", lambda: through_observe(n, "legacy", False)),
        ("4. observe()+remember(), shadow", lambda: through_observe(n, "shadow", True)),
    ]

    print(f"  {args.label}  |  {n:,} mau moi lop\n")
    print(f"  {'lop':<36} {'p50':>8} {'p95':>9} {'p99':>9} {'max':>10}")
    results = {}
    for name, fn in layers:
        st = fn()
        results[name] = st
        print(f"  {name:<36} {st['p50']:>8.2f} {st['p95']:>9.2f} "
              f"{st['p99']:>9.2f} {st['max']:>10.1f}", flush=True)

    floor = results["1. bare SQLite, synchronous=FULL"]
    top = results["4. observe()+remember(), shadow"]
    print(f"\n  SLO hien tai: observe p95 <= 1.00 ms")
    print(f"  san phan cung p95 (khong co ma nao cua du an): {floor['p95']:.2f} ms")
    print(f"  -> nguong dang thap hon san {floor['p95']:.1f} lan")
    print(f"  chi phi RIENG cua Bio-Agent OS o p95: "
          f"{top['p95'] - floor['p95']:+.2f} ms tren san")

    payload = {"label": args.label, "samples": n,
               "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "layers": results,
               "current_slo_p95_ms": 1.0,
               "hardware_floor_p95_ms": floor["p95"],
               "project_cost_over_floor_p95_ms": round(top["p95"] - floor["p95"], 3)}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"\n  {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
