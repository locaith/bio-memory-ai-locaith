"""CLI: `python -m benchmarks.reliability <command>`.

Baseline first, then any optimisation, then the same command again. Results go
to `reports/v082/` under the name the command was given, so an apples-to-apples
comparison is a diff rather than an argument.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.reliability import corpus, environment, recovery, soak, workloads  # noqa: E402

REPORTS = _REPO / "reports" / "v082"


def _run_dir(keep: bool, label: str) -> Path:
    if keep:
        target = _REPO / ".bench" / label
        target.mkdir(parents=True, exist_ok=True)
        return target
    return Path(tempfile.mkdtemp(prefix=f"bench_{label}_"))


def _print(text: str) -> None:
    """Always flushed. Redirected stdout is block-buffered, so an unflushed
    progress line arrives with the *next* one, which makes a long run look
    stalled at the step it has already finished."""
    print(text, flush=True)


def _emit(payload: dict[str, Any], name: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _print(f"\n  written: {path}")
    return path


def cmd_environment(args: argparse.Namespace) -> int:
    record = environment.capture(repo=_REPO)
    record["corpus_domains"] = list(corpus.DOMAINS)
    record["corpus_tenants"] = corpus.TENANTS
    record["corpus_workspaces_per_tenant"] = corpus.WORKSPACES_PER_TENANT
    _emit(record, "environment.json")
    for key in ("os", "cpu_model", "cpu_cores_logical", "ram_gb", "python",
                "sqlite_version", "process_start_method", "commit_sha"):
        _print(f"  {key:<26} {record[key]}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Everything except the soak, in one pass, into one file."""
    run_dir = _run_dir(args.keep, "baseline")
    started = time.time()
    results: dict[str, Any] = {
        "label": args.label,
        "started_at": started,
        "environment": environment.capture(repo=_REPO, extra={"run_dir": str(run_dir)}),
        "corpus": {
            "domains": list(corpus.DOMAINS),
            "tenants": corpus.TENANTS,
            "workspaces_per_tenant": corpus.WORKSPACES_PER_TENANT,
            "domain_distribution_10k": corpus.distribution(10_000),
        },
    }

    _print("\n== A. append only ==")
    results["A_append_only"] = []
    for producers in args.producers:
        _print(f"   {args.events} events, {producers} producer(s) ...")
        out = workloads.workload_a_append_only(
            run_dir=run_dir, events=args.events, producers=producers
        )
        _print(f"     {out['append_throughput_per_s']:>9.1f} ev/s   "
              f"lock_errors={out['lock_errors']}  wall={out['wall_seconds']}s")
        results["A_append_only"].append(out)

    _print("\n== B. projection only ==")
    results["B_projection_only"] = []
    for workers in args.workers:
        _print(f"   {args.events} jobs, {workers} worker(s) ...")
        out = workloads.workload_b_projection_only(
            run_dir=run_dir, events=args.events, workers=workers
        )
        lat = out["latency"]["end_to_end_visibility"]
        _print(f"     {out['projection_throughput_per_s']:>9.1f} job/s  "
              f"p95={lat.get('p95_ms')}ms  p99={lat.get('p99_ms')}ms  "
              f"doctor_after={out['doctor_after']['exit_code']}")
        results["B_projection_only"].append(out)

    _print("\n== C. mixed live ==")
    results["C_mixed"] = []
    for producers, workers in args.mixed:
        _print(f"   {args.events} events, {producers}p + {workers}w ...")
        out = workloads.workload_c_mixed(
            run_dir=run_dir, events=args.events, producers=producers, workers=workers
        )
        lat = out["latency"]["end_to_end_visibility"]
        _print(f"     produce={out['producer_throughput_per_s']:.1f}/s  "
              f"project={out['worker_throughput_per_s']:.1f}/s  "
              f"p95={lat.get('p95_ms')}ms  peak_queue={out['peak_queue_depth']}  "
              f"drained={out['queue_drained']}")
        results["C_mixed"].append(out)

    _print("\n== D. shadow ==")
    results["D_shadow"] = workloads.workload_d_shadow(
        run_dir=run_dir, events=args.shadow_events
    )
    d = results["D_shadow"]
    _print(f"     match={d['match_rate_pct']}%  overhead_p95={d['observe_overhead_p95_pct']}%  "
          f"leak={d['shadow_rows_visible_in_production']}")

    _print("\n== E. recovery ==")
    results["E_recovery"] = recovery.workload_e_recovery(
        run_dir=run_dir, events=args.recovery_events, workers=4
    )
    e = results["E_recovery"]
    _print(f"     lost_events={e['lost_events']}  "
          f"integrity={e['scenarios'][-1]['sqlite_integrity_check']}  "
          f"doctor={e['doctor_after']['exit_code']}")

    _print("\n== F. deliberate failure distribution ==")
    results["F_fault_distribution"] = workloads.workload_faults(
        run_dir=run_dir, events=args.fault_events, workers=4
    )
    f = results["F_fault_distribution"]
    _print(f"     terminal={f['terminal_states']}  "
          f"unexplained_dead_letters={f['unexplained_dead_letters']}  "
          f"stale_remaining={f['stale_claims_remaining']}")

    results["total_seconds"] = round(time.time() - started, 1)
    _emit(results, args.out)
    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    return 0


