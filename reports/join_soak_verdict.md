# Join soak — verdict

    ran         2026-08-10 07:29 -> 2026-08-11 19:29, 36 hours
    cycles      6,705 per arm, 0 errored
    events      53.6 M projection jobs in the joined arm, 26.8 M in baseline
    model       none — no GPU, no API, no quota
    verdict     FAIL on criterion 1 of 4

The thresholds were written down on 2026-08-10, before any data existed, in
`join_soak_HANDOFF.md`. They are unchanged. A threshold moved after a failure
cannot be evidence of anything.

---

## 1. Scored

    criterion                                       measured            result
    1. p95 no worse by more than 0.10 ms            +0.196 ms           FAIL
       and both arms under the 1.0 ms SLO           0.480 / 0.676       (pass)
    2. max no worse than 1.5x baseline              26.3 vs 31.0 max    PASS
    3. WAL peak within 10% of baseline              0.4%                PASS
    4. zero dead-lettered, zero failed              0 and 0             PASS

Medians across 6,705 cycles.

## 2. What the failure actually is

    p50    0.150 -> 0.190 ms    +0.040   (+26%)
    mean   0.337 -> 0.464 ms    +0.126   (+37%)
    p95    0.480 -> 0.676 ms    +0.196   (+41%)

    cycles breaching the 1.0 ms SLO
      baseline     14 / 6,705    0.21%
      joined      155 / 6,705    2.31%     11x more often

The cost is one extra `projection_outbox` row per observation, and it is
consistent rather than pathological. Nothing degrades over 36 hours — it
*improves* slightly as the machine settles, and the WAL is flat to two decimal
places:

    quarter    base p95   join p95    base WAL   join WAL
    1             0.510      0.704      4.20 MB    4.21 MB
    2             0.502      0.700      4.20 MB    4.21 MB
    3             0.464      0.670      4.20 MB    4.21 MB
    4             0.465      0.633      4.20 MB    4.21 MB

Draining is sub-linear — 1.85x the time for 2x the jobs — so the worker handles
the second consumer better than proportionally. That part of the design works.

## 3. Why it failed, honestly

The bar was set below the intrinsic cost of the design it was measuring.

An extra outbox row costs about 0.2 ms at p95 on this machine. I chose 0.10 ms
as "no worse by more than", a round number picked for looking strict, without
deriving it from what the extra row actually costs. The design could not have
met that bar however well it was written.

That is a defect in how the criterion was set, and the correct response is
**not** to relax it. It is to ask whether the second outbox row needs to exist.

## 4. The question the failure raises

The outbox exists to make sure a projection is not lost: leases, retries,
dead-letter, exactly-once. Those guarantees are the right ones for
`cognitive_memory`, because a memory that goes missing is gone.

A label is not that. It is derived data, recomputable from an immutable event at
any later time, and idempotent by construction — miss one and the next pass
picks it up, with no loss and nothing to reconcile. Paying outbox prices for
something that cannot be lost is paying for insurance against an impossibility.

So the alternative is to enqueue nothing at write time and let the upgrader find
its own work:

    SELECT e.* FROM cognitive_events e
    LEFT JOIN hippocampus_labels h ON h.event_id = e.event_id
    WHERE h.event_id IS NULL

`observe()` then costs **exactly what it costs today** — byte-identical to the
path nine canary runs hardened — and criterion 1 passes by removing the cost
rather than by moving the bar. The label work moves entirely off the write path,
which is where Phase 1 said it had to go anyway.

What is given up: automatic retry and dead-letter accounting for labels. What
replaces it: a query that cannot miss anything, because "unlabelled" is a
property of the data rather than of a queue.

Measured next, on the same harness, same thresholds.

## 5. Not in dispute

Both arms sit far under the SLO the system is actually held to. 0.676 ms against
a 1.0 ms budget is not a crisis, and 2.31% of cycles breaching is not a broken
system. This is a failed criterion, not a failed feature — and the criterion is
being kept because the alternative is a habit of adjusting bars until things
pass, which is how the 25.34 ms fsync claim happened in the first place.
