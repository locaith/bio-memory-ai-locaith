# Diagnosis and reconciliation

**Date:** 2026-08-05 · **Branch:** `feat/v082-reliability-kernel`

The pipeline can recover from a crash. This is how you find out whether it
currently needs to.

---

## Commands

```bash
bio-agent-os --db <path> doctor                      # quick, read-only
bio-agent-os --db <path> doctor --deep               # full check set
bio-agent-os --db <path> doctor --deep --json        # for monitoring
bio-agent-os --db <path> projection status
bio-agent-os --db <path> projection reconcile                    # dry run
bio-agent-os --db <path> projection reconcile --repair
bio-agent-os --db <path> projection reconcile --tenant <tenant>
bio-agent-os --db <path> projection reconcile --event-id <id>
bio-agent-os --db <path> projection replay [--event-id <id>] [--apply]
bio-agent-os --db <path> projection worker --once|--forever
```

**Doctor never writes.** **Reconcile defaults to dry run.** Neither is an
accident of the interface; both are the point of it.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | no FAIL or CRITICAL |
| 1 | at least one FAIL |
| 2 | at least one CRITICAL |
| 3 | **the scan itself did not finish** |

3 outranks the findings deliberately. A doctor that crashed must never be
mistaken for a clean bill of health.

---

## Findings

Every finding carries `code`, `severity`, `entity_type`, `entity_id`,
`tenant_id`, `workspace_id`, `description`, `evidence`, `repairable`,
`suggested_action`. Severity is `PASS`, `INFO`, `WARN`, `FAIL` or `CRITICAL`.

The `code` is stable and is what a runbook should key off. A doctor that only
reports "something is wrong" is not useful.

### Capability awareness

`cognitive_memory` is checked fully. The other four types have no builder and
are reported `UNSUPPORTED_CAPABILITY` at **INFO** — a missing capability, not
damage. They are never hidden so the summary can say healthy, and never given
a repair action.

```
supported projection types  : cognitive_memory
unsupported                 : 4 (self_model_update, prospective_memory,
                                 context_block, checkpoint_reference)
```

### Legacy is not corruption

The legacy write path produces events with no projection debt. That is what
the primary path looks like today, so it reports as `INFO`
(`EVENT_PROJECTED_WITHOUT_DEBT`) and leaves the exit code at 0.

---

## What deep mode checks

| Area | Codes |
|---|---|
| SQLite | `SQLITE_INTEGRITY`, `SQLITE_FOREIGN_KEY`, `SQLITE_JOURNAL_MODE` |
| Schema | `SCHEMA_MISSING_TABLE`, `SCHEMA_MISSING_INDEX`, `SCHEMA_OPTIONAL_TABLE_ABSENT` |
| Capability | `UNSUPPORTED_CAPABILITY`, `CAPABILITY_SUMMARY` |
| Outbox | `OUTBOX_PENDING`, `OUTBOX_DEAD_LETTER`, `OUTBOX_WITHOUT_EVENT` |
| Job lifecycle | `JOB_STALE_LEASE`, `JOB_CLAIMED_WITHOUT_OWNER`, `JOB_ATTEMPTS_EXCEEDED`, `DEAD_LETTER_WITHOUT_REASON`, `INVALID_PROJECTION_KEY`, `INVALID_PROJECTION_VERSION` |
| Events | `DUPLICATE_EVENT_ID`, `EVENT_MISSING_CHECKSUM`, `EVENT_PROJECTED_WITHOUT_DEBT` |
| Ledger | `LEDGER_WITHOUT_EVENT`, `LEDGER_WITHOUT_PROJECTION`, `LEDGER_TENANT_MISMATCH` |
| Projection | `COMPLETED_WITHOUT_PROJECTION`, `SKIPPED_WITH_LEDGER` |
| Dependency | `DEPENDENCY_CYCLE`, `CHILD_PENDING_WITH_DEAD_PARENT`, `CHILD_COMPLETED_BEFORE_PARENT` |
| Shadow | `SHADOW_MISMATCH`, `SHADOW_COMPARISON_MISSING`, `SHADOW_TENANT_MISMATCH`, `SHADOW_LEAKED_INTO_PRODUCTION`, `SHADOW_CONSISTENT` |
| Dangling | `CONTEXT_BLOCK_DANGLING`, `CHECKPOINT_DANGLING` — both `UNSUPPORTED_REPAIR` |

---

## Repair policy

Repairs are an **allow-list**. Anything absent is refused with the finding's
own suggested action. A deny-list would silently automate whatever a future
check happens to add.

### Automatic

| Finding | Action | Risk |
|---|---|---|
| `JOB_STALE_LEASE` | release the lease, return to pending | safe |
| `JOB_CLAIMED_WITHOUT_OWNER` | return to pending | safe |
| `JOB_ATTEMPTS_EXCEEDED` | move to dead-letter, keep history | safe |
| `COMPLETED_WITHOUT_PROJECTION` | reset to pending so the worker rebuilds | moderate |
| `CHILD_PENDING_WITH_DEAD_PARENT` | dead-letter the child as blocked | safe |
| `SHADOW_COMPARISON_MISSING` | re-run the comparison (deterministic) | safe |

### Never automatic

Deleting a raw event · editing a tenant · changing a security label · guessing
provenance · repointing a checkpoint at another memory version · breaking a
dependency cycle · building an unsupported projection · deleting an orphaned
projection before a human has looked.

### Preconditions and idempotency

Each action's precondition is **re-checked at apply time**. State moves between
the scan and the repair, and a worker may have fixed the problem itself; an
action whose world changed is skipped as `conflict` rather than applied to a
situation nobody diagnosed. That is also what makes a second `--repair` a
no-op: the precondition has stopped being true.

### Audit

Every `--repair` writes `reports/reconciliation_<timestamp>.{json,md}` with
findings before and after, actions chosen and refused, row counts on both
sides, exit codes on both sides, and the operator. `--repair` without an audit
is not a supported path.

---

## A bug this work found in itself

Doctor marked 50 findings `repairable` while reconcile planned **zero**
actions. `SHADOW_COMPARISON_MISSING` advertised a repair that did not exist,
and `OUTBOX_PENDING` claimed `--repair` would drain a queue that only a worker
drains.

A finding that tells an operator the system will handle it, and then does not,
is worse than one that says nothing. Both were corrected — one gained its
recipe, the other stopped claiming to be repairable — and
`test_every_repairable_finding_has_a_recipe` now holds the invariant.

---

## Measured

On a 50-observation shadow database:

| | |
|---|---:|
| checks run (deep) | 22 |
| scan time | 0.003 s |
| findings | 59 |
| repairable | 50 |
| manual review | 0 |

Dry run changed **0 rows**, verified by reading counts from a freshly opened
connection on the closed file. A second `--repair` applied **0** actions.

---

## Limits

- Scale was measured at tens to hundreds of rows, not the 10,000 events /
  10,000 projections / 1,000 shadow records the plan asks for. Scan time is
  sub-millisecond at this size and the queries are indexed, but that is an
  expectation, not a measurement.
- `DUPLICATE_EVENT_ID` cannot fire on SQLite: `event_id` is the primary key, so
  the storage layer refuses it outright. The check remains as defence in depth
  for a backend with a looser constraint.
- Incremental scan is not implemented. Quick and deep and tenant-scoped are.
- Peak memory was not measured.
