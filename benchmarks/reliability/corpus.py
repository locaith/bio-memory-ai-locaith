"""Load that is not one sentence repeated a hundred thousand times.

A benchmark whose every event carries the same string measures the SQLite page
cache, not the pipeline. This generator varies tenant, workspace, language,
domain and length, so page splits, index depth and the immune system's
redaction path all get exercised the way real traffic exercises them.

The point is *not* to judge domain quality — no retrieval accuracy is claimed
here. It is to make the write path see the shape of production input.

Deterministic by seed: the same seed produces the same corpus, so a rerun
after an optimisation compares like with like.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator

#: Ten tenants, ten workspaces each. Single-tenant load hides tenant-scoped
#: index behaviour and every tenancy bug the doctor checks for.
TENANTS = 10
WORKSPACES_PER_TENANT = 10

DOMAINS = (
    "vi", "en", "coding", "erp", "legal",
    "finance", "medical", "devops", "iot", "personal",
)

_TEMPLATES: dict[str, tuple[str, ...]] = {
    "vi": (
        "Khách hàng {n} phản hồi rằng quy trình duyệt đơn hàng đang chậm hơn cam kết ba ngày.",
        "Cuộc họp vận hành tuần {n} chốt lại: ưu tiên xử lý tồn kho trước khi mở thêm kênh bán.",
        "Ghi chú nội bộ {n}: đội kỹ thuật đề xuất tách dịch vụ thanh toán khỏi khối lõi.",
        "Báo cáo {n} cho thấy tỉ lệ huỷ đơn tăng ở nhóm khách mới, cần rà soát lại bước xác thực.",
    ),
    "en": (
        "Customer {n} reported that the approval queue stalls whenever an attachment exceeds ten megabytes.",
        "Weekly operations review {n} agreed to freeze the schema until the migration backlog clears.",
        "Internal note {n}: the support rota needs a second on-call engineer for the Asia window.",
        "Retro {n} concluded the incident was a configuration drift, not a code regression.",
    ),
    "coding": (
        "def resolve_{n}(conn): return conn.execute('SELECT 1').fetchone()  # placeholder until the real query lands",
        "Refactor note {n}: the retry decorator swallows CancelledError, which makes graceful shutdown hang.",
        "Stack trace {n}: sqlite3.OperationalError: database is locked, raised from projection commit under load.",
        "Patch {n} replaces the O(n^2) dedup with a set keyed on (file, line); measured 40x on the 10k fixture.",
    ),
    "erp": (
        "Purchase order PO-{n} awaiting three-way match; goods receipt posted, invoice missing.",
        "Cost centre CC-{n} exceeded its quarterly allocation by 4.2 percent after the freight revaluation.",
        "Master data change {n}: vendor payment terms moved from NET30 to NET45 effective next period.",
        "Inventory count {n} shows a variance of 12 units in the bonded warehouse; recount scheduled.",
    ),
    "legal": (
        "Clause {n} of the processing agreement requires deletion within thirty days of contract termination.",
        "Counsel note {n}: the indemnity cap is mutual, so the asymmetric drafting in schedule two is an error.",
        "Filing {n} was accepted; the opposition window closes ninety days from the publication date.",
        "Compliance review {n} flags cross-border transfer without a documented lawful basis.",
    ),
    "finance": (
        "Reconciliation {n}: bank statement shows 84,320,000 VND received against an expected 84,300,000 VND.",
        "Accrual {n} was reversed in the following period, producing a double credit in the trial balance.",
        "FX note {n}: unrealised loss of 1.8 percent on the USD payable after the quarter-end revaluation.",
        "Budget line {n} underspent; the variance is timing, not saving, and reverses next quarter.",
    ),
    "medical": (
        "Encounter note {n}: patient reports intermittent symptoms over six weeks; no acute findings on exam.",
        "Lab result {n} is within reference range; repeat in three months per the standing protocol.",
        "Care plan {n} updated after review; medication unchanged, follow-up moved to the nurse-led clinic.",
        "Triage note {n}: presentation is non-urgent; routed to the standard appointment queue.",
    ),
    "devops": (
        "Alert {n}: p99 latency crossed the 500ms objective for eleven minutes during the deploy window.",
        "Runbook {n}: drain the queue before restarting the worker, or in-flight leases expire mid-build.",
        "Capacity note {n}: WAL growth outpaced checkpointing under sustained write load; autocheckpoint tuned.",
        "Postmortem {n}: the rollback worked, but the alert fired nine minutes after user impact began.",
    ),
    "iot": (
        "Sensor {n} reported 27.4C and 61 percent humidity; the gateway buffered for 40 seconds during the outage.",
        "Device {n} firmware 2.14 drops its session after a DHCP renew; downgrade pending vendor fix.",
        "Telemetry batch {n} arrived out of order; timestamps are device-local and not NTP-synchronised.",
        "Gateway {n} queue depth peaked at 4,096 messages before the backhaul link recovered.",
    ),
    "personal": (
        "Contact record {n}: reachable on 0912-345-{n:03d} or at person{n}@example.invalid for scheduling.",
        "Onboarding note {n} for employee ID EMP-{n:05d}; bank details collected separately, never in free text.",
        "Reminder {n}: passport A{n:07d} expires next year; renewal appointment not yet booked.",
        "Household note {n}: delivery address is 12 Nguyen Trai, apartment {n}, code at the gate is not stored here.",
    ),
}

#: Padding that makes some events large without making them all large. Row
#: size drives page splits, and a uniform corpus never produces one.
_PADDING = (
    "",
    " Additional context was captured from the originating thread for traceability.",
    " Additional context was captured from the originating thread for traceability, "
    "including the prior decision, who approved it, and the constraint it was "
    "meant to satisfy at the time it was taken.",
)


@dataclass(frozen=True, slots=True)
class Observation:
    tenant_id: str
    workspace_id: str
    domain: str
    actor: str
    source: str
    content: str


def generate(count: int, *, seed: int = 20820805, start: int = 0) -> Iterator[Observation]:
    """`count` observations spread over every tenant, workspace and domain.

    `start` offsets the sequence so several producer processes can draw from
    one corpus without overlapping content.
    """
    rng = random.Random(seed + start)
    for i in range(start, start + count):
        tenant = f"tenant_{i % TENANTS:02d}"
        workspace = f"ws_{(i // TENANTS) % WORKSPACES_PER_TENANT:02d}"
        domain = DOMAINS[i % len(DOMAINS)]
        template = rng.choice(_TEMPLATES[domain])
        padding = _PADDING[rng.randrange(len(_PADDING))]
        yield Observation(
            tenant_id=tenant,
            workspace_id=f"{tenant}_{workspace}",
            domain=domain,
            actor=f"agent_{i % 7}",
            source=f"bench.{domain}",
            content=template.format(n=i) + padding,
        )


def distribution(count: int, *, seed: int = 20820805) -> dict[str, int]:
    """Domain histogram, so the report can state what was actually generated."""
    out: dict[str, int] = {d: 0 for d in DOMAINS}
    for obs in generate(count, seed=seed):
        out[obs.domain] += 1
    return out


__all__ = ["DOMAINS", "TENANTS", "WORKSPACES_PER_TENANT", "Observation", "distribution", "generate"]
