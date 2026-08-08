# Observe latency — what the number is actually made of

    measured    2026-08-08, while canary Run 9 was running
    verdict     do NOT change `observe_p95_ms = 1.0`
    reason      the argument for changing it was built on a measurement
                that does not reproduce

---

## The correction

On 2026-08-07 I measured a bare SQLite durable commit at **p95 = 25.34 ms** and
told Tuấn Anh, twice, that `observe_p95_ms = 1.0` therefore "measures the disk,
not the code" and "can never be green on this hardware". That claim is
withdrawn.

It came from a single 400-sample run taken while the machine was also running
Playwright, ffmpeg and a canary. Repeated six times today, the same measurement
gives:

    400 samples    p95  1.35 ms
    400 samples    p95  1.25 ms
    400 samples    p95  0.98 ms
    2,000 samples  p95  0.98 ms
    2,000 samples  p95  1.03 ms
    2,000 samples  p95  1.67 ms

The floor is around **1 ms**, not 25. The threshold sits at the floor, not
twenty-five times below it.

---

## What the layers actually cost

Single process, 1,500–2,000 samples per layer, measured while Run 9 ran:

    layer                                     p50      p95      p99      max
    bare SQLite, synchronous=FULL            0.60     1.67     8.12    110.4
    bare SQLite, synchronous=NORMAL          0.01     0.03     0.07     12.7
    observe() only, legacy, fresh db         0.14     0.22     0.97    100.8
    observe()+remember(), shadow, fresh db   0.59     1.50    13.11    220.9
    observe()+remember(), shadow, 1.7 GB db  0.88     2.02    40.95    386.9

Three things follow, none of which match what I said yesterday.

**The legacy path passes the SLO comfortably.** 0.22 ms p95 against a 1.0 ms
limit. The threshold is not unreachable; the production write path clears it
with room to spare.

**Database size is nearly free.** Growing from empty to 1.7 GB costs +0.52 ms at
p95. The B-tree depth is not the problem.

**Shadow mode is the larger single cost, and it is still small.** +1.28 ms at
p95 on a fresh database — the second projection, its ledger row and its
comparison.

---

## So where do the canary's 32–45 ms come from?

Not from the disk, not from scale, not from shadow. One process doing the full
shadow path on a 1.7 GB database reaches **2.02 ms** at p95. The canary reports
**32–45 ms** for the same operation.

The difference is what the canary adds and this measurement does not: **two
producers, three workers and a supervisor writing the same SQLite database at
once.** SQLite serialises writers, so under five-way contention a write waits
for the writes ahead of it. That is queueing, and queueing is what the extra
thirty to forty milliseconds is.

This is worth stating precisely because it changes what the red column means. It
has never meant "the hardware cannot do this". It means "five processes are
contending for one write lock", which is a real property of the deployment being
tested and arguably something an SLO *should* surface.

---

## Recommendation

**Leave `observe_p95_ms = 1.0` alone.** Tuấn Anh's original instruction was
right. My argument for changing it rested on a number that does not reproduce,
and changing a pass condition on that basis would have been exactly the move
this project forbids.

If the contention itself is worth reducing — and 30–40 ms of queueing at 50
writes/second is worth a look — the lever is the writer topology, not the
threshold: fewer concurrent writers, batched commits, or a single writer process
fed by a queue. That is a design question for after Run 9, with numbers, not a
threshold to be moved before it.

---

## Method

`scripts/measure_observe_floor.py`, run as:

    python scripts/measure_observe_floor.py --samples 2000 --label "canary running"

Repeat on an idle machine and compare. A floor measured once, on a busy box, is
how this went wrong the first time.
