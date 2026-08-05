"""`bio-agent-os doctor` and `bio-agent-os projection …`.

Thin by design: every decision lives in `diagnostics.py` and
`reconciliation.py`, so the same behaviour is available to a dashboard, a
scheduled job or a test without going through argv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diagnostics import DeepDoctor
from .facade import MemoryOS
from .projection_capability import render as capability_render
from .projection_engine import ProjectionReplayEngine
from .reconciliation import ReconciliationEngine, write_audit
from .reconciliation_worker import worker_for


def _runtime(args: argparse.Namespace) -> MemoryOS:
    return MemoryOS(args.db)


def cmd_doctor(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    report = DeepDoctor(runtime.events.conn, tenant_id=args.tenant).run(deep=args.deep)
    if args.json:
        sys.stdout.write(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(report.render() + "\n")
    return report.exit_code


def cmd_status(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    engine = ProjectionReplayEngine(runtime.events.conn, runtime.events.outbox)
    payload = {
        "projection": engine.status(),
        "shadow_records": runtime.shadow_memories.count(),
        "mode": runtime.projection_mode.value,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return 0
    sys.stdout.write(capability_render() + "\n\n")
    for key, value in payload["projection"].items():
        sys.stdout.write(f"  {key:<26} {value}\n")
    sys.stdout.write(f"  {'shadow_records':<26} {payload['shadow_records']}\n")
    sys.stdout.write(f"  {'projection_mode':<26} {payload['mode']}\n")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Dry run unless `--repair` is passed, and never repairs without an audit."""
    runtime = _runtime(args)
    engine = ReconciliationEngine(
        runtime.events.conn, tenant_id=args.tenant, operator=args.operator
    )
    result = engine.run(deep=True, repair=args.repair, event_id=args.event_id)
    sys.stdout.write(result.render() + "\n")

    if args.repair:
        json_path, md_path = write_audit(result, Path(args.reports))
        sys.stdout.write(f"\n  audit: {json_path}\n  audit: {md_path}\n")
    return 0 if result.after is None or result.after.exit_code == 0 else result.before.exit_code


def cmd_replay(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    engine = ProjectionReplayEngine(runtime.events.conn, runtime.events.outbox)
    report = engine.replay(
        event_id=args.event_id, tenant_id=args.tenant, dry_run=not args.apply
    )
    sys.stdout.write(report.render() + "\n")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    worker = worker_for(runtime, worker_id=args.worker_id, tenant_id=args.tenant)
    if args.forever:
        worker.install_signal_handlers()
        worker.run_forever(batch_size=args.batch_size, max_seconds=args.max_seconds)
    else:
        worker.run_once(batch_size=args.batch_size)
    sys.stdout.write(json.dumps(worker.metrics.as_dict(), indent=2) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bio-agent-os")
    p.add_argument("--db", default="bio_agent_os.db", help="database path")
    p.add_argument("--tenant", help="scope to one tenant")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="consistency diagnosis (read-only)")
    d.add_argument("--deep", action="store_true", help="run the full check set")
    d.add_argument("--json", action="store_true", help="machine-readable output")
    d.set_defaults(func=cmd_doctor)

    proj = sub.add_parser("projection", help="projection pipeline operations")
    psub = proj.add_subparsers(dest="projection_command", required=True)

    st = psub.add_parser("status")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    rec = psub.add_parser("reconcile", help="plan and optionally apply repairs")
    group = rec.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="repair", action="store_false",
                       help="plan only (the default)")
    group.add_argument("--repair", dest="repair", action="store_true",
                       help="apply the plan; always writes an audit report")
    rec.add_argument("--event-id", help="limit to one event")
    rec.add_argument("--operator", default="cli", help="who is running this")
    rec.add_argument("--reports", default="reports")
    rec.set_defaults(func=cmd_reconcile, repair=False)

    rep = psub.add_parser("replay", help="requeue owed projections")
    rep.add_argument("--event-id")
    rep.add_argument("--all", action="store_true", help="every event in scope")
    rep.add_argument("--apply", action="store_true", help="act; default is dry-run")
    rep.set_defaults(func=cmd_replay)

    wk = psub.add_parser("worker", help="drain the projection queue")
    wk.add_argument("--once", dest="forever", action="store_false")
    wk.add_argument("--forever", dest="forever", action="store_true")
    wk.add_argument("--batch-size", type=int, default=100)
    wk.add_argument("--max-seconds", type=float, default=None)
    wk.add_argument("--worker-id", default=None)
    wk.set_defaults(func=cmd_worker, forever=False)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return 130
    except Exception as exc:  # a doctor that crashes must not look healthy
        sys.stderr.write(f"doctor failed: {type(exc).__name__}: {exc}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
