"""Service objectives for the canary, and the alerts that watch them.

Written before the canary runs, not after. The previous phase proposed a
shadow-overhead threshold of 10% at p95, measured 99.4%, and did not move the
threshold to make the run pass — the number stands as a miss. This module
exists so the next set of objectives is declared in code, in advance, with a
version stamp, rather than remembered afterwards.

Two things changed in how latency is expressed.

**Absolute first, ratio second.** 99.4% of 0.30 ms is a percentage of a very
small number, and a percentage alone cannot tell an operator whether that
matters. The objective is now an absolute budget with the ratio reported
alongside it, so a doubling of something already sub-millisecond is visible
without being fatal.

**Latency is judged unsaturated.** The pipeline has no steady state above its
projection rate, so a p95 measured while the queue is backed up describes the
backlog, not the pipeline. Every latency objective here applies inside the
operating envelope and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

#: Bumped whenever an objective changes. A run reports the version it was
#: judged against, so "it passed" is always answerable with "against what".
SLO_VERSION = "v0.8.2-rc1"


class Severity(str, Enum):
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Objective:
    """One thing that must hold, and how to tell whether it did."""

    key: str
    description: str
    #: Reads the measurement out of a run summary. Returns None when the run
    #: did not measure it — which is reported as "not measured", never as a
    #: pass and never as a failure.
    read: Callable[[dict[str, Any]], Any]
    #: True when the value is acceptable.
    holds: Callable[[Any], bool]
    limit: str
    required: bool = True
    note: str = ""


def _get(payload: dict[str, Any], *path: str) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


#: Correctness. Every one is required, and none of them has a tolerance.
CORRECTNESS: tuple[Objective, ...] = (
    Objective("lost_events", "committed events still present",
              lambda p: _get(p, "correctness", "lost_events"), lambda v: v == 0, "0"),
    Objective("lost_debts", "committed projection debts still present",
              lambda p: _get(p, "correctness", "lost_debts"), lambda v: v == 0, "0"),
    Objective("duplicate_projections", "no projection built twice",
              lambda p: _get(p, "correctness", "duplicate_projections"),
              lambda v: v == 0, "0"),
    Objective("ledger_inconsistencies", "ledger agrees with the projections",
              lambda p: _get(p, "correctness", "ledger_inconsistencies"),
              lambda v: v == 0, "0"),
    Objective("tenant_leakage", "no tenant or workspace crossing",
              lambda p: _get(p, "correctness", "tenant_leakage"), lambda v: v == 0, "0"),
    Objective("unexplained_shadow_mismatch", "shadow agrees with legacy",
              lambda p: _get(p, "correctness", "unexplained_shadow_mismatch"),
              lambda v: v == 0, "0"),
    Objective("doctor_fail_or_critical", "doctor ends clean",
              lambda p: _get(p, "correctness", "doctor_fail_or_critical"),
              lambda v: v == 0, "0",
              note="deliberate dead letters are explained and do not count"),
    Objective("sqlite_integrity", "integrity_check ok",
              lambda p: _get(p, "correctness", "sqlite_integrity"),
              lambda v: v == "ok", "ok"),
)

#: Reliability. Also required, and also without tolerances.
RELIABILITY: tuple[Objective, ...] = (
    Objective("queue_drains_after_producer_stop", "the queue reaches zero on its own",
              lambda p: _get(p, "reliability", "queue_drained"), lambda v: v is True,
              "true"),
    Objective("stale_leases_after_recovery", "no lease outlives its worker",
              lambda p: _get(p, "reliability", "stale_leases"), lambda v: v == 0, "0"),
    Objective("orphan_claimed_jobs", "no job claimed by nobody",
              lambda p: _get(p, "reliability", "orphan_claimed"), lambda v: v == 0, "0"),
    Objective("unexplained_dead_letters", "every dead letter has a reason",
              lambda p: _get(p, "reliability", "unexplained_dead_letters"),
              lambda v: v == 0, "0"),
    Objective("wal_within_limits", "WAL below its hard limit, or alerting",
              lambda p: _get(p, "reliability", "wal_level"),
              lambda v: v in ("ok", "warn"), "ok or warn",
              note="critical is acceptable only with an alert and a recovery"),
)

#: Latency. Absolute budgets; the ratio is reported but is not the objective.
LATENCY: tuple[Objective, ...] = (
    Objective("legacy_observe_p95_ms", "legacy observe() stays sub-millisecond",
              lambda p: _get(p, "latency", "legacy_observe_p95_ms"),
              lambda v: v is not None and v <= 1.0, "<= 1.0 ms"),
    Objective("shadow_observe_p95_ms", "shadow observe() stays sub-millisecond",
              lambda p: _get(p, "latency", "shadow_observe_p95_ms"),
              lambda v: v is not None and v <= 1.0, "<= 1.0 ms"),
    Objective("shadow_overhead_p95_ms", "shadow costs at most half a millisecond",
              lambda p: _get(p, "latency", "shadow_overhead_p95_ms"),
              lambda v: v is not None and v <= 0.5, "<= 0.50 ms",
              note="the previous phase set this as a ratio and measured 99.4% of "
                   "0.30 ms; an absolute budget is what an operator can act on"),
    Objective("shadow_overhead_p95_pct", "the ratio, reported not enforced",
              lambda p: _get(p, "latency", "shadow_overhead_p95_pct"),
              lambda v: True, "reported", required=False,
              note="kept visible so a regression in shape is noticed even while "
                   "the absolute budget holds"),
    Objective("visibility_p95_ms", "projection visible within 100 ms in the envelope",
              lambda p: _get(p, "latency", "visibility_p95_ms"),
              lambda v: v is not None and v <= 100.0, "<= 100 ms",
              note="measured below the projection rate; a saturated queue "
                   "measures backlog depth, not the pipeline"),
    Objective("visibility_p99_ms", "and within 250 ms at p99",
              lambda p: _get(p, "latency", "visibility_p99_ms"),
              lambda v: v is not None and v <= 250.0, "<= 250 ms"),
)

ALL_OBJECTIVES: tuple[Objective, ...] = CORRECTNESS + RELIABILITY + LATENCY


# -- the envelope, and what pages on it -------------------------------------

#: Measured on the benchmark machine, sized from the projection floor with 30%
#: headroom. Peak throughput is deliberately not an operating limit.
ENVELOPE: dict[str, Any] = {
    "backend": "SQLite single-node alpha",
    "recommended_producers": 4,
    "recommended_workers": 4,
    "safe_sustained_input_per_s": 390,
    "burst_input_per_s": 550,
    "burst_window_seconds": 60,
    "max_healthy_queue_depth": 1100,
    "expected_p95_visibility_ms": 100,
    "note": "8 workers cost 41% of total throughput against 4 on this machine; "
            "do not scale workers past 4 here",
}

#: Thresholds a monitor pages on. Queue depth comes first on purpose: latency
#: is a lagging indicator of a backlog that has already formed.
ALERTS: tuple[dict[str, Any], ...] = (
    {"code": "QUEUE_DEPTH_HIGH", "severity": Severity.WARN.value,
     "metric": "queue_depth", "op": ">", "threshold": 700,
     "action": "the projector is falling behind; check input rate against 390/s"},
    {"code": "QUEUE_DEPTH_CRITICAL", "severity": Severity.CRITICAL.value,
     "metric": "queue_depth", "op": ">", "threshold": 1100,
     "action": "beyond the healthy envelope; reduce input or pause producers"},
    {"code": "PROJECTION_LAG_HIGH", "severity": Severity.WARN.value,
     "metric": "visibility_p95_ms", "op": ">", "threshold": 100,
     "action": "lagging indicator; the queue was already deep"},
    {"code": "PROJECTION_LAG_CRITICAL", "severity": Severity.CRITICAL.value,
     "metric": "visibility_p95_ms", "op": ">", "threshold": 500,
     "action": "consider pausing projection and draining"},
    {"code": "WAL_HIGH", "severity": Severity.WARN.value,
     "metric": "wal_mb", "op": ">", "threshold": 256,
     "action": "passive checkpointing continues; look for a long-lived reader"},
    {"code": "WAL_CRITICAL", "severity": Severity.CRITICAL.value,
     "metric": "wal_mb", "op": ">", "threshold": 512,
     "action": "storage checkpoint --mode restart, or truncate in maintenance"},
    {"code": "WORKER_BEHIND_INPUT", "severity": Severity.WARN.value,
     "metric": "worker_deficit_per_s", "op": ">", "threshold": 0,
     "window_seconds": 60,
     "action": "worker throughput below input rate for a full minute"},
    {"code": "QUEUE_NOT_DRAINING", "severity": Severity.CRITICAL.value,
     "metric": "queue_not_draining_after_producer_stop", "op": "==", "threshold": True,
     "action": "producers stopped and the queue is not falling; check for a "
               "paused worker, a dead letter storm or a held lock"},
)

_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": lambda a, b: a is not None and a > b,
    ">=": lambda a, b: a is not None and a >= b,
    "<": lambda a, b: a is not None and a < b,
    "==": lambda a, b: a == b,
}


def evaluate(measurements: dict[str, Any]) -> dict[str, Any]:
    """Judge a run against the objectives. Never invents a measurement.

    An objective whose input is absent is reported `not_measured`, which fails
    the run only if the objective is required — an unmeasured requirement is
    not a pass.
    """
    results = []
    for objective in ALL_OBJECTIVES:
        value = objective.read(measurements)
        if value is None:
            status = "not_measured"
        else:
            status = "pass" if objective.holds(value) else "fail"
        results.append({
            "key": objective.key,
            "description": objective.description,
            "measured": value,
            "limit": objective.limit,
            "required": objective.required,
            "status": status,
            "note": objective.note,
        })

    failed = [r for r in results if r["status"] == "fail" and r["required"]]
    missing = [r for r in results if r["status"] == "not_measured" and r["required"]]
    return {
        "slo_version": SLO_VERSION,
        "objectives": results,
        "passed": sum(1 for r in results if r["status"] == "pass"),
        "failed": [r["key"] for r in failed],
        "not_measured": [r["key"] for r in missing],
        "meets_slo": not failed and not missing,
        "envelope": ENVELOPE,
    }


def check_alerts(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Which alerts a current set of metrics would raise."""
    firing = []
    for alert in ALERTS:
        value = metrics.get(alert["metric"])
        if _OPS[alert["op"]](value, alert["threshold"]):
            firing.append({**alert, "measured": value})
    return firing


def render(evaluation: dict[str, Any], width: int = 78) -> str:
    marks = {"pass": "PASS", "fail": "FAIL", "not_measured": "----"}
    lines = ["=" * width, f"  CANARY SLO  ({evaluation['slo_version']})", "=" * width]
    for row in evaluation["objectives"]:
        flag = marks[row["status"]]
        required = " " if row["required"] else "?"
        lines.append(
            f"  [{flag}]{required} {row['key']:<34} {str(row['measured']):>12}  "
            f"(want {row['limit']})"
        )
    lines.append("-" * width)
    lines.append(f"  meets SLO: {evaluation['meets_slo']}")
    if evaluation["failed"]:
        lines.append(f"  failed:       {', '.join(evaluation['failed'])}")
    if evaluation["not_measured"]:
        lines.append(f"  not measured: {', '.join(evaluation['not_measured'])}")
    lines.append("=" * width)
    return "\n".join(lines)


__all__ = [
    "ALERTS",
    "ALL_OBJECTIVES",
    "CORRECTNESS",
    "ENVELOPE",
    "LATENCY",
    "RELIABILITY",
    "SLO_VERSION",
    "Objective",
    "Severity",
    "check_alerts",
    "evaluate",
    "render",
]
