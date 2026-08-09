# Run 9, and closing the canary programme

    result                 NO-GO
    classification         defect discovery run
    duration               2.72 h of a requested 24 h
    stopped because        wal_dwelling_above_hard_limit
    programme decision     STOP — the canary has delivered its findings; what
                           remains is architectural and belongs to a deployment
                           shape that is not shipped

---

## 1. Run 9

    commit        b8758a9  (fix/v082-doctor-bounded-snapshots)
    started       2026-08-08 17:20:53
    stopped       2026-08-08 20:04:01
    gate          run9_gate.py PASS — 10 suites, Locaith OS, torture verdict
    torture gate  30 min PASS — max hold 145 ms, 54/55 truncates, 0 s above hard

### The doctor fix worked, completely

    doctor snapshot hold      Run 8              Run 9
      maximum                 8,000-11,700 ms    322 ms
      p50 / p95 / p99         not measured       37 / 166 / 217 ms
      samples                 none               399
      slices per pass         1                  ~300
    WAL growth during doctor  294 MB             173 MB

The measurement that Run 8 could not make is the whole point: Run 8's
post-mortem had to infer the doctor's role from correlation, and Run 9 shows it
directly. Bounded snapshots do what they were built to do.

### And the run still died

    time_above_hard_seconds   602.8   (limit 600)
    truncate attempts         90
    truncate succeeded        65
    truncate busy             25
    truncate_skipped_reader   0
    reclaimed                 6,727 MB

Twenty-five blocked reclaims, and `truncate_skipped_reader = 0` — so the blocker
was never a *registered* reader. The doctor is registered and holds 322 ms. The
processes that are not registered are the two producers and three workers, and
they are what is left holding snapshots.

---

## 2. Why the programme stops here

### What nine runs established

    Run 7 + 8 + 9 combined
      shadow comparisons            ~1,850,000
      mismatches                             0
      duplicate projections                  0
      tenant / workspace leakage             0
      write failures (Run 7, 9)              0
      recovery drills recovered            2/2
      sqlite integrity after every run      ok

Nine real defects were found and fixed, each by measurement rather than by
reading code:

    1  candidate_pool joined on an expression no index could serve  33,426 ms -> 48.7 ms
    2  WAL manager was constructed and never called
    3  PASSIVE and RESTART never return the file; only TRUNCATE does
    4  incremental doctor ran a whole-file pragma every scan
    5  quick doctor did the same
    6  scheduled TRUNCATE had no time budget — 23,236 ms holding every writer
    7  doctor emitted one finding per row — 234,745 lines, a 111 MB report
    8  cross-entrypoint scope mismatch — thirty memories invisible for 18 hours
    9  doctor held one read snapshot for its whole scan — 8-12 s every 5 minutes

That is the canary's job and it did it. The write path can be trusted.

### What it did not establish, and why it should not keep trying

Twenty-four hours under **two producers, three workers and a supervisor writing
one SQLite file at 50 observations per second.**

The failure is no longer a defect. SQLite resets its write-ahead log only in a
moment when no reader holds a snapshot, and five concurrent processes almost
never leave one. The same cause explains the latency: one process doing the full
shadow path on a 1.7 GB database measures **2.02 ms** at p95, and the canary
reports **32-45 ms** — thirty to forty milliseconds of queueing behind one write
lock.

Both are the writer topology, not the code.

    Run 5   1.55 h        Run 8   2.07 h
    Run 6   2.89 h        Run 9   2.72 h
    Run 7   6.48 h

Five runs, four fixes, no convergence in duration — while every individual fix
is real and verified. Each round removes one layer and reveals the next: the
manager never ran, then a 23-second checkpoint, then a 12-second doctor, now
worker reads. There is no reason to expect the next round to be the last, and
each costs a day.

### And the shape being tested is not the shape being shipped

The deployment that exists today is the Claude Code memory hook: **one process,
33 memories, 0.3 MB.** Nothing runs five concurrent writers against this
database. The canary has been proving durability for a configuration that has no
users, while the claim the product is actually judged on — that memory behaves
biologically — was never on its path at all.

---

## 3. What replaces it

Not nothing. Three things, in order.

**Join the two stacks.** `bio_agent_os/cognitive/` — what Claude Code runs — has
no model in it. The hippocampus, the consolidation, the dream cycle live in
`bio_agent_os/memory/` and are not reachable from the shipped path. The socket
already exists: `ProjectionType` has five members and only one does anything.
Plan in the platform repo at `docs/architecture/hippocampus-join-plan.md`.

**Re-confirm the claim.** LoCoMo has been run fifteen times against three
systems and four models. The result worth defending is temporal reasoning:

    gemma4:12b     naive-rag 0.416   bio-memory 0.603    +45%
    qwen2.5:7b     naive-rag 0.215   bio-memory 0.525   +144%

Time is exactly what a biological memory should be better at, and it is where
this one wins. Confirm it still holds on today's code, with the embedding
backend recorded this time — the first re-run attempt started in hash fallback,
and the June reports do not record enough configuration to tell whether they
did the same.

**Then a canary for the shape that ships.** One process, hook-shaped load.
Cheap, quick, and it answers the question a user would actually ask.

---

## 4. Not deleted, not changed

`.staging/v082-canary-run{1..9}` retain every database, WAL, log, metric and
report. Thresholds are untouched: `wal_soft = 64 MB`, `wal_hard = 128 MB`,
`observe_p95 = 1.0 ms`. Run 8 and Run 9 both failed under exactly these, and a
threshold moved after a failure cannot be evidence of anything.

A correction belongs here. On 2026-08-07 I reported the machine's durable-commit
floor as p95 = 25.34 ms and argued from it that `observe_p95_ms = 1.0` measures
the disk rather than the code. Re-measured six times on 2026-08-08: 0.98, 0.98,
1.03, 1.25, 1.35, 1.67 ms. The floor is about 1 ms. The legacy write path alone
measures 0.22 ms at p95 — under the threshold, comfortably. The original
instruction to leave the threshold alone was right and the argument against it
was mine and wrong. Recorded in full in `reports/observe_latency_model.md` on
branch `fix/v082-observe-slo-from-measurement`.
