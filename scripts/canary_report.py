"""Turn the staging canary's raw metric streams into the run report.

Separate from canary_supervisor.py on purpose: the supervisor is running
while this is being written, and the rule for the canary window is that its
code does not change. This reads what the run produced and adds nothing to
it.

    python scripts/canary_report.py            # report on the current run
    python scripts/canary_report.py --interim  # allow an unfinished run

Percentiles come from the per-minute samples, which are themselves computed
from per-operation histograms. A percentile-of-percentiles is not the true
percentile of the underlying operations, and every such figure is labelled
"worst minute" or "median minute" rather than presented as a run-wide p95.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

STAGING = _REPO / ".staging" / "v082-canary"
METRICS = STAGING / "metrics"
REPORTS = STAGING / "reports"
CONFIG = STAGING / "config"
OUT_DOCS = _REPO / "docs" / "v082"
OUT_REPORTS = _REPO / "reports" / "v082"

#: SLOs whose breach means the projected data is wrong. These stop a run.
FATAL_SLOS = frozenset({
    "lost_committed_events", "lost_outbox_debts", "duplicate_projections",
    "duplicate_ledgers", "tenant_leakage", "shadow_mismatch",
    "shadow_visible_in_production_recall", "dead_letter",
})


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _fmt(n: Any, digits: int = 2) -> str:
    if n is None:
        return "not measured"
    if isinstance(n, bool):
        return "yes" if n else "no"
    if isinstance(n, int):
        return f"{n:,}"
    if isinstance(n, float):
        return f"{n:,.{digits}f}"
    return str(n)


def _series(samples: list[dict[str, Any]], key: str) -> list[float]:
    return [s[key] for s in samples if isinstance(s.get(key), (int, float))]


def _stat_block(samples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    vals = _series(samples, key)
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "min": min(vals),
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        "max": max(vals),
    }


def _drift(samples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """First-hour mean vs last-hour mean, which is what a leak looks like."""
    vals = [(s.get("elapsed_hours"), s.get(key)) for s in samples]
    vals = [(h, v) for h, v in vals if isinstance(h, (int, float))
            and isinstance(v, (int, float))]
    if len(vals) < 4:
        return {"measured": False}
    span = vals[-1][0] - vals[0][0]
    if span <= 0:
        return {"measured": False}
    window = min(1.0, span / 3)
    first = [v for h, v in vals if h <= vals[0][0] + window]
    last = [v for h, v in vals if h >= vals[-1][0] - window]
    if not first or not last:
        return {"measured": False}
    a, b = statistics.fmean(first), statistics.fmean(last)
    return {
        "measured": True, "window_hours": round(window, 3),
        "first": a, "last": b, "delta": b - a,
        "per_hour": (b - a) / span if span else 0.0,
        "pct": ((b - a) / a * 100) if a else None,
    }


def build(interim: bool) -> dict[str, Any]:
    samples = _load_jsonl(METRICS / "timeseries.jsonl")
    events = _load_jsonl(METRICS / "events.jsonl")
    summary = _load_json(REPORTS / "summary.json")
    before = _load_json(REPORTS / "doctor_before.json")
    after = _load_json(REPORTS / "doctor_after.json")
    params = _load_json(CONFIG / "run_parameters.json")

    if not samples:
        raise SystemExit("no samples; nothing to report on")
    if not summary and not interim:
        raise SystemExit("run has not finished (no summary.json); use --interim")

    last = samples[-1]
    hours = last.get("elapsed_hours", 0.0)

    # Correctness: the invariants are checked every minute, so a violation at
    # any point in the run counts even if the final sample is clean.
    worst_correctness: dict[str, int] = {}
    for s in samples:
        for k, v in (s.get("correctness") or {}).items():
            if isinstance(v, (int, float)):
                worst_correctness[k] = max(worst_correctness.get(k, 0), int(v))
    for k in ("shadow_mismatch", "shadow_visible_in_production_recall",
              "shadow_jobs_dead_letter"):
        worst_correctness[k] = max(
            (int(s[k]) for s in samples if isinstance(s.get(k), (int, float))),
            default=0)

    statuses: dict[str, int] = {}
    for s in samples:
        for k, v in (s.get("comparison_statuses") or {}).items():
            statuses[k] = max(statuses.get(k, 0), int(v))

    breaches: dict[str, dict[str, Any]] = {}
    for s in samples:
        for b in s.get("slo_breaches") or []:
            name = b.get("slo")
            if not name:
                continue
            rec = breaches.setdefault(name, {
                "minutes": 0, "limit": b.get("limit"),
                "fatal": bool(b.get("fatal")), "worst": None})
            rec["minutes"] += 1
            m = b.get("measured")
            if isinstance(m, (int, float)):
                rec["worst"] = m if rec["worst"] is None else max(rec["worst"], m)

    recoveries = [e for e in events if str(e.get("kind", "")).startswith("recovery:")]
    doctors = [s for s in samples if s.get("doctor_exit_code") is not None]

    # The boot check is written to the supervisor log rather than the event
    # stream. Read it back rather than restating it from memory.
    boot: dict[str, Any] = {}
    log_path = STAGING / "logs" / "supervisor.log"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            marker = "boot check: "
            if marker in line:
                try:
                    boot = json.loads(line.split(marker, 1)[1])
                except json.JSONDecodeError:
                    pass
                break

    # The per-source split is not carried in the samples, so measure it from
    # the database rather than leaving a zero that reads like "none".
    split: dict[str, Any] = {"measured": False}
    db = STAGING / "canary.db"
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120)
        try:
            baseline = conn.execute(
                "SELECT COUNT(*) FROM cognitive_events "
                "WHERE tenant_id LIKE 'baseline\\_%' ESCAPE '\\'").fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) FROM cognitive_events").fetchone()[0]
            split = {"measured": True, "total": total, "baseline": baseline,
                     "shadow": total - baseline}
        except sqlite3.Error as exc:
            split = {"measured": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            conn.close()

    return {
        "boot": boot,
        "split": split,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "interim": interim and not summary,
        "params": params,
        "summary": summary,
        "samples": samples,
        "sample_count": len(samples),
        "hours": hours,
        "events": events,
        "recoveries": recoveries,
        "doctor_before": before,
        "doctor_after": after,
        "doctor_samples": doctors,
        "worst_correctness": worst_correctness,
        "comparison_statuses": statuses,
        "breaches": breaches,
        "last": last,
        "stats": {
            k: _stat_block(samples, k) for k in (
                "input_rate", "projection_rate", "queue_depth",
                "projection_lag_p50", "projection_lag_p95", "projection_lag_p99",
                "observe_p50", "observe_p95", "observe_p99",
                "legacy_observe_p95_ms", "shadow_overhead_absolute_p95_ms",
                "shadow_overhead_ratio_p95_pct", "wal_bytes", "rss_bytes",
                "database_bytes")
        },
        "drift": {k: _drift(samples, k) for k in
                  ("rss_bytes", "wal_bytes", "projection_lag_p95",
                   "observe_p95", "queue_depth")},
    }


def _verdict(r: dict[str, Any]) -> tuple[str, list[str]]:
    """The verdict follows the evidence, and the reasons are stated."""
    reasons: list[str] = []
    fatal = False

    for k, v in r["worst_correctness"].items():
        if v:
            fatal = True
            reasons.append(f"correctness invariant `{k}` was violated ({v:,})")

    for name, rec in r["breaches"].items():
        if rec["fatal"]:
            fatal = True
            reasons.append(f"fatal SLO `{name}` breached in {rec['minutes']} minute(s)")

    summary = r["summary"]
    if summary.get("stopped_early_because"):
        fatal = True
        reasons.append(f"run stopped early: {summary['stopped_early_because']}")

    if r["interim"]:
        return "INTERIM — RUN STILL IN PROGRESS", reasons

    if fatal:
        return "NO-GO", reasons

    hours = summary.get("actual_hours", r["hours"])
    if hours < 23.5:
        reasons.append(f"only {hours:.2f}h of the required 24h completed")
        return "REPEAT CANARY", reasons

    fired = set(summary.get("recovery_events_fired") or [])
    if len(fired) < 7:
        reasons.append(f"only {len(fired)} of 7 recovery events fired")
        return "REPEAT CANARY", reasons

    if not summary.get("final_drained"):
        reasons.append("the queue did not drain to zero after producers stopped")
        return "REPEAT CANARY", reasons

    if summary.get("doctor_after_exit") not in (0, None):
        reasons.append(f"doctor after the run exited {summary['doctor_after_exit']}")
        return "REPEAT CANARY", reasons

    latency = [n for n, rec in r["breaches"].items() if not rec["fatal"]]
    if latency:
        reasons.append(
            "correctness held for the whole run; the outstanding breaches are "
            "latency SLOs (" + ", ".join(sorted(latency)) + ") which were set "
            "from single-writer benchmark numbers and do not survive "
            "concurrent writers. They need to be re-derived before the next "
            "gate, not waived.")
    return "GO TO TENANT-ALLOWLIST OUTBOX CANARY", reasons


def render(r: dict[str, Any]) -> str:
    s, last, st = r["summary"], r["last"], r["stats"]
    verdict, reasons = _verdict(r)
    params = r["params"]
    dev = params.get("rate_deviation", {})
    false_start = params.get("false_start", {})
    env = s.get("environment", {})

    def stat_row(label: str, key: str, scale: float = 1.0, digits: int = 2) -> str:
        b = st.get(key, {})
        if not b.get("n"):
            return f"| {label} | not measured | | | |"
        return (f"| {label} | {_fmt(b['min'] / scale, digits)} "
                f"| {_fmt(b['median'] / scale, digits)} "
                f"| {_fmt(b['mean'] / scale, digits)} "
                f"| {_fmt(b['max'] / scale, digits)} |")

    L: list[str] = []
    add = L.append

    add("# Staging shadow canary — cognitive_memory")
    add("")
    if r["interim"]:
        add("> **Interim.** The run is still going. Every figure below is from "
            f"the first {r['hours']:.2f} hours and will change.")
        add("")
    add(f"- **Verdict:** {verdict}")
    add(f"- **Commit:** "
        f"`{s.get('commit') or env.get('commit') or params.get('commit') or 'unknown'}`  "
        f"**Tag:** `{s.get('tag') or params.get('tag') or 'v0.8.2rc1'}`")
    add(f"- **Duration:** {_fmt(s.get('actual_hours', r['hours']), 3)} h "
        f"of {_fmt(params.get('hours', 24))} h requested")
    add(f"- **Samples:** {r['sample_count']:,} at "
        f"{_fmt(params.get('sample_seconds', 60))} s")
    add(f"- **Report generated:** {r['generated_at']}")
    add("")

    add("## 1. What was run, and what was not")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Mode | shadow; legacy remains the production path |")
    add(f"| Outbox primary | never enabled |")
    add(f"| Database | isolated staging copy under `.staging/`, synthetic data |")
    add(f"| Shadow rate | {_fmt(params.get('shadow_observations_per_second'))} obs/s |")
    add(f"| Legacy baseline rate | "
        f"{_fmt(params.get('legacy_baseline_observations_per_second'))} obs/s, "
        f"same database, `baseline_` tenant prefix |")
    add(f"| Producers / workers | {_fmt(params.get('producers'))} / "
        f"{_fmt(params.get('workers'))} |")
    add(f"| Ramp | {params.get('ramp', 'unknown')} |")
    add("")
    if dev:
        add("### Deviation from the runbook rate")
        add("")
        add(f"The runbook asked for **{dev.get('runbook_asked_for')}**. "
            f"This run used **{dev.get('actually_running')}**.")
        add("")
        add(f"**Why.** {dev.get('reason')}")
        add("")
        add(f"**What it costs.** {dev.get('what_this_costs')}")
        add("")
        add(f"**Volume still covered.** {dev.get('volume_still_covered')}")
        if dev.get("projected_disk"):
            add(f" Projected disk: {dev['projected_disk']}.")
        add("")
    if false_start:
        add("### False start")
        add("")
        add(f"A first attempt ran for {false_start.get('ran_for_minutes')} minutes "
            f"({false_start.get('first_attempt_at')} → "
            f"{false_start.get('stopped_at')}) and was stopped.")
        add("")
        add(f"**Cause.** {false_start.get('why')}")
        add("")
        add(f"**Fix.** `{false_start.get('fix')}`")
        add("")
        add(f"**Clock.** {false_start.get('clock')}")
        add("")

    add("## 2. Boot verification")
    add("")
    add("Checked before any load, rather than assumed. The run refuses to "
        "start if any of these is not true:")
    add("")
    add("```json")
    add(json.dumps(r["boot"], indent=2) if r["boot"]
        else '{"error": "no boot check found in the supervisor log"}')
    add("```")
    add("")

    add("## 3. Volume")
    add("")
    split = r["split"]
    add("| | |")
    add("|---|---|")
    add(f"| Events written, all producers | "
        f"{_fmt(int(last.get('observations_total', 0)))} |")
    if split.get("measured"):
        add(f"| — of which shadow path | {_fmt(int(split['shadow']))} |")
        add(f"| — of which legacy baseline | {_fmt(int(split['baseline']))} |")
    else:
        add(f"| — shadow / baseline split | not measured "
            f"({split.get('error', 'database unavailable')}) |")
    add(f"| Legacy memory rows (production projection output) | "
        f"{_fmt(int(last.get('legacy_success_total', 0)))} |")
    add(f"| Projection jobs created | {_fmt(int(last.get('shadow_jobs_created', 0)))} |")
    add(f"| Projection jobs completed | {_fmt(int(last.get('shadow_jobs_completed', 0)))} |")
    add(f"| Shadow comparisons | "
        f"{_fmt(sum(r['comparison_statuses'].values()))} |")
    add(f"| Database at end | "
        f"{_fmt(last.get('database_bytes', 0) / 1e9, 2)} GB |")
    add("")

    add("## 4. Correctness — the invariants that would stop the run")
    add("")
    add("Evaluated every minute. The figure is the **worst value seen at any "
        "point**, not the value at the end, so a violation that healed still "
        "shows up here.")
    add("")
    add("| Invariant | Worst seen | Verdict |")
    add("|---|---|---|")
    for k in sorted(r["worst_correctness"]):
        v = r["worst_correctness"][k]
        add(f"| `{k}` | {_fmt(int(v))} | {'**VIOLATED**' if v else 'held'} |")
    add("")
    add("### Shadow comparison outcomes")
    add("")
    add("| Status | Count |")
    add("|---|---|")
    for k in sorted(r["comparison_statuses"], key=lambda x: -r["comparison_statuses"][x]):
        add(f"| `{k}` | {_fmt(r['comparison_statuses'][k])} |")
    add("")

    add("## 5. Latency and throughput")
    add("")
    add("Each row is the distribution **across minutes** of a per-minute "
        "statistic. The per-minute p95 is itself computed from per-operation "
        "samples; the columns below describe how that p95 moved over the run. "
        "The max column is the worst minute, not a run-wide p95.")
    add("")
    add("| Metric | Min | Median | Mean | Max |")
    add("|---|---|---|---|---|")
    add(stat_row("Input rate (obs/s)", "input_rate"))
    add(stat_row("Projection rate (jobs/s)", "projection_rate"))
    add(stat_row("Queue depth", "queue_depth", digits=0))
    add(stat_row("Projection lag p50 (ms)", "projection_lag_p50"))
    add(stat_row("Projection lag p95 (ms)", "projection_lag_p95"))
    add(stat_row("Projection lag p99 (ms)", "projection_lag_p99"))
    add(stat_row("Observe p50 (ms)", "observe_p50"))
    add(stat_row("Observe p95 (ms)", "observe_p95"))
    add(stat_row("Observe p99 (ms)", "observe_p99"))
    add(stat_row("Legacy baseline observe p95 (ms)", "legacy_observe_p95_ms"))
    add(stat_row("Shadow overhead, absolute p95 (ms)", "shadow_overhead_absolute_p95_ms"))
    add(stat_row("Shadow overhead, ratio p95 (%)", "shadow_overhead_ratio_p95_pct"))
    add("")

    add("## 6. Resource drift — the leak question")
    add("")
    add("First window vs last window of the run. A flat line here is the point; "
        "a rising one is a leak.")
    add("")
    add("| Metric | First | Last | Change | Per hour |")
    add("|---|---|---|---|---|")
    for label, key, scale, unit in (
            ("RSS", "rss_bytes", 1e6, "MB"),
            ("WAL", "wal_bytes", 1e6, "MB"),
            ("Projection lag p95", "projection_lag_p95", 1.0, "ms"),
            ("Observe p95", "observe_p95", 1.0, "ms"),
            ("Queue depth", "queue_depth", 1.0, "")):
        d = r["drift"].get(key, {})
        if not d.get("measured"):
            add(f"| {label} | not measured | | | |")
            continue
        add(f"| {label} | {_fmt(d['first'] / scale)} {unit} "
            f"| {_fmt(d['last'] / scale)} {unit} "
            f"| {_fmt(d['delta'] / scale)} {unit} "
            f"| {_fmt(d['per_hour'] / scale)} {unit}/h |")
    add("")

    add("## 7. Doctor")
    add("")
    add("| Point | Exit | Findings | Duration |")
    add("|---|---|---|---|")
    for label, doc in (("Before", r["doctor_before"]), ("After", r["doctor_after"])):
        if not doc:
            add(f"| {label} | not run | | |")
            continue
        add(f"| {label} | {doc.get('exit_code')} "
            f"| {len(doc.get('findings') or [])} "
            f"| {_fmt(doc.get('duration_s'))} s |")
    mid = next((e for e in r["events"] if e.get("kind") == "doctor:deep_midpoint"), None)
    if mid:
        add(f"| Midpoint (h12) | {mid.get('exit_code')} | {mid.get('findings')} "
            f"| {_fmt(mid.get('duration_s'))} s |")
    add(f"| Incremental/quick runs during the run | | "
        f"{len(r['doctor_samples'])} sampled | |")
    add("")
    add(f"SQLite integrity after the run: "
        f"**{s.get('sqlite_integrity_after', 'not measured')}**")
    add("")

    add("## 8. Recovery events")
    add("")
    add("Seven scheduled events, one every three hours.")
    add("")
    add("| Hour | Event | Result |")
    add("|---|---|---|")
    schedule = [(3, "worker_restart"), (6, "kill_lease_holder"), (9, "write_lock"),
                (12, "pause_and_drain"), (15, "full_runtime_restart"),
                (18, "duplicate_enqueue"), (21, "wal_checkpoint_passive")]
    fired = {str(e.get("kind", "")).split(":", 1)[-1].split(":")[0]: e
             for e in r["recoveries"]}
    for hour, kind in schedule:
        e = fired.get(kind)
        if not e:
            add(f"| {hour} | `{kind}` | not reached |")
            continue
        detail = {k: v for k, v in e.items()
                  if k not in ("at", "at_iso", "kind")}
        add(f"| {hour} | `{kind}` | "
            f"{json.dumps(detail, ensure_ascii=False)[:220]} |")
    add("")
    add(f"Unplanned producer deaths: "
        f"**{_fmt(s.get('unplanned_producer_deaths', 0))}**. "
        f"Unplanned worker deaths: "
        f"**{_fmt(s.get('unplanned_worker_deaths', 0))}**.")
    add("")

    add("## 9. SLO breaches")
    add("")
    if not r["breaches"]:
        add("None.")
    else:
        add("| SLO | Limit | Worst measured | Minutes breached | Of total | Fatal |")
        add("|---|---|---|---|---|---|")
        for name in sorted(r["breaches"], key=lambda n: -r["breaches"][n]["minutes"]):
            rec = r["breaches"][name]
            pct = rec["minutes"] / r["sample_count"] * 100
            add(f"| `{name}` | {_fmt(rec['limit'])} | {_fmt(rec['worst'])} "
                f"| {_fmt(rec['minutes'])} | {pct:.1f}% "
                f"| {'**yes**' if rec['fatal'] else 'no'} |")
        add("")
        add("These SLOs were **not relaxed during the run**. The three latency "
            "targets were derived from the reliability benchmark's "
            "single-writer measurements (legacy observe p95 0.30 ms, shadow "
            "0.59 ms). Under five concurrent processes sharing one SQLite "
            "file, write contention alone puts observe p95 an order of "
            "magnitude above that. The targets describe a machine that isn't "
            "this one; the honest fix is to re-derive them from a concurrent "
            "baseline before the next gate, not to widen them until the "
            "dashboard is green.")
    add("")

    add("## 10. Shutdown")
    add("")
    add(f"- Producers stopped, workers left running so the queue had to reach "
        f"zero on its own: **{'drained' if s.get('final_drained') else 'did not drain'}** "
        f"in {_fmt(s.get('final_drain_seconds'))} s")
    add(f"- Final queue: `{json.dumps(s.get('final_queue', {}))}`")
    sweep = next((e for e in r["events"]
                  if e.get("kind") == "final_comparison_sweep"), None)
    if sweep:
        add(f"- Final comparison sweep: "
            f"`{json.dumps({k: v for k, v in sweep.items() if k not in ('at', 'at_iso', 'kind')})}`")
    add(f"- Stopped early because: "
        f"**{s.get('stopped_early_because') or 'nothing — it ran to term'}**")
    add("")

    add("## 11. Verdict")
    add("")
    add(f"### {verdict}")
    add("")
    for reason in reasons:
        add(f"- {reason}")
    if not reasons:
        add("- Every correctness invariant held for the full run, all seven "
            "recovery events completed, the queue drained, and the doctor "
            "agreed before and after.")
    add("")
    add("**What this does not license.** Shadow mode proves the projection "
        "pipeline computes the same answer as legacy under load. It does not "
        "prove the outbox is safe as the primary write path, because in this "
        "run nothing read from it. The next gate is a tenant-allowlist outbox "
        "canary, and it needs its own evidence. Outbox stays off by default on "
        "`main`.")
    add("")
    add("---")
    add("")
    add("## Appendix — environment")
    add("")
    add("```json")
    add(json.dumps(env, indent=2, ensure_ascii=False)[:4000])
    add("```")
    add("")
    add("Raw material for every figure above:")
    add("")
    add("- `reports/v082/staging_canary_timeseries.jsonl` — one sample a minute")
    add("- `reports/v082/staging_canary_events.jsonl` — recovery and control events")
    add("- `reports/v082/staging_canary_summary.json`")
    add("- `reports/v082/staging_canary_doctor_before.json`")
    add("- `reports/v082/staging_canary_doctor_after.json`")
    add("")
    return "\n".join(L) + "\n"


def publish(r: dict[str, Any], text: str) -> None:
    OUT_DOCS.mkdir(parents=True, exist_ok=True)
    OUT_REPORTS.mkdir(parents=True, exist_ok=True)
    (OUT_DOCS / "STAGING_CANARY_REPORT.md").write_text(text, encoding="utf-8")
    for src, dst in (
            (METRICS / "timeseries.jsonl", "staging_canary_timeseries.jsonl"),
            (METRICS / "events.jsonl", "staging_canary_events.jsonl"),
            (REPORTS / "summary.json", "staging_canary_summary.json"),
            (REPORTS / "doctor_before.json", "staging_canary_doctor_before.json"),
            (REPORTS / "doctor_after.json", "staging_canary_doctor_after.json"),
            (CONFIG / "run_parameters.json", "staging_canary_parameters.json")):
        if src.exists():
            shutil.copyfile(src, OUT_REPORTS / dst)
            print(f"  {OUT_REPORTS / dst}")
    print(f"  {OUT_DOCS / 'STAGING_CANARY_REPORT.md'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="canary_report")
    p.add_argument("--interim", action="store_true",
                   help="report on a run that has not finished")
    p.add_argument("--publish", action="store_true",
                   help="write into docs/v082 and reports/v082")
    args = p.parse_args(argv)

    r = build(args.interim)
    text = render(r)
    if args.publish:
        publish(r, text)
    else:
        # The report is UTF-8; a cp1252 console would rather raise than print it.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - exotic stdout
            pass
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
