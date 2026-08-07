# Staging shadow canary — Run 8

    result                 NO-GO
    classification         defect discovery run
    duration               2.07 h of a requested 24 h
    durability evidence    no
    defect evidence        yes
    stopped because        wal_dwelling_above_hard_limit

    commit                 2d102ab13c3b006e817717349374dafd77a74de7
    runtime tree           0c9f9b0291cb1f69c45e892a96b833cd0b41816c
    harness sha            7b1426ad021dc29a
    config sha             d7046c1c520a85d4
    gate                   scripts/run8_gate.py — PASS, all 7 suites, clean tree
    started                2026-08-07 21:37:01
    stopped                2026-08-07 23:41:28
    scope                  locaith / locaith-intelligence-os (faef976762e7)

**2.07 hours is not durability evidence.** Neither is the write volume below.
Per section 18: stop as soon as the blocker is demonstrated, do not run on for a
prettier number. Run 9's clock starts at zero.

---

## 1. What Run 8 established

Valid for the 2.07 hours it covers, recorded separately from the durability
question because the run did not fail on any of them.

    writes / events                    519,143
    shadow comparisons                 344,734
    mismatches                               0
    dead letter                              0
    tenant / workspace leakage               0
    final queue drained          yes, 0.2 s
    sqlite integrity after                  ok
    deep doctor findings                    30

**Identity, the second blocker from Run 7, held for the whole run.**

    scope_fingerprint_cli == scope_fingerprint_hook   every one of 122 samples
    SCOPE_CONFIGURATION_MISMATCH                       0
    false "retrieval empty" from scope wiring          0

**The reclaim campaign added after Run 7 does run.**

    truncate attempts        68
    truncate succeeded       48
    truncate busy            20   (29% blocked by a reader)
    truncate ineffective      0

Run 7 made zero reclaim attempts below the hard limit because that branch did
not exist. So the mechanism works. It is not sufficient, which is a different
statement and the subject of the rest of this report.

---

## 2. Why it stopped

    time_above_soft_seconds   2,019.2
    time_above_hard_seconds     634.2   -> fatal at 600.0

The trigger is one added the night before, in response to Run 7: a bare ceiling
check cannot distinguish a log that spikes and is reclaimed from a log that sits
above its limit with nothing able to bring it down. It fired on the second case,
which is what it was built for.

WAL over the run, sampled every ~10 minutes:

    0.02h    8 MB      1.02h   44 MB      1.68h   83 MB
    0.35h   25 MB      1.35h   50 MB      1.85h   92 MB
    0.68h   43 MB      1.52h   22 MB      2.02h  101 MB

Climbing, with reclaim visibly working against it and losing.

---

## 3. Root cause, measured

Three hypotheses were tested and two were killed. All measurements are on fresh
databases in scratch space; nothing here touched Run 1–8 evidence.

### 3.1 Rejected — "each write generates too much WAL"

    observe + remember, autocheckpoint at running config
      3,000 events      4,219 B/write    1.02 WAL frames
     30,000 events      4,202 B/write    1.02 WAL frames
     51,000 events      4,211 B/write    1.02 WAL frames

Flat with database size, so not an index-locality problem. And ~1 page per
durable multi-table write is close to the floor — there is nothing to cut:

    event append only, no projection debt    2,763 B
    + outbox debt                            2,788 B   (+25)
    full observe()                           2,755 B   (-33)
    + remember()                             2,821 B   (+66)
    + shadow mode                            2,810 B   (-11)

Removing the outbox, remember, or shadow mode entirely would save under 70 bytes
out of 2,800. The layers this project spent months building are not the cost.

*(The 2,800 and 4,200 figures differ because `wal_autocheckpoint=1000` collapses
duplicate frames; 4,200 is the number for the configuration actually running.)*

### 3.2 Rejected — "SQLite can cap the file for us"

`PRAGMA journal_size_limit` is unset (-1) and setting it changes nothing,
because it only takes effect when a checkpoint completes and resets the log —
which is exactly what a reader prevents:

    with readers, journal_size_limit = -1      peak 1,287 MB
    with readers, journal_size_limit = 32 MB   peak 1,285 MB
    with readers, journal_size_limit = 64 MB   peak 1,288 MB

### 3.3 Confirmed — a held read snapshot is the whole problem

Identical write volume (9,800 writes), PASSIVE checkpoints only, changing
nothing but how the readers hold their transaction:

    no reader at all                          peak     4.0 MB
    BEGIN, hold 0.35 s, rollback              peak 1,284.3 MB
    BEGIN, release immediately                peak     4.0 MB
    autocommit, no BEGIN                      peak     4.1 MB

