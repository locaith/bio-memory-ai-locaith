# Staging shadow canary — Run 7

    result                 NO-GO
    classification         defect discovery run
    duration               6.48 h of a requested 24 h
    durability evidence    no
    defect evidence        yes
    stopped because        wal_above_hard_limit

**6.48 hours is not 24-hour stability evidence and must not be quoted as such.**
Neither is the write volume below. A run that ends on a fatal SLO breach
demonstrates that the defects it found are real; it demonstrates nothing about
what happens in hour seven.

    commit                 193085ebdaa16b139182f132459c103220d4ab1f
    runtime tree           528796527c25a6b6392741acde1378b6f2c02df2
    tag                    v0.8.2rc1
    started                2026-08-06 23:50:53
    stopped                2026-08-07 06:19:56
    configuration          2 producers + 3 workers, 50 obs/s flat, no ramp
    database at exit       5,423,149,056 bytes

---

## What Run 7 did establish

These are correctness and resilience results, valid for the 6.48 hours they
cover. They are recorded separately from the durability question on purpose:
the run did not fail on any of them.

    writes                             1,040,550
    write failures                             0
    lock errors                                0
    shadow comparisons                 1,040,090
    mismatches                                 0
    duplicate projections                      0
    tenant / workspace leakage                 0
    unplanned producer deaths                  0
    unplanned worker deaths                    0
    planned recovery drills recovered        2/2
    final queue drained                 yes, 0.4 s
    dead letter                                0
    sqlite integrity after                    ok
    max observe latency                  3,098 ms

The two recovery drills were `worker_restart` at 02:51:31 and
`kill_lease_holder` at 05:53:33. Both recovered without operator action and
without losing a job.

### The two fixes carried in from Run 6 held

    fix 6 — bounded scheduled TRUNCATE
      Run 6   1 write failure in 465,738; observe max 23,457 ms and 32,774 ms
      Run 7   0 write failures in 1,040,550; observe max 3,098 ms

    fix 7 — capped per-row findings in the doctor
      Run 6   deep doctor 234,756 findings, report 111 MB
      Run 7   deep doctor 30 findings, report 0.01 MB
              incremental doctor 7–398 findings (was 6,500–13,400)

Run 6 died on `legacy_observe_failed` at 2.89 h. Run 7 passed that mark and ran
to 6.48 h at 2.2× the write volume with no write failure at all, so fix 6 is
demonstrated rather than assumed.

---

## Why it stopped

    06:14:15   wal 220.6 MB   state critical   last checkpoint PASSIVE, 176 frames
    06:14:57   wal 612.1 MB   state critical   last checkpoint TRUNCATE, busy
    06:14:57   FATAL wal_above_hard_limit (supervisor SLO: 512 MB)

391 MB of write-ahead log in 42 seconds, against a background write rate of
~98 observations/second. The excursion is the trigger; the condition that made
it fatal had been building for six hours.

    soft limit (manager)      64 MB
    hard limit (manager)     128 MB
    fatal limit (supervisor) 512 MB
    wal median               118 MB
    samples above soft       81 %  (309 of 382)
    minimum observed           7 MB
    maximum observed         612 MB

### Root cause

`PRAGMA wal_checkpoint(PASSIVE)` copies frames back into the database and
leaves the file at its high-water mark. Only TRUNCATE returns space to the
filesystem. Under the Run 7 policy, TRUNCATE was reachable only above the
**hard** limit, so between 64 MB and 128 MB the manager had no action that
could reduce the file — the soft limit was not a threshold that was crossed
often, it was a threshold the manager's own action could not satisfy.

This was foreseeable and was in fact foreseen. The Run 6 analysis states, in
the commit message for `193085e`, that the soft limit is unreachable by
PASSIVE, and then chooses the minimal fix on the reasoning that "the hard limit
is not a runaway". Run 7 disproves that reasoning. Recorded here rather than
quietly corrected, because the reasoning was mine and the run cost a day.

### What is *not* claimed

The mechanism of the final 391 MB excursion in 42 seconds has not been
measured. The plausible account — a reader holding a snapshot so the log cannot
reset, while writes append at full rate — fits the arithmetic and fits the
`busy=True` on the final TRUNCATE, but no instrumentation confirms it. Run 8
adds reader registration and per-sample WAL state precisely so that the next
occurrence is measured instead of inferred. **Hypothesis, not root cause.**

---

## Second defect found after the run

Not a Run 7 SLO breach — found while investigating why memory recall returned
nothing useful — but it blocks Run 8 and belongs in this record.

### Finding: cross-entrypoint scope configuration mismatch

Not a retrieval bug. Not a full-text-search bug. Not a ranking bug. The first
diagnosis said ranking or Vietnamese tokenisation; that diagnosis was wrong.

    CLI identity              tenant=locaith   workspace=locaith-intelligence-os
    Claude Code hook identity tenant=local     workspace=<project path>

The hook was installed 2026-08-07 01:12. From that minute it wrote into a
partition named after a filesystem path and read back from the same one, while
30 memories written by the CLI sat one partition away. Three unrelated queries
returned the same four rows whose entire content was the name of the hook that
wrote them.

Tenant and workspace isolation worked correctly throughout. It hid exactly what
it is designed to hide. A correct isolation boundary and a broken index are
indistinguishable from inside a single query, which is why the mistaken
diagnosis was easy to reach and why `scope doctor` now exists to make the
distinction from one level up.

After aligning the scope, through the same hook binary Claude Code invokes:

    Techcombank account memory        confidence 0.98
    ARCHILAB real pricing memory      confidence 0.95
    quotation correction memory       confidence 0.90

That is end-to-end retrieval evidence for this database and these queries. It
is not a retrieval benchmark and must not be quoted as one.

---

## Blockers cleared before Run 8

    A. WAL lifecycle          three-state machine, hysteresis, cooldown,
                              bounded TRUNCATE from the soft limit,
                              busy never counted as success
                              15 regression tests, each measuring file bytes

    B. Identity contract      single resolver, documented precedence,
                              no silent path scoping, scope fingerprint,
                              scope status / doctor / migrate --dry-run
                              12 regression tests including negative isolation

    suite                     578 passed, 1 skipped (was 551)

Nothing from Runs 1–7 was deleted: databases, WAL, logs, metrics and reports
are retained in `.staging/v082-canary-run{1..7}`.
