# Bounded doctor snapshots — phase report

    branch      fix/v082-doctor-bounded-snapshots
    date        2026-08-08
    purpose     remove the reader hold that killed Run 8
    result      the worst single hold on a 1.7 GB database fell from
                2,147 ms to 99 ms, measured before and after on the same data

---

## 1. What was wrong

Run 8 stopped at 2.07 of 24 hours on `wal_dwelling_above_hard_limit`. A held
SQLite read snapshot prevents the write-ahead log from resetting, so everything
written during the hold accumulates with nothing able to reclaim it. In
isolation, identical write volume, changing only the reader:

    no reader                     peak     4.0 MB
    BEGIN, hold 0.35 s            peak 1,284.3 MB
    BEGIN, release immediately    peak     4.0 MB
    autocommit                    peak     4.1 MB

The holder in the real run was the doctor, and the evidence was in Run 8's own
log the whole time:

    21:42 - 23:02   doctor 0.5 - 4.9 s    ->  WAL grew 0 MB
    23:07 - 23:37   doctor 8.0 - 11.7 s   ->  WAL grew +30 to +43 MB, each run
    total           111 s of held read    ->  294 MB, 2.6 MB per second held

---

## 2. Where the time actually went

Profiled on a copy of Run 8's database — 1,718 MB, 519,143 events. The original
was not opened.

    quick doctor          192 ms total, worst single hold      64 ms   already fine
    deep doctor        80,321 ms total, worst single hold   5,728 ms
    incremental        19-28 s total,   worst single hold   4.7-6.1 s

**The incremental doctor was not incremental.**

    window     5,000 events   ->  19,354 ms
    window   400,000 events   ->  28,627 ms

Eighty times the window for forty-eight percent more time. The cost was never in
the window; it was in the checks that scan the whole database every pass — the
ones the docstring calls "cheap and global". On 1.7 GB,
`check_projection_consistency` held a snapshot for five to six seconds to return
zero rows.

With a real advancing cursor the picture sharpened further:

    check_shadow             2,147 ms   not windowed
    check_job_lifecycle      1,084 ms   not windowed
    check_dependencies         107 ms
    check_projection_consistency 24 ms  windowed, and fine

Windowing already worked where it was applied — 1,000 new rows cost 37 ms,
50,000 cost 655 ms, linear. The problem was the checks it was not applied to.

---

## 3. What changed

**`_chunked()`** — keyset scan by rowid. The ceiling is read once before the
first slice, so a scan never chases rows written while it runs. Slice size
adapts: starts at 500, halves above 100 ms, grows by half below 25 ms.
Asymmetric on purpose — overshooting the hold is the failure being prevented,
undershooting costs round trips.

**`_scan()`** — window and slice together. They do different jobs: the window is
how much a scan must look at, the slice is how long it may hold the log while
looking. Windowing alone is not enough, because with an empty cursor the window
is the whole table.

**Applied to** the shadow checks, projection consistency, event debt, orphan
jobs, and the outbox identity scan.

**Deep doctor keeps its whole-file pragma** and gains a stated contract instead.
An integrity check of half a file is not an integrity check, so it is not
sliced; it now warns — `INTEGRITY_AUDIT_WITH_ACTIVE_WRITERS` — when run against
a database that is still being written.

**Reader instrumentation** — `note_reader(id, source)`, a `reading()` context
manager that releases in `finally`, p50/p95/p99/max, split by source. Run 8
recorded `registered_readers = 0` for all 122 samples while a reader was
demonstrably pinning the log; the post-mortem had to infer from correlation what
one counter would have shown.

**Thresholds**, derived from the failure rather than chosen for comfort. At
2.6 MB of log per second of holding, 100 ms costs a quarter of a megabyte and a
second costs two and a half:

    target      100 ms
    warn        250 ms
    critical    500 ms
    fail      1,000 ms

---

## 4. Result

Same database, before and after:

                     before      after
    first scan     2,037 ms      97 ms    240 slices
    later scan     2,147 ms      99 ms    214 slices
    total          4,684 ms   6,261 ms

Total time rose 34% and that is the correct trade. The work did not get cheaper
— it got interruptible. A scan taking thirty seconds in three hundred
hundred-millisecond slices is safe; one taking ten seconds in a single slice is
not, and watching the total is what let this survive eight canary runs.

---

## 5. Defects introduced and caught during this phase

Recorded because the pattern matters more than the individual mistakes: each was
found by a measurement or a test, none by reading the code back.

**`LIMIT` does not bound input.** The first slicing attempt used
`rowid > last LIMIT 500` and still took 2,077 ms. A limit caps output, and a
predicate matching nothing never fills it, so SQLite walks every remaining row
to prove the absence. Fixed with a closed rowid interval.

**An empty slice used as a stop condition.** A check hunting for absence returns
nothing from almost every slice. Breaking on the first empty one reports "no
leak" correctly for the wrong reason and misses anything past it — in a check
whose severity is CRITICAL. Pinned by
`test_an_empty_slice_does_not_end_the_scan`.

**Lost tolerance for a missing table.** `_q` had always swallowed
`OperationalError` and returned empty: a check referencing a table this database
has not created is an absent capability, not damage. `_chunked` did not, so a
missing `projection_ledger` became `scan_error` and exit 3 — louder than the
CRITICAL the test was asserting.

**Windowing a check that must stay global.** `check_job_lifecycle` was moved
into the windowed set on the reasoning that a `projection_key` can only become
wrong when a row is written. True for that finding, false for the check, because
the same check also reports stale leases — and a lease goes stale by the clock.
`test_global_checks_run_even_with_an_empty_window` caught it within the hour.
Reverted; the hold is bounded by slicing instead of by looking at less.

**A cross-function name in the supervisor.** The per-sample reader metrics were
wired to `manager.reader_hold_percentiles()`, but `manager` is defined in
`run_recovery` and the sample is built in `run` — a `NameError` on the first
sample, which would have killed Run 9 in its first sixty seconds. Found by
checking the AST rather than by running it.

The deeper cause of that one is worth keeping: workers report their WAL manager
through a status file, but the doctor runs in the supervisor process on its own
connection, so its holds were invisible to every worker's manager. That is the
real reason Run 8 recorded zero registered readers.

**An always-true timestamp comparison.** `observed_at >= datetime('now', '-5
seconds')` — `observed_at` is stored ISO-8601 with a `T`, `datetime()` returns a
space. `T` sorts above a space, so every row written on the same date compared as
"just now" and the new warning fired permanently.

**A stress gate that could not reproduce the failure.** Three versions:
sequential doctor and writer, so nothing ever overlapped; an empty starting
database, where the same scan holds 24 ms; and a manager that was constructed
and never called, so the gate failed its own reclaim check for a reason
unrelated to the code under test.

---

## 6. Not done, and why

**Thresholds unchanged.** 64 MB soft, 128 MB hard, `observe_p95 <= 1.0 ms`. Run 8
failed under exactly these, so changing them now would destroy the ability to
show the root-cause fix worked. Re-derivation belongs in a separate document
after a green run — the generation rate is measured (~4,200 bytes per write,
1.0 GB/hour at 70 writes/s) and waiting on it.

**No claim about Run 9.** This phase removes the reader hold. Whether the log
has a finite lifecycle over twenty-four hours is what Run 9 is for, and nothing
here is durability evidence.
