from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .sqlite_utils import connect_sqlite
from typing import Any

from .models import ExecutionOutcome


@dataclass(frozen=True)
class CapabilityAssessment:
    capability: str
    successes: int
    failures: int
    estimated_success_rate: float
    lower_confidence_bound: float
    evidence_count: int


class SelfModel:
    """Evidence-based agent capability model.

    Only verifier-checked outcomes update the model. This prevents an agent
    from declaring itself competent based on its own prose.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = connect_sqlite(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS self_capabilities(
                tenant_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                trace_ids_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(tenant_id, agent_id, capability)
            )"""
        )
        self.conn.commit()

    def record(self, tenant_id: str, agent_id: str, capability: str, outcome: ExecutionOutcome) -> bool:
        if not outcome.verifier_passed:
            return False
        row = self.conn.execute(
            "SELECT * FROM self_capabilities WHERE tenant_id=? AND agent_id=? AND capability=?",
            (tenant_id, agent_id, capability),
        ).fetchone()
        successes = (row["successes"] if row else 0) + int(outcome.success)
        failures = (row["failures"] if row else 0) + int(not outcome.success)
        traces = json.loads(row["trace_ids_json"]) if row else []
        if outcome.trace_id not in traces:
            traces.append(outcome.trace_id)
        self.conn.execute(
            """INSERT INTO self_capabilities(tenant_id,agent_id,capability,successes,failures,trace_ids_json)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(tenant_id,agent_id,capability) DO UPDATE SET
               successes=excluded.successes, failures=excluded.failures, trace_ids_json=excluded.trace_ids_json""",
            (tenant_id, agent_id, capability, successes, failures, json.dumps(traces)),
        )
        self.conn.commit()
        return True

    def assess(self, tenant_id: str, agent_id: str, capability: str) -> CapabilityAssessment:
        row = self.conn.execute(
            "SELECT * FROM self_capabilities WHERE tenant_id=? AND agent_id=? AND capability=?",
            (tenant_id, agent_id, capability),
        ).fetchone()
        successes = int(row["successes"]) if row else 0
        failures = int(row["failures"]) if row else 0
        n = successes + failures
        rate = successes / n if n else 0.5
        lower = self._wilson_lower(successes, n) if n else 0.0
        return CapabilityAssessment(
            capability=capability,
            successes=successes,
            failures=failures,
            estimated_success_rate=round(rate, 6),
            lower_confidence_bound=round(lower, 6),
            evidence_count=n,
        )

    @staticmethod
    def _wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
        if n == 0:
            return 0.0
        phat = successes / n
        denominator = 1 + z * z / n
        centre = phat + z * z / (2 * n)
        margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
        return max(0.0, (centre - margin) / denominator)

    def should_delegate(self, tenant_id: str, agent_id: str, capability: str, risk_level: str) -> bool:
        assessment = self.assess(tenant_id, agent_id, capability)
        threshold = {"low": 0.35, "medium": 0.55, "high": 0.75, "critical": 0.90}.get(risk_level, 0.55)
        return assessment.evidence_count < 3 or assessment.lower_confidence_bound < threshold