def cmd_scale(args: argparse.Namespace) -> int:
    """One configuration at several corpus sizes: where does it saturate."""
    run_dir = _run_dir(args.keep, "scale")
    results: dict[str, Any] = {
        "label": "scale",
        "environment": environment.capture(repo=_REPO),
        "runs": [],
    }
    for events in args.sizes:
        _print(f"\n== {events:,} events, {args.producers}p + {args.workers}w ==")
        out = workloads.workload_c_mixed(
            run_dir=run_dir, events=events, producers=args.producers, workers=args.workers,
            sample_interval=1.0,
        )
        lat = out["latency"]["end_to_end_visibility"]
        _print(f"   produce={out['producer_throughput_per_s']:.1f}/s  "
              f"project={out['worker_throughput_per_s']:.1f}/s  "
              f"p95={lat.get('p95_ms')}ms  p99={lat.get('p99_ms')}ms  "
              f"db={out['database']['total_bytes'] / 1048576:.1f}MB  "
              f"doctor={out['doctor_after']['exit_code']}")
        results["runs"].append(out)
    _emit(results, args.out)
    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    return 0


def cmd_soak(args: argparse.Namespace) -> int:
    run_dir = REPORTS
    run_dir.mkdir(parents=True, exist_ok=True)
    work = _run_dir(True, "soak")
    _print(f"  soaking for {args.seconds}s ({args.seconds / 3600:.2f}h), "
           f"{args.producers}p + {args.workers}w at {args.rate}/s target")
    result = soak.run(
        run_dir=REPORTS, seconds=args.seconds, producers=args.producers,
        workers=args.workers, target_rate=args.rate,
        worker_restart_every=args.restart_every, doctor_every=args.doctor_every,
    )
    result["environment"] = environment.capture(repo=_REPO, extra={"work_dir": str(work)})
    _emit(result, args.out)
    _print(f"  appended={result['appended']}  completed={result['completed']}  "
           f"drained={result['queue_drained_after_stop']}  "
           f"rss {result['rss_mb_first']}MB -> {result['rss_mb_last']}MB  "
           f"drift={result['latency_drift_pct']}%")
    return 0


def _pair(text: str) -> tuple[int, int]:
    a, b = text.split(":")
    return int(a), int(b)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m benchmarks.reliability")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("environment", help="capture the machine and write environment.json")
    e.set_defaults(func=cmd_environment)

    b = sub.add_parser("baseline", help="workloads A-F in one pass")
    b.add_argument("--events", type=int, default=10_000)
    b.add_argument("--shadow-events", type=int, default=10_000)
    b.add_argument("--recovery-events", type=int, default=4_000)
    b.add_argument("--fault-events", type=int, default=10_000)
    b.add_argument("--producers", type=int, nargs="+", default=[1, 2, 4, 8])
    b.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    b.add_argument("--mixed", type=_pair, nargs="+",
                   default=[(1, 1), (2, 2), (4, 4), (4, 8), (8, 4)])
    b.add_argument("--label", default="baseline")
    b.add_argument("--out", default="reliability_baseline.json")
    b.add_argument("--keep", action="store_true", help="keep the databases for inspection")
    b.set_defaults(func=cmd_baseline)

    s = sub.add_parser("scale", help="one configuration at several corpus sizes")
    s.add_argument("--sizes", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    s.add_argument("--producers", type=int, default=4)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", default="reliability_scale.json")
    s.add_argument("--keep", action="store_true")
    s.set_defaults(func=cmd_scale)

    k = sub.add_parser("soak", help="long-running mixed load with a 60s time series")
    k.add_argument("--seconds", type=float, default=3600)
    k.add_argument("--producers", type=int, default=2)
    k.add_argument("--workers", type=int, default=3)
    k.add_argument("--rate", type=float, default=150.0)
    k.add_argument("--restart-every", type=float, default=900.0)
    k.add_argument("--doctor-every", type=int, default=5,
                   help="run the quick scan every Nth sample; it is a full "
                        "scan and grows with the database")
    k.add_argument("--out", default="reliability_soak.json")
    k.set_defaults(func=cmd_soak)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
