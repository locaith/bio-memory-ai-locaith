# Projection failure and recovery matrix

**Date:** 2026-08-05
**Branch:** `feat/v082-reliability-kernel`
**Suite at completion:** 347 passed, 1 skipped (25 of them this matrix)

Every case kills at a **named point**, not after a sleep. A child process
signals the parent over a `multiprocessing.Event` the instant it reaches the
point, then calls `os._exit()` — no atexit handlers, no flush, no destructors.
A passing test therefore states where the process was, not merely that it died
somewhere.

All process-level cases use a **real SQLite file**. `:memory:` does not
outlive the process that made it, which is the thing under test.

The `spawn` start method is used throughout: it is the only one available on
Windows, and it catches import-time assumptions that `fork` would hide.

---

## Mechanism

`bio_agent_os/cognitive/fault_points.py` exposes fourteen named points. The
pipeline calls `fire()` at each; production cost is one `is None` check.
Nothing happens unless a test installs a hook, and `armed()` reports whether
one is — asserted false in the production-path test.

```
BEFORE_EVENT_TRANSACTION      AFTER_EVENT_INSERT
AFTER_OUTBOX_INSERT           AFTER_EVENT_COMMIT
BEFORE_CLAIM                  AFTER_CLAIM
AFTER_EVENT_LOAD              BEFORE_LEDGER_INSERT
AFTER_LEDGER_INSERT           BEFORE_PROJECTION_WRITE
AFTER_PROJECTION_WRITE_BEFORE_COMMIT
AFTER_PROJECTION_COMMIT       BEFORE_OUTBOX_COMPLETE
AFTER_OUTBOX_COMPLETE
```

---

## A. Event and outbox — one transaction

| # | Fault point | State before | State after crash | Recovery | Result |
|---|---|---|---|---|---|
| F01 | before event transaction | empty | 0 events, 0 jobs, 0 projections | none needed | **PASS** |
| F02 | after event insert, before outbox insert | empty | rolled back: 0 events, 0 jobs | none needed | **PASS** |
| F03 | after outbox insert, before shared commit | empty | rolled back: 0 events, 0 jobs | none needed | **PASS** |
| F04 · *process* | after shared commit | empty | 1 event, 1 pending job, 0 projections | fresh worker | **PASS** — projection built, job COMPLETED |

F02 and F03 are the outbox's reason for existing: an event may not become
durable without the record of what it owes.

## B. Claim and lease

| # | Fault point | State after crash | Recovery | Result |
|---|---|---|---|---|
| F05 · *process* | after claim | job IN_PROGRESS, lease held, 0 projections | lease valid → no one else may claim; expired → exactly one reclaims, attempts=2 | **PASS** |
| F06 · *process* | two processes race | — | exactly one winner, one projection, one ledger row | **PASS** |
| F07 · *process* | restart with stale lease | job IN_PROGRESS | first worker completes, second claims nothing | **PASS** |
| F08 | lease expiry at the boundary | — | `elapsed >= lease` expires | **PASS** |

F08 guards the Windows clock-resolution bug found earlier: the clock ticks at
roughly 15 ms, so a claim and a check inside one tick give an elapsed of
exactly 0.0, and `>` meant a zero-length lease never expired.

## C. Projection transaction

| # | Fault point | State after crash | Recovery | Result |
|---|---|---|---|---|
| F09 | before projection write | 0 projections, **0 ledger**, job PENDING | retry after backoff | **PASS** |
| F10 · *process* | after projection insert, before commit | 0 projections, 0 ledger | ordinary retry | **PASS** |
| F11 | after ledger insert, before projection | 0 ledger, 0 projections | — | **PASS** |
| F12 · *process* | **after projection commit, before outbox complete** | 1 projection, 1 ledger, job IN_PROGRESS | reclaim → `already_built` → COMPLETED | **PASS** — no duplicate |
| F13 | same job processed ten times | — | 1 projection, 1 ledger, COMPLETED | **PASS** |

F12 is the case the whole design exists for, and it is the one that proves the
ledger works: the projection is durable, the job is not marked done, and the
retry recognises prior work instead of repeating it.

