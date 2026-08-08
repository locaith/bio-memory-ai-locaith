"""A short, hostile rehearsal before spending another twenty-four hours.

Run 8 cost a night to learn something a thirty-minute test could have shown, so
this exists to fail fast. It is deliberately harsher than the canary: the same
write path and the same SQLite configuration, but the doctor runs every thirty
seconds instead of every five minutes, because the defect being hunted is
proportional to how often a scan holds the log.

**This is not durability evidence and must never be quoted as any.** It answers
one question — has the reader-hold defect actually gone — and answers it before
the expensive run rather than six hours into it.

    python scripts/wal_torture_gate.py --minutes 30
    python scripts/wal_torture_gate.py --minutes 5 --doctor-every 10   # smoke

Thresholds are the ones Run 9 will be judged by. They are not lowered here to
make the gate easier: a gate that passes what the real run fails is worse than
no gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("BIO_AGENT_PROJECTION_MODE", "shadow")

from bio_agent_os.cognitive.doctor_incremental import IncrementalDoctor   # noqa: E402
from bio_agent_os.cognitive.facade import MemoryOS                        # noqa: E402
from bio_agent_os.cognitive.models import MemoryType                      # noqa: E402
from bio_agent_os.cognitive.shadow_runner import shadow_worker            # noqa: E402
from bio_agent_os.cognitive.wal import (                                  # noqa: E402
    READER_HOLD_FAIL_MS,
    READER_HOLD_WARN_MS,
    manager_for,
)

#: Same limits the canary runs under. Section 14: not changed to suit the gate.
SOFT_LIMIT_BYTES = 64 * 1048576
HARD_LIMIT_BYTES = 128 * 1048576
BODY = "x" * 380


def _wal_bytes(db: Path) -> int:
    p = Path(str(db) + "-wal")
    return p.stat().st_size if p.exists() else 0


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(len(s) * q))], 2)


def run(minutes: float, doctor_every: float, rate: float, out: Path,
        seed_from: str | None = None) -> int:
    staging = _REPO / ".staging" / "wal-torture"
    staging.mkdir(parents=True, exist_ok=True)
    db = staging / "torture.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()

    # Seeding matters more than duration. Run 8's doctor was slow *because the
    # database was 1.7 GB* — the same scan on a fresh database holds for 24 ms
    # and proves nothing. A gate starting from empty cannot reproduce the
    # failure it exists to clear, however long it runs.
    if seed_from:
        import shutil
        src = Path(seed_from)
        if not src.exists():
            raise SystemExit(f"seed database not found: {src}")
        shutil.copy2(src, db)
        print(f"  khởi đầu từ {src.name} ({src.stat().st_size/1048576:,.0f} MB)", flush=True)

    os_ = MemoryOS(db, projection_mode="shadow")
    manager = manager_for(os_, soft_limit_bytes=SOFT_LIMIT_BYTES,
                          hard_limit_bytes=HARD_LIMIT_BYTES, interval_seconds=30.0)
    worker = shadow_worker(os_, worker_id="torture", lease_seconds=30)

    deadline = time.time() + minutes * 60
    next_doctor = time.time() + doctor_every
    interval = 1.0 / max(1e-9, rate)

    holds: list[float] = []
    wal_series: list[tuple[float, int, str]] = []
    doctor_runs: list[dict[str, Any]] = []
    written = 0
    started = time.time()

    print(f"  {minutes:g} phút, doctor mỗi {doctor_every:g}s, {rate:g} lượt ghi/giây")
    print(f"  ngưỡng giữ nguyên: mềm {SOFT_LIMIT_BYTES//1048576} MB, "
          f"cứng {HARD_LIMIT_BYTES//1048576} MB\n", flush=True)
    print(f"  {'phút':>6} {'ghi':>9} {'walMB':>7} {'trạng thái':>10} "
          f"{'giữ p95':>8} {'giữ max':>8}", flush=True)

    doctor_thread: threading.Thread | None = None
    lock = threading.Lock()

    def _doctor_pass() -> None:
        conn = sqlite3.connect(str(db), timeout=120.0)
        try:
            report = IncrementalDoctor(conn).run_incremental()
        finally:
            conn.close()
        # File size, not `manager.status()`. The manager holds a connection
        # created on the main thread and SQLite refuses to let another thread
        # touch it — the first version called it here and every pass logged
        # "SQLite objects created in a thread can only be used in that same
        # thread", quietly reporting journal_mode as unknown.
        wal_now = _wal_bytes(db)
        state = ("critical" if wal_now >= HARD_LIMIT_BYTES
                 else "pressure" if wal_now >= SOFT_LIMIT_BYTES else "normal")
        with lock:
            for entry in report.reader_holds.values():
                holds.append(entry["max_ms"])
            worst = (report.holds_ranked() or [("none", {"max_ms": 0.0})])[0]
            doctor_runs.append({
                "at_minute": round((time.time() - started) / 60, 2),
                "duration_ms": round(report.duration_s * 1000, 1),
                "max_hold_ms": round(report.max_hold_ms, 1),
                "slowest_check": worst[0],
                "chunks": report.chunks,
                "exit_code": report.exit_code,
            })
            wal_series.append((round((time.time() - started) / 60, 2),
                               wal_now, state))
            print(f"  {(time.time()-started)/60:>6.1f} {written:>9,} "
                  f"{wal_now/1048576:>7.1f} {state:>10} "
                  f"{_pct(holds,0.95):>8.0f} {max(holds, default=0):>8.0f}",
                  flush=True)

    try:
        while time.time() < deadline:
            tick = time.perf_counter()
            event = os_.observe(tenant_id="t1", actor="a", source="torture",
                                content=f"row {written} {BODY}", workspace_id="ws")
            os_.remember(event=event, memory_type=MemoryType.EPISODIC,
                         content=f"row {written} {BODY}")
            written += 1

            if written % 200 == 0:
                worker.run_once()
                # Drive the manager from the thread that owns its connection.
                # The first version created it and never called it, so the only
                # thing checkpointing was SQLite's own autocheckpoint — and the
                # gate then failed its own "reclaim was observed" check for a
                # reason that had nothing to do with the code under test.
                manager.maybe_checkpoint()

            now = time.time()
            if now >= next_doctor and not (doctor_thread and doctor_thread.is_alive()):
                # In its own thread, on its own connection, so writes CONTINUE
                # while it reads. That is the whole point: a doctor that pauses
                # the writer cannot pin a log against anybody, and a gate that
                # runs them in turn proves nothing about a canary that runs them
                # at once. The first version of this gate did exactly that and
                # sailed through at a flat 4 MB.
                doctor_thread = threading.Thread(target=_doctor_pass, daemon=True)
                doctor_thread.start()
                next_doctor = now + doctor_every

            elapsed = time.perf_counter() - tick
            if elapsed < interval:
                time.sleep(interval - elapsed)
    except KeyboardInterrupt:
        print("  dừng bằng tay", flush=True)

    peaks = [w for _, w, _ in wal_series]
    first_third = max(peaks[: max(1, len(peaks) // 3)], default=0)
    last_third = max(peaks[-max(1, len(peaks) // 3):], default=0)
    time_above_hard = sum(1 for _, w, _ in wal_series if w >= HARD_LIMIT_BYTES) * doctor_every

    checks = [
        ("reader hold max under the fail threshold",
         max(holds, default=0) < READER_HOLD_FAIL_MS,
         f"{max(holds, default=0):.0f} ms vs {READER_HOLD_FAIL_MS:.0f} ms"),
        ("reader hold p95 under the warn threshold",
         _pct(holds, 0.95) < READER_HOLD_WARN_MS,
         f"{_pct(holds,0.95):.0f} ms vs {READER_HOLD_WARN_MS:.0f} ms"),
        ("the log is not climbing a staircase",
         last_third <= max(first_third * 2, 16 * 1048576),
         f"first third {first_third/1048576:.0f} MB, last third {last_third/1048576:.0f} MB"),
        ("time above the hard limit is ~0",
         time_above_hard <= doctor_every,
         f"{time_above_hard:.0f} s"),
        ("reclaim was observed",
         manager.metrics["truncate_succeeded"] > 0 or max(peaks, default=0) < SOFT_LIMIT_BYTES,
         f"{manager.metrics['truncate_succeeded']} successful truncates, "
         f"peak {max(peaks, default=0)/1048576:.0f} MB"),
        ("no doctor scan failed",
         all(d["exit_code"] != 3 for d in doctor_runs),
         f"{sum(1 for d in doctor_runs if d['exit_code'] == 3)} scan error(s)"),
    ]

    payload = {
        "minutes": minutes, "doctor_every_seconds": doctor_every,
        "writes": written, "doctor_runs": len(doctor_runs),
        "soft_limit_bytes": SOFT_LIMIT_BYTES, "hard_limit_bytes": HARD_LIMIT_BYTES,
        "reader_hold_ms": {
            "p50": _pct(holds, 0.50), "p95": _pct(holds, 0.95),
            "p99": _pct(holds, 0.99), "max": round(max(holds, default=0), 2),
            "samples": len(holds),
        },
        "wal_series": wal_series,
        "doctor_detail": doctor_runs[-20:],
        "wal_manager": manager.status().as_dict(),
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "passed": all(ok for _, ok, _ in checks),
        "note": "stress gate, NOT durability evidence",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"         {detail}")
    print("=" * 70)
    print("  GATE PASSED — Run 9 may be prepared" if payload["passed"]
          else "  GATE FAILED — do not start Run 9")
    print(f"  {out}")
    os_.close()
    return 0 if payload["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="wal_torture_gate")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--doctor-every", type=float, default=30.0,
                    help="seconds; the canary uses 300, this amplifies")
    ap.add_argument("--rate", type=float, default=70.0,
                    help="writes/second; Run 8's measured rate was 70")
    ap.add_argument("--out", default=str(_REPO / "reports" / "v082" / "wal_torture_gate.json"))
    ap.add_argument("--seed-from", default=None,
                    help="copy this database first; without it the doctor is fast "
                         "and the gate proves nothing")
    args = ap.parse_args(argv)
    return run(args.minutes, args.doctor_every, args.rate, Path(args.out), args.seed_from)


if __name__ == "__main__":
    raise SystemExit(main())
