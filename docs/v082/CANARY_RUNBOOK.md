# Canary runbook — `cognitive_memory` projection

**Version:** v0.8.2-rc1 · **Verdict it acts on:** CONDITIONAL GO, SQLite
single-node, `cognitive_memory` only, legacy retained as the rollback path.

Staging or a de-identified copy. Not production traffic.

---

## Before anything

- [ ] `doctor --deep` exits 0 on the target database
- [ ] `storage wal-status` exits 0
- [ ] the rollback has been *practised* on this database, not just read about
- [ ] a worker can be started and stopped by whoever is on call
- [ ] `reports/` is writable — `--repair` refuses to run without an audit

Rollback in full is one variable: `BIO_AGENT_PROJECTION_MODE=legacy`. See
`ROLLBACK_RUNBOOK.md`.

---

## Stage A — Shadow, minimum 24 hours

```bash
BIO_AGENT_PROJECTION_MODE=shadow
bio-agent-os --db <path> projection worker --forever
```

Both paths run from one canonical input. Legacy keeps producing the production
memory; the shadow projection lands in `shadow_memories`, a separate table, so
production recall cannot reach it by construction rather than by a filter.

### Watch, at least hourly

```bash
bio-agent-os --db <path> doctor --incremental
bio-agent-os --db <path> projection status
bio-agent-os --db <path> storage wal-status
```

| Signal | Where | Healthy |
|---|---|---|
| observations processed | `projection status` | rising |
| shadow match rate | shadow comparison | **100%** |
| queue depth | `projection status` | < 700 |
| p95 / p99 visibility | worker samples | < 100 ms / < 250 ms |
| `observe()` overhead | benchmark D | ≤ 0.50 ms absolute |
| WAL | `storage wal-status` | exit 0 or 1, never sustained 2 |
| doctor | `doctor --incremental` | exit 0 |
| dead letters | `projection status` | 0, or every one explained |
| worker restarts | supervisor | recovers each time |
| RSS | process list | flat over hours |

### Pass conditions — all of them

```
0 unexplained mismatches
0 duplicate projections
0 lost debts
0 tenant or workspace leakage
0 doctor FAIL or CRITICAL
queue depth does not grow without bound
WAL controlled, or alerting and recovering
```

Any failure: stop, do not proceed to Stage B, and write down what was seen
before changing anything.

---

## Stage B — Outbox canary by tenant

Only after Stage A is green for a full 24 hours.

There is no per-request routing, so the canary is a **tenant allow-list**.
Every tenant not on it stays on legacy.

```bash
BIO_AGENT_OUTBOX_TENANTS=internal-test,staging-tenant
```

### Steps, in order

| Step | Scope | Hold for |
|---|---|---|
| B1 | 1 internal tenant | 24 h |
| B2 | 5 tenants | 24 h |
| B3 | 25% of tenants | 48 h |
| B4 | 50% of tenants | 48 h |
| B5 | 100% of tenants | 1 week before anything is called stable |

Each step requires, before the next:

- [ ] `doctor --deep` exit 0 (not just `--incremental`)
- [ ] queue drained at least once with producers still running
- [ ] no new finding codes compared with the previous step
- [ ] WAL returned below the soft limit at least once
- [ ] the SLO evaluation passes — see below

### Per-tenant pause

A tenant can be taken off the canary without touching the others:

```bash
bio-agent-os --db <path> --tenant <tenant> projection pause --reason "..."
```

A global pause covers everything; a tenant pause covers only that tenant.

---

## The SLOs this is judged against

Declared in `bio_agent_os/cognitive/slo.py`, version-stamped, written before
the run. Absolute budgets first, ratios reported alongside — a percentage of a
sub-millisecond number cannot tell an operator whether it matters.

### Correctness — required, no tolerance

```
0 lost events            0 lost debts
0 duplicate projections  0 ledger inconsistencies
0 tenant leakage         0 unexplained shadow mismatch
0 doctor FAIL/CRITICAL   integrity_check = ok
```

### Reliability — required

```
queue drains after producers stop
0 stale leases after recovery
0 orphaned CLAIMED jobs
0 unexplained dead letters
WAL below the hard limit, or alerting with a recovery
```

### Latency — inside the envelope

```
legacy observe p95      <= 1.0 ms
shadow observe p95      <= 1.0 ms
shadow overhead p95     <= 0.50 ms absolute   (ratio reported, not enforced)
visibility p95          <= 100 ms
visibility p99          <= 250 ms
```

An objective whose input was not measured is reported `not_measured`, and an
unmeasured requirement fails the run. **A run cannot pass by not looking.**

---

## Operating envelope

```
SQLite single-node alpha, i5-12400F class hardware

  producers                4
  workers                  4        (8 costs 41% of total throughput)
  safe sustained input     390 events/s
  burst                    550 events/s for up to 60 s
  max healthy queue depth  1,100
  expected p95 visibility  < 100 ms
  storage                  3.1 KB per event, linear
```

**Size on the projector, never on the appender.** Append sustains roughly
three times what the projector drains, and anything sized on the append figure
produces a queue that grows for as long as input continues.

**Alert on queue depth, not on latency.** Latency is a lagging indicator of a
backlog that has already formed.

| Alert | Severity |
|---|---|
| queue depth > 700 | WARN |
| queue depth > 1,100 | CRITICAL |
| visibility p95 > 100 ms | WARN |
| visibility p95 > 500 ms | CRITICAL |
| WAL > 256 MB | WARN |
| WAL > 512 MB | CRITICAL |
| worker throughput below input for 60 s | WARN |
| queue not falling after producers stop | CRITICAL |

---

## Stop conditions

Stop the canary immediately and roll back on any of:

* a lost event or a lost debt
* a duplicate projection or a duplicate ledger row
* any tenant or workspace crossing
* `doctor` CRITICAL that is not a stale read snapshot (re-run it first)
* a queue that does not fall after producers stop
* a crash that does not recover on restart
* latency that keeps climbing while the queue is shallow

---

## What this canary is not

It is not a production rollout. It is not distributed anything. It does not
cover the four projection types with no builder. It does not carry over to
PostgreSQL, where the single-writer serialisation that shapes every throughput
figure here does not apply.

Nothing here should be described as production-distributed. It is one node,
one engine, one projection type, with the previous behaviour one environment
variable away.
