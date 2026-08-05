"""Driving shadow mode: run both paths, drain the queue, compare, report.

Kept apart from `shadow.py` so the data structures stay usable without the
orchestration, and apart from the facade so nothing here can slow an
`observe()` down.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import MemoryType
from .projection_capability import CAPABILITIES, unsupported_types
from .reconciliation_worker import ReconciliationWorker
from .shadow import (
    COGNITIVE_MEMORY,
    CanonicalObservation,
    Comparison,
    ComparisonStatus,
    ShadowMemoryBuilder,
    ShadowReport,
    compare,
    output_hash,
)


def shadow_worker(memory_os: Any, **kwargs: Any) -> ReconciliationWorker:
    """A worker that writes shadow projections instead of production ones.

    The ledger still lives on the memory connection, which is also where the
    shadow table lives — so the ledger row and the shadow row commit together,
    the same guarantee the production path has.
    """
    return ReconciliationWorker(
        memory_os.events.conn,
        projection_conn=memory_os.memories.conn,
        outbox=memory_os.events.outbox,
        builders={COGNITIVE_MEMORY: ShadowMemoryBuilder(memory_os.shadow_memories)},
        **kwargs,
    )


def legacy_projection(memory_os: Any, event_id: str) -> dict[str, Any] | None:
    """The production memory a legacy `remember()` produced for this event.

    Joins the `memory_source_events` link table rather than matching the JSON
    column with a leading-wildcard LIKE. The LIKE could not use an index, so
    each call scanned every memory — and this is called once per event, which
    made comparing 10,000 shadow observations cost 62.5 s.
    """
    row = memory_os.memories.conn.execute(
        "SELECT m.* FROM cognitive_memories m "
        "JOIN memory_source_events s "
        "  ON s.memory_id = m.memory_id AND s.version = m.version "
        "WHERE s.event_id = ? ORDER BY m.version DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "memory_id": row["memory_id"],
        "tenant_id": row["tenant_id"],
        "workspace_id": row["workspace_id"],
        "memory_type": row["memory_type"],
        "content": row["content"],
        "confidence": float(row["confidence"]),
        "epistemic_status": row["epistemic_status"],
        "security_label": row["security_label"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "source_event_id": event_id,
    }


def shadow_projection(memory_os: Any, event_id: str) -> dict[str, Any] | None:
    rows = memory_os.shadow_memories.by_event(event_id)
    if not rows:
        return None
    row = rows[-1]
    return {
        "shadow_projection_key": row["shadow_projection_key"],
        "tenant_id": row["tenant_id"],
        "workspace_id": row["workspace_id"],
        "memory_type": row["memory_type"],
        "content": row["content"],
        "confidence": float(row["confidence"]),
        "epistemic_status": row["epistemic_status"],
        "security_label": row["security_label"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "source_event_id": row["source_event_id"],
        "canonical_input_hash": row["canonical_input_hash"],
        "output_hash": row["output_hash"],
    }


def compare_event(memory_os: Any, event_id: str) -> Comparison:
    legacy = legacy_projection(memory_os, event_id)
    shadow = shadow_projection(memory_os, event_id)
    result = compare(
        event_id=event_id,
        legacy=legacy,
        shadow=shadow,
        canonical_input_hash=(shadow or {}).get("canonical_input_hash"),
    )
    if shadow is not None:
        memory_os.shadow_memories.set_comparison(shadow["shadow_projection_key"], result.status)
    return result


def check_determinism(memory_os: Any, event_id: str, runs: int = 10) -> tuple[int, int]:
    """Rebuild the canonical input and its projection `runs` times.

    Returns the number of distinct input hashes and output hashes. Anything
    other than (1, 1) means the pipeline is not reproducible, and a comparator
    that tolerated it would be measuring noise.
    """
    from .shadow import projected_fields

    event = memory_os.events.get(event_id, _tenant_of(memory_os, event_id))
    inputs, outputs = set(), set()
    for _ in range(runs):
        canonical = CanonicalObservation.from_event(event)
        inputs.add(canonical.input_hash())
        outputs.add(output_hash(projected_fields(canonical)))
    return len(inputs), len(outputs)


def _tenant_of(memory_os: Any, event_id: str) -> str:
    row = memory_os.events.conn.execute(
        "SELECT tenant_id FROM cognitive_events WHERE event_id=?", (event_id,)
    ).fetchone()
    return row["tenant_id"] if row else ""


def run_shadow_comparison(
    memory_os: Any,
    event_ids: list[str],
    *,
    worker: ReconciliationWorker | None = None,
    batch_size: int = 100,
    determinism_sample: int = 25,
) -> ShadowReport:
    """Drain the shadow queue, compare every event, and report.

    Synchronous by design: this is the integration path. The runtime path
    leaves the queue to an asynchronous worker and never blocks `observe()`.
    """
    report = ShadowReport(observations=len(event_ids))
    report.unsupported = {
        t: CAPABILITIES[t].reason or "no builder" for t in unsupported_types()
    }

    worker = worker or shadow_worker(memory_os, worker_id="shadow-1", lease_seconds=0)
    started = time.perf_counter()
    while True:
        before = worker.metrics.claimed
        worker.run_once(batch_size=batch_size)
        if worker.metrics.claimed == before:
            break
    elapsed_ms = (time.perf_counter() - started) * 1000
    if worker.metrics.completed:
        per_job = elapsed_ms / worker.metrics.completed
        report.shadow_latency_ms = [per_job] * worker.metrics.completed

    for event_id in event_ids:
        result = compare_event(memory_os, event_id)
        report.comparisons.append(result)

    report.legacy_projections = sum(
        1 for e in event_ids if legacy_projection(memory_os, e) is not None
    )
    report.shadow_projections = memory_os.shadow_memories.count()

    for event_id in event_ids[:determinism_sample]:
        n_inputs, n_outputs = check_determinism(memory_os, event_id)
        if (n_inputs, n_outputs) != (1, 1):
            report.non_deterministic.append(event_id)
            report.comparisons.append(
                Comparison(event_id, ComparisonStatus.NON_DETERMINISTIC_OUTPUT.value,
                           detail=f"{n_inputs} input hashes, {n_outputs} output hashes")
            )
    return report


def write_report(report: ShadowReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "shadow_comparison.json"
    md_path = reports_dir / "shadow_comparison.md"
    json_path.write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(
        "# Shadow comparison — cognitive_memory\n\n```\n"
        + report.render()
        + "\n```\n\nRaw results: `shadow_comparison.json`\n",
        encoding="utf-8",
    )
    return json_path, md_path


__all__ = [
    "check_determinism",
    "compare_event",
    "legacy_projection",
    "run_shadow_comparison",
    "shadow_projection",
    "shadow_worker",
    "write_report",
]