**A 321× difference, caused entirely by whether a read transaction stays open
while writes continue.** Two readers each holding 0.35 s at overlapping times
leave no instant with zero open snapshots, so the log can never reset and grows
without bound.

### 3.4 Who is holding it, in the real run

The cognitive layer itself is mostly clean — 25 `fetchall()` against 5 lazy
cursor iterations, three of which are `PRAGMA` calls at init. The holder is the
**doctor**, and the evidence was in Run 8's own log the whole time:

    window            doctor duration    WAL growth during it
    21:42 - 23:02     0.5 - 4.9 s        0 MB
    23:07 - 23:37     8.0 - 11.7 s       +30 to +43 MB, every run

    total doctor time holding a connection      111 s
    total WAL growth inside those windows       294 MB
                                                2.6 MB per second of doctor

The incremental doctor opens a read connection and holds it for the duration of
its scan. That duration **grows with the database**: 0.5 s at the start of the
run, 11.7 s two hours in. Every five minutes it pins the log open for longer
than the time before, and the log grows by whatever is written meanwhile.

This is the same shape as five earlier findings on this project: **the
diagnostic is damaging the system it diagnoses.** Fixes 4 and 5 (`14bbbd5`,
`9c85fee`) already made the doctor faster by dropping a whole-file pragma. They
treated the constant factor. The scan is still O(data) and still holds one
snapshot for all of it.

---

## 4. What this means for the WAL work done so far

The state machine, hysteresis, cooldown and bounded TRUNCATE are correct and
should stay. They are also, now that the cause is known, **treatment of a
symptom**. No checkpoint policy can reclaim a log that a reader is holding open,
and the reader in question is ours.

The thresholds are a separate matter and should not be quietly moved. For the
record, so the decision is made on numbers rather than habit:

    measured generation at Run 8's real rate    1.0 GB/hour  (70 writes/s)
    hard limit 128 MB reached from zero in      457 s
    Run 8 reclaim cadence                       one success per 155 s
    WAL accumulated between successes           ~43 MB

The 64 / 128 MB thresholds were inherited from an older soak; the module
docstring still cites it. They have never been derived from a measured
generation rate. **Whether to re-derive them is Tuấn Anh's call, not mine** —
changing a pass condition after a failed run is exactly the move that must never
be made quietly.

---

## 5. Recommended for Run 9

In priority order. Nothing here is implemented yet.

1. **Bound how long the doctor holds a snapshot.** Scan in chunks, closing the
   read transaction between them, so the log gets a reset window every few
   hundred milliseconds instead of every eight to twelve seconds. This is the
   fix that addresses the cause.
2. **Register the doctor's read with `note_reader` / `release_reader`.** The
   manager already has this and Run 8 recorded `registered_readers = 0` for all
   122 samples while a reader demonstrably existed. With it wired, the manager
   can skip a doomed TRUNCATE instead of spending its budget on it, and the
   post-mortem can see the hold directly instead of inferring it.
3. **Add an SLO on reader hold time**, so the next regression of this kind is
   caught by the harness rather than by an overnight investigation.
4. **Only then** revisit thresholds, with the generation rate in hand.

---

## 6. Corrections to earlier reports

**Run 7 report, "end-to-end retrieval evidence".** That section cites recall
returning the Techcombank memory at confidence 0.98 as evidence retrieval works.
Measured since: the same memory is returned at rank 1 for *"quy trình đăng ký
tên miền website"* and at rank 3 for `"aaaa bbbb cccc"`. Scoring does respond to
the query — a WAL question scores 3.270 against a 1.624 baseline — but there is
**no relevance floor**, so an unmatched query still returns the highest-confidence
memories rather than nothing. The Run 7 line proves the scope wiring is correct.
It does not prove retrieval quality and must not be quoted as if it did.

**My own instrumentation.** Run 8 sampled `truncate_bytes_reclaimed`,
`truncate_skipped_cooldown`, `truncate_skipped_reader` and `state_transitions`
as absent — the counters existed in `metrics` but were never carried into
`WALStatus`, so every minute recorded a hole where "how much did reclaim
actually return" belonged. Fixed after the run; the post-mortem above had to
work around it.

---

## 7. Retained

Nothing deleted. `.staging/v082-canary-run{1..8}` hold every database, WAL, log,
metric and report. Disk after Run 8: see `doctor` output; Run 8 added ~2 GB.