## D. Target-local ledger across the six stores

| # | Store | Result |
|---|---|---|
| F14 | cognitive_memory | **PASS** — the worker's `projection_conn` is asserted to *be* the memory store's connection |
| F15 | self_model_update | **PASS (gap exposed)** — dead-letters with "no builder registered"; **0** ledger rows |
| F16 | prospective_memory | **PASS (gap exposed)** — same |
| F17 | context_block | **PASS** — held as PENDING_DEPENDENCY, job stays PENDING |
| F18 | checkpoint_reference | **PASS** — dead-letters on retry, **no substitute projection invented** |

F15 and F16 are deliberately written to expose a gap rather than mock past it.
Those two stores have no builder, so their ledger *cannot* be target-local —
there is no target transaction to share. A job for one dead-letters with the
reason stated. This is recorded as a known gap, not a passing feature.

## E. Locks, failure and determinism

| # | Condition | Result |
|---|---|---|
| F19 · *process* | another process holds a real write lock | **PASS** — contention observed on a live connection, then the worker completes normally; integrity ok |
| F20 | builder fails to `max_attempts` | **PASS** — FAILED → retry → DEAD_LETTER, with `attempts` and `last_error` preserved, 0 ledger rows |
| F21 | parent dead-lettered | **PASS** — child becomes BLOCKED, not retried forever |
| F22 | non-deterministic builder | **PASS** — second build refused by the ledger; the first result is not overwritten |

Plus: **tenant isolation under crash** — two tenants, a worker killed at
`AFTER_PROJECTION_COMMIT`, recovery verified to keep each projection with its
own tenant and every ledger row pointing at an event of the same tenant.

---

## Bugs found by this matrix

### 1. `AFTER_PROJECTION_WRITE_BEFORE_COMMIT` was unreachable

The point existed in the enum and was never fired. `SQLiteMemoryStore.put()`
performs the INSERT and the COMMIT in one call, so there is no instruction
between them for a fault point to occupy — F10 was silently testing nothing,
and the first run of it "failed" by finding a committed projection.

Rather than assert around it, the child now uses a builder that performs the
insert on the same connection and stops before committing. That reproduces the
exact intermediate state, so the question can actually be asked.

**Consequence for the architecture:** the atomicity claim holds — ledger insert
and `put()` share one connection and one transaction — but it is only
observable at the boundaries. Anything wanting a fault *inside* `put()` would
need `put()` to expose a non-committing variant. Recorded in KNOWN_GAPS.

### 2. Backoff makes a naive retry loop a no-op

F09's first version cleared the fault and called the worker again, and nothing
happened: the failed job's `available_at` had been pushed into the future by
exponential backoff, so it was not claimable. The behaviour is correct; the
test was wrong to expect an immediate retry. Tests now advance `available_at`
explicitly rather than sleeping through the window.

---

## Invariants checked after every case

Read from a **freshly opened connection** on the closed file, so no cache can
flatter the result:

```
event count            outbox status          ledger count
outbox count           attempt count          projection count
lease owner            projection key         projection version
lease expiry           tenant id              source event id
PRAGMA integrity_check
```

Held in every case:

- no committed raw event lost
- no committed outbox debt lost
- no duplicate projection
- no ledger row without the projection it vouches for
- no cross-tenant projection or ledger reference
- never COMPLETED without the target projection existing
- `checkpoint_reference` never rebuilt against a different version
- no silent failure — every terminal state carries its reason
- `PRAGMA integrity_check` returns `ok`

---

## Coverage limits, stated plainly

- **Two of the six stores have no builder**, so F15/F16 prove only that their
  absence is reported honestly, not that their ledger is target-local.
- `context_block` and `checkpoint_reference` are tested at the dependency and
  refusal boundaries; neither has a builder either, so no context block or
  checkpoint projection is actually produced anywhere in this matrix.
- No case runs across two machines, and none runs for longer than a few
  seconds. This is single-node, short-duration crash recovery.
- The lock case (F19) proves contention is real and recovery works after it; it
  does not measure how the worker behaves under sustained lock pressure. That
  belongs to the reliability benchmark.
