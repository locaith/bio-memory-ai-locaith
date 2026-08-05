# v0.8.2rc1 — release candidate

**Branch:** `feat/v082-reliability-kernel` · **Not a stable release.**

A release candidate for the reliability kernel. Legacy remains the default
write path, and remains the rollback path. Nothing here turns the projection
pipeline on for anybody.

---

## What this is

The v0.8.2 work closed the window between an event committing and the
projection it owes: a transactional outbox, a target-local ledger, a leased
worker, a replay engine, deterministic fault injection, shadow mode, and a
capability-aware doctor. The previous phase benchmarked it under load and
returned **CONDITIONAL GO** for `cognitive_memory` on SQLite single-node.

This candidate closes the three gaps that verdict named, adds incremental
scanning and the operational controls a canary needs, and packages it.

## What this is not

Not a cutover. Not production. Not distributed. Not PostgreSQL. Not the four
projection types that still have no builder. `BIO_AGENT_PROJECTION_MODE`
defaults to `legacy` and a test asserts that doing nothing gets you exactly
the behaviour that shipped in v0.6.1.

---

## Changes

### `fix(projection)` — one transaction on the target connection

The ledger row, the projection and the ledger's `target_id` were in two
transactions: the builder committed, then the worker committed again solely to
write `target_id`. A crash between them left a ledger row that said a
projection existed and could not say which one.

`put()` also committed *inside* its FTS branch, so on a SQLite without FTS5 a
projection was durable only by whatever the caller happened to do next.

The contract is now explicit: **a builder writes into the worker's open
transaction and does not commit.** Commits per job on the target connection:
2.00 → 1.00. Total across both connections: 3.02 → 2.02.

Verified by killing a real process at each of seven fault points and
recovering:

```
fault point                             proj ledger dup already_built
before_ledger_insert                       1      1   0             0
after_ledger_insert                        1      1   0             0
before_projection_write                    1      1   0             0
after_projection_write_before_commit       1      1   0             0
after_projection_commit                    1      1   0             1
before_outbox_complete                     1      1   0             1
after_outbox_complete                      1      1   0             0
```

`integrity_check` ok at all seven, zero ledger rows without a `target_id`, and
the full 25-case fault matrix green.

Throughput is unchanged as far as this machine can tell: three alternating
rounds gave 559.9 / 900.2 / 949.2 job/s before and 462.2 / 925.9 / 937.8
after — medians 2.9% apart with overlapping ranges, smaller than the 1.15×
run-to-run spread already measured for this stage. **The reason for the change
is the atomicity, not the commit count.**

### `feat(storage)` — observable WAL checkpointing

The soak measured the WAL at 500 MB after an hour — 46% of the database —
returning to zero only when the last connection closed.

`WALCheckpointManager` watches it and checkpoints on a schedule, between worker
cycles and never on the append path. Default mode is `PASSIVE`, which yields to
readers rather than waiting for them. `TRUNCATE` is never automatic and the CLI
requires `--maintenance` for it.

```
below soft limit (256 MB)   PASSIVE
above soft limit            PASSIVE, and a warning
above hard limit (512 MB)   RESTART if no reader is registered, else PASSIVE
```

`storage wal-status` and `storage checkpoint --mode ...`. Full detail in
`WAL_OPERATIONS.md`.

Reader age is reported as `None` — not `0` — when nothing has registered,
because SQLite has no API for it and a zero would read as "no reader is holding
anything back".

### `perf(doctor)` — the relationship is a row, not a substring

"Does a projection exist for this event" was asked as
`source_event_ids_json LIKE '%' || event_id || '%'` in three places. A leading
wildcard cannot use an index. `memory_source_events` makes it a row with the
primary key leading on `event_id`, written in the same transaction as the
memory, with `ON DELETE CASCADE`.

Query plans are checked at every size: `SEARCH ... USING INDEX`, never `SCAN`.

Cost, measured by alternating three rounds: append +2.6%, projection −9.6%,
both with overlapping ranges. About a tenth of projection throughput, at the
edge of what this machine resolves.

### `feat(doctor)` — incremental scans that cannot skip

A deep scan is 65.7 s at 366,000 events. `doctor --incremental` scans from a
stored cursor and runs every cheap global invariant in full every time —
schema, capabilities, stale leases, unexplained dead letters, dependency
cycles, shadow consistency, dangling references, `quick_check`.

Measured on one 100,000-event database:

| scan | seconds |
|---|---:|
| `--deep` | 13.359 |
| quick (4 checks) | 8.314 |
| `--incremental`, nothing new | **2.124** |
| `--incremental`, 500 new events | **2.695** |

6.3× cheaper than the audit, and cheaper than quick mode while running more
checks — the difference is `integrity_check`, which reads every page.

The cursor advances only after a completed scan, and only when nothing is
outstanding. A FAIL or CRITICAL holds it until the problem is gone. The doctor
version and a schema fingerprint invalidate it. The high-water mark is read
before the scan, so rows appended during it fall into the next window.

### `feat(projection)` — pause, resume, drain, and declared SLOs

Pause is a row in the database, read once at the top of a worker cycle and
never inside one: a claimed batch always finishes. Tenant-scoped, which is what
a tenant-allowlist canary needs. Drain overrides the pause deliberately.

Canary SLOs are declared in `slo.py` with a version stamp, **before** the run.
Absolute budgets first, ratios reported alongside: the previous phase set
shadow overhead at 10% and measured 99.4% of 0.30 ms, and that miss is not
retroactively excused — it stands in the benchmark report against the
threshold that was in force.

---

## Two defects found by this work, both older than it

**FTS rows could outlive their memory.** Rebuilding a projection then produced
a second index entry under one key, which SQLite reports as
`malformed inverted index for FTS5 table` — corruption, from a supported
operation, since the `COMPLETED_WITHOUT_PROJECTION` repair asks the worker to
rebuild. A trigger now deletes the FTS row with its memory, putting the cost on
the delete path rather than on every write.

**The doctor could report corruption that did not exist.** The integrity pragma
ran on the shared connection, and Python's `sqlite3` leaves a read transaction
open after a `SELECT` — so a connection that had read anything was pinned to an
old snapshot. With six connections to one file, a snapshot taken before another
connection rewrote the FTS index makes SQLite report a malformed index on a
healthy database. `SQLITE_INTEGRITY` is CRITICAL; that would have stopped a
canary for nothing. The pragma now runs on its own short-lived connection.

---

## Verification

See `reports/v082/rc1_verification.json` for the machine-readable record,
including the wheel's SHA-256 and the clean-install behaviour probe.

---

## Upgrading

Nothing to do. The schema migrations are additive — `memory_source_events`,
`memory_store_migrations`, `doctor_cursor`, `projection_control` — and the
link-table backfill runs once, bounded by a marker row.

`BIO_AGENT_PROJECTION_MODE` unset means legacy. An unrecognised value means
legacy. Rolling back to legacy keeps committed debt for a later replay rather
than deleting it.

---

## Next

1. Merge to `main` with legacy still the default.
2. Staging shadow canary, minimum 24 hours — `CANARY_RUNBOOK.md`.
3. Tenant-allowlist outbox canary, in the steps that runbook sets out.
4. Only then a conversation about `0.8.2` stable.

Before a full GO: decide the shadow-overhead budget, benchmark against
PostgreSQL, and run a 6- and 24-hour soak.
