# Reliability benchmark — cognitive_memory projection pipeline

**Date:** 2026-08-05 · **Branch:** `feat/v082-reliability-kernel`
**Baseline commit:** `65bef58` · **Raw results:** `reports/v082/`

The fault matrix proved the pipeline recovers. Shadow mode proved it produces
the same answer. Neither ran it under load. This does.

---

## What this measured, and what it did not

**Measured.** Append throughput and latency; projection throughput and
per-stage latency; both together; the cost of shadow mode on the response
path; crash, kill, lock and stale-lease recovery under a loaded queue; a
deliberate distribution of failures; the doctor before and after every run;
and a long-running soak.

**Not measured.** PostgreSQL. Any projection type other than
`cognitive_memory` — four of five have no builder. Retrieval quality: the
corpus spans ten domains to vary row shape and index depth, not to judge
domain accuracy. Multi-node anything.

---

## 1. Environment

| | |
|---|---|
| OS | Windows 10 build 26200 (Windows 11 Pro) |
| CPU | 12th Gen Intel Core i5-12400F — 6 cores, 12 threads |
| RAM | 31.82 GB |
| Python | 3.11.9 CPython |
| SQLite | 3.45.1 (driver 2.6.0) |
| Filesystem | NTFS |
| Journal mode | WAL, `synchronous=NORMAL`, `busy_timeout=30000`, `wal_autocheckpoint=1000` |
| Page size | 4096 |
| Process start method | `spawn` (Windows) |
| Measurement tooling | stdlib only — `ctypes`/`resource`; psutil is not installed and was not added |

Recorded per run in `reports/v082/environment.json` and inside every result
file. Two numbers from different machines or different pragmas are not
comparable and are not compared here.

### Corpus

10 tenants × 10 workspaces = 100 workspaces. Ten domains in equal share —
Vietnamese, English, coding, ERP, legal, finance, medical, DevOps, IoT, and
simulated personal data — with three padding lengths so row size varies and
page splits actually happen. Deterministic by seed; `start` offsets keep
producer processes from drawing the same content.

At 10,000 events: exactly 1,000 per domain, >900 distinct contents per 1,000,
more than 50 distinct row lengths.

### Method

Processes, not threads: each producer and worker is a separate OS process with
its own `MemoryOS` and therefore its own six connections. At 4p+4w that is 48
connections to one file, which is the contention this is trying to find.

Latency is sampled **per operation**, never by dividing a wall clock. Each
projection records five timestamps — outbox row created, claimed, build
started, build finished, outbox completed — written to a per-worker JSONL and
merged afterwards. Percentiles are exact up to 250,000 samples and report
themselves as `exact`; beyond that they come from log-spaced buckets and say
so, with the ±2% bucket resolution attached.

The instrumented worker is a **subclass** of the production one. Nothing in
`bio_agent_os/` knows the benchmark exists.

### One measurement was broken, and its results are withdrawn

`rss_bytes()` called `GetProcessMemoryInfo` through ctypes without setting
`argtypes`/`restype`. The process handle was truncated to 32 bits, the call
failed, and the function returned **0** — which reads as "no memory used"
rather than "measurement broken".

Every `peak_rss_mb` figure in workloads A through F is therefore `0.0` and
means nothing. Those numbers are not quoted anywhere in this report. The bug
is fixed, a test asserts the reading is non-zero on a platform where it can be
read, and the soak (section 11c) is the only run whose memory figures come
from the working implementation. Where an external figure is quoted — the
195 MB during the 100,000-event doctor scan — it came from the OS process list,
not from this function, and is labelled as such.

A measurement that fails silently to a plausible value is worse than one that
raises, and this is the second time that shape of bug has appeared in this
work: the first was a doctor finding that advertised a repair which did not
exist.

---

## 2. Workload A — append only

10,000 events, no worker running. Producers commit the event and its
projection debt in one transaction.

| producers | throughput | wall | append p50 | p95 | p99 | lock errors | failures |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,260 ev/s | 4.42 s | 0.194 ms | 0.356 ms | 12.33 ms | 0 | 0 |
| 2 | **2,594 ev/s** | 3.85 s | 0.222 ms | 1.957 ms | 15.67 ms | 0 | 0 |
| 4 | 2,322 ev/s | 4.31 s | 0.224 ms | 2.298 ms | 21.24 ms | 0 | 0 |
| 8 | 2,322 ev/s | 4.31 s | 0.250 ms | 3.558 ms | 30.35 ms | 0 | 0 |

**The append path does not scale with producers.** Throughput is flat from one
producer onward; SQLite serialises writers, so adding processes moves the cost
into the tail rather than into throughput. p50 barely moves (0.19 → 0.25 ms)
while p99 grows 2.5× (12 → 30 ms) — the queueing shows up where queueing always
shows up.

**Zero lock errors at every level.** The 30-second `busy_timeout` absorbs the
contention; nobody sees an error, they just wait.

Outbox enqueue matched event append exactly: one outbox row per event, one
commit per event (profile below).

---

## 3. Workload B — projection only

The queue is preloaded with 10,000 jobs, then workers drain it. No appends
compete.

| workers | throughput | drain | build p95 | completion gap p95 | total build CPU | lock errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,098 job/s | 9.11 s | 2.003 ms | 0.762 ms | 5.32 s | 0 |
| 2 | **1,106 job/s** | 9.04 s | 2.548 ms | 1.013 ms | 7.86 s | 0 |
| 4 | 982 job/s | 10.18 s | 1.504 ms | 1.016 ms | 12.74 s | 0 |
| 8 | 957 job/s | 10.45 s | 1.502 ms | 1.029 ms | 18.61 s | 0 |

**More workers is slower.** 1→8 workers costs 13% of throughput. The
"total build CPU" column is the same 10,000 builds measured from inside the
workers: it grows 3.5× while the work does not change. That is contention,
paid inside each build, not queueing outside it.

Every run: 10,000 claimed, 10,000 completed, 0 failed, 0 already_built, 0
dead-lettered. Doctor clean before and after.

> **Read the queue-wait column with care.** In this workload every job was
> enqueued *before* any worker started, so `queue_wait` p95 of 9.5–10.6 s is
> the age of a preloaded backlog, not pipeline latency. End-to-end visibility
> is only meaningful in workload C, where events arrive while workers run.

---

## 4. Workload C — mixed live

Producers and workers at the same time. This is the configuration an operator
actually runs, and the only place end-to-end visibility latency means
anything.

| config | produce | project | total | p50 | p95 | p99 | peak queue | drain after stop | drained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1p+1w | 1,091/s | 756/s | 1,847/s | 4,456 ms | 4,876 ms | 4,908 ms | 4,725 | 4.07 s | yes |
| 2p+2w | 943/s | 730/s | 1,673/s | 3,945 ms | 4,716 ms | 4,777 ms | 3,648 | 3.10 s | yes |
| **4p+4w** | 1,164/s | 754/s | **1,918/s** | 4,059 ms | 5,091 ms | 5,728 ms | 4,720 | 4.67 s | yes |
| 4p+8w | 567/s | 566/s | 1,133/s | **906 ms** | **2,489 ms** | 5,230 ms | **2,033** | **0.003 s** | yes |
| 8p+4w | 1,152/s | 702/s | 1,853/s | 4,360 ms | 5,559 ms | 5,675 ms | 6,041 | 5.57 s | yes |

Zero lock errors in every configuration. The queue drained to zero after the
producers stopped in every configuration. Doctor exit 0 after every one.

Three things this says:

**The producer outruns the projector by roughly 1.5×.** Append sustains
~1,100–1,160/s while projection sustains ~700–760/s in the same run. The queue
therefore grows for as long as input continues, and p95 visibility is the depth
of that backlog rather than a property of the pipeline. Every latency figure
above except 4p+8w is a *saturated-queue* measurement.

**4p+8w is the only balanced configuration, and it is the slowest overall.**
Producers and workers land on the same number (567 vs 566/s) because eight
workers take enough of the single write lock to throttle the producers into
step. Total throughput drops 41% against 4p+4w — but the queue stays shallow
(2,033), p50 falls to 906 ms and the queue drains in 3 ms. That is the honest
trade: this configuration buys latency with throughput.

**Beyond 4 workers, adding workers subtracts throughput.** 4p+4w is the
saturation point on this machine.

---

## 5. Workload D — shadow mode cost

Same corpus twice: once with the shadow queue off, once on. Both runs call
`observe()` then `remember()`, so the legacy production memory exists in both.

| | legacy | shadow | overhead |
|---|---:|---:|---:|
| `observe()` p50 | 0.1494 ms | 0.3274 ms | **+119.1%** |
| `observe()` p95 | 0.2977 ms | 0.5937 ms | **+99.4%** |
| `observe()` mean | 0.2405 ms | 0.7338 ms | **+205.2%** |
| `remember()` p50 | 0.2516 ms | 0.3408 ms | +35.5% |

**Shadow mode roughly doubles `observe()` latency**, and by mean it triples it.
The proposed threshold was ≤10% at p95; the measured figure is 99.4%, so this
**fails that threshold by an order of magnitude**.

In absolute terms the cost is +0.18 ms at p50 and +0.30 ms at p95 — a third of
a millisecond. Whether that matters depends on the caller, and this report does
not decide that. It records that the threshold as written is not met and that
the threshold was written against a percentage of a very small number.

**Correctness, on the same run:**

| | |
|---|---:|
| observations compared | 10,000 |
| `MATCH` | **10,000 (100%)** |
| mismatches | 0 |
| shadow rows visible in production tables | **0** |
| shadow rows returned by `recall()` | **0** |
| shadow drain | 7.20 s at 1,388 job/s |

Isolation is by table, not by filter: the shadow projection lives in
`shadow_memories`, so production recall cannot reach it by construction.

Two caveats on this workload's own numbers. The shadow drain's end-to-end
figures (min 7.2 s) are the preloaded-queue artifact again — all 10,000 jobs
existed before the drain began. And the comparison pass took **62.5 s for
10,000 events**, 6.25 ms each, because `shadow_runner.legacy_projection()`
looks a memory up with `source_event_ids_json LIKE '%id%'`, which scans the
whole table. That is comparison tooling, not the runtime path, but it is the
same defect described in section 9 and it is not fixed.

---

## 6. Workload E — recovery under load

5,201 events appended across the scenarios; **5,201 present at the end; 0
lost.** Final queue: 5,201 completed, 0 pending, 0 in-progress, 0 dead-letter.

| # | scenario | result |
|---|---|---|
| 1 | worker killed mid-drain (`TerminateProcess`, no cleanup) after 1,023 completions | 54 jobs left `in_progress` holding leases |
| 2 | workers restarted | **2,814 jobs recovered in 24.1 s**, queue reached zero |
| 3 | write lock held 3 s, probed twice | 800 ms probe: blocked 0.95 s then **1 lock error**, as designed. Runtime's own 30 s timeout: blocked 2.08 s, **0 errors**, succeeded |
| 4 | stale lease planted (locked 600 s ago) | reclaimed in **0.28 s** |
| 5 | same job enqueued 3 times | **0 duplicate jobs**, 1 ledger row |
| 6 | crash at `BEFORE_OUTBOX_COMPLETE` — projection committed, outbox not | crashed as designed (exit 70); on retry **1 job took the `already_built` path**; queue reached zero |
| 7 | full runtime restart | 5,201 events readable, `integrity_check` = **ok**, **0** duplicate ledger rows, **0** duplicate projections |

Scenario 6 is the one the ledger exists for, and it is the only place
`already_built` fires. It fired.

Scenario 2 also produced an operational lesson the first run got wrong: the
restarted workers originally used an idle timeout **shorter than the lease**,
so they exited while the dead worker's claims were still inside their lease
window and recovered **0 of 9** jobs. A worker that gives up before
`lease_seconds` cannot reclaim anything. The harness now idles for longer than
the lease, and the runbook should say so.

---

## 7. Workload F — deliberate failure distribution

10,000 events, faults assigned deterministically by a hash of the event id.

| bucket | planned | actual |
|---|---:|---:|
| normal | 80.0% | 80.04% |
| transient failure | 8.0% | 7.69% |
| stale lease | 4.0% | 4.23% |
| dependency wait | 3.0% | 3.00% |
| skipped | 2.0% | 1.91% |
| permanent failure | 2.0% | 2.02% |
| duplicate enqueue | 1.0% | 1.11% |

**Terminal states — not all COMPLETED, which is the point:**

| state | count |
|---|---:|
| completed | 9,607 |
| dead-letter | 502 |
| skipped | 191 |
| pending | **0** |
| in-progress | **0** |

**Every dead letter is accounted for:**

| reason | count | classification |
|---|---:|---|
| injected permanent failure | 202 | expected |
| `no builder registered for context_block` | 300 | expected — unsupported capability |
| anything else | **0** | — |

Attempts behaved exactly as specified: dead-lettered jobs reached
`max_attempts = 5` (mean 3.18); completed jobs averaged 1.21 attempts with a
maximum of 5, i.e. transient failures recovered on retry rather than being
retried forever. 1,594 transient and 1,010 permanent failures were injected
across attempts; 191 builder skips.

423 ghost claims were planted with a lease an hour old. **0 remained.**
111 duplicate enqueue attempts produced **0 duplicate jobs.**
285 dependency holds were observed — the child projection genuinely waited on
its parent rather than being attempted and failed.

Doctor after this run exits **1**, on `OUTBOX_DEAD_LETTER`. That is correct:
there are 502 dead letters and the doctor should say so. Exit 0 after a run
that deliberately dead-letters would mean the check does not work.

---

## 8. Write-path profile — where the time goes

Single process, SQL traced. The **counts** are exact; the **times** are
inflated by the trace callback (it fires per statement and calls into Python),
so speed figures come from the untraced runs above.

**Append: 1 commit per event.**

```
BEGIN   3,000     INSERT  6,000     COMMIT  3,000
cognitive_events 3,000   projection_outbox 3,000
```

Two inserts, one transaction, one commit. There is nothing to remove.

**Projection: 3.02 commits per job, 25.6 statements per job.**

| connection | commits per job |
|---|---:|
| queue (`events.conn`) | 1.02 |
| projection (`memories.conn`) | **2.00** |
| **total** | **3.02** |

`projection_ledger` is touched **4 times per job**: the already-built check, the
insert, the `target_id` update, and the post-build verification read.

The builder itself accounts for **66.3%** of per-job time. The other third is
the worker's bookkeeping.

**The removable commit.** One of the two commits on the projection connection
exists only to fill in `target_id` after the builder has already committed:

```python
self._record_ledger(job, target_id=None)      # INSERT, uncommitted
result = builder.build(...)                    # put() commits -> both durable
if result.target_id:
    self.projection_conn.execute("UPDATE projection_ledger SET target_id=?...")
    self.projection_conn.commit()              # <- second commit
```

Removing it means either giving the builder a `commit=False` path or a
two-phase builder protocol. Both move the point at which the projection
becomes durable, which changes what `AFTER_PROJECTION_COMMIT` means and
invalidates the 25-case fault matrix that currently proves crash safety.

**That change is not made here.** Trading a proof of crash safety for a third
of the commits, in the same session that measured them, is the wrong order. It
belongs in its own session with the fault matrix re-run as its acceptance
criterion. Section 13 lists it as an open gap.

---

## 9. A quadratic scan the benchmark found in the doctor

The scale run stalled. The 50,000-event mixed workload finished its pipeline in
under six minutes and then sat for thirteen more with one process at 70% of a
core. It was `doctor --deep`.

Three deep checks asked, per row:

```sql
NOT EXISTS (SELECT 1 FROM cognitive_memories m
            WHERE m.source_event_ids_json LIKE '%' || event_id || '%')
```

A leading wildcard cannot use an index. Each of the N outer rows scanned all M
memories: **O(N×M)**.

**Measured before the fix** (`reports/v082/doctor_scaling_before.json`):

| events | deep scan | s per 1,000 events | queries |
|---:|---:|---:|---:|
| 1,000 | 0.777 s | 0.78 | 34 |
| 5,000 | 23.65 s | 4.73 | 34 |
| 10,000 | 98.67 s | 9.87 | 34 |

Ten times the data took **127 times** the time — exponent **2.1**. The query
count is constant, so it was never "more queries"; it was the same 34 queries
each doing more work. Extrapolated, 100,000 events would have taken about
**2.75 hours**.

`docs/v082/OPERATIONS.md` claimed sub-millisecond scan time and that "the
queries are indexed, but that is an expectation, not a measurement". The
expectation was wrong, and that document has been corrected.

**The fix.** Read `source_event_ids_json` once into a set and test membership
in Python: **O(N+M)**, at a cost of roughly 10 MB of id strings at 100,000
memories. It is also stricter — a substring match could pair an event with a
memory that merely contains its id inside a longer one, which exact membership
cannot. A test now asserts that.

**Measured after the fix:** see section 10.

**Two sites with the same shape are deliberately left alone.**
`shadow_runner.legacy_projection()` and `reconciliation.py`'s repair
precondition each scan once *per event asked about* rather than once per row of
a full scan, so a single call is O(M), not quadratic. A caller in a loop makes
it quadratic anyway: comparing 10,000 shadow events took 62.5 s for exactly
this reason. Neither is on the runtime write path, and changing them was not
needed to unblock this benchmark.

---

## 10. Doctor scan time by database size

Single process, no workers competing, a fresh database per size, quick and
deep timed separately. Each database carries real events, real projections and
1,000 shadow records.

**After the fix** (`reports/v082/doctor_scaling.json`):

| events | projections | shadow | quick | deep | s per 1,000 events | queries | findings |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 2,000 | 1,000 | 0.028 s | 0.092 s | 0.092 | 35 | 1,009 |
| 5,000 | 6,000 | 1,000 | 0.088 s | 0.186 s | 0.037 | 35 | 1,009 |
| 10,000 | 11,000 | 1,000 | 0.254 s | **0.431 s** | 0.043 | 35 | 1,009 |
| 50,000 | 51,000 | 1,000 | 2.172 s | 3.597 s | 0.072 | 35 | 1,009 |
| 100,000 | 101,000 | 1,000 | 5.279 s | **8.480 s** | 0.085 | 35 | 1,009 |

**100× the data takes 92× the time — exponent 0.98.** Linear.

| | before | after | factor |
|---|---:|---:|---:|
| 10,000-event deep scan | 98.665 s | **0.431 s** | **229× faster** |
| 100,000-event deep scan | ~2.75 h (extrapolated) | **8.480 s** | ~1,170× |
| scaling exponent | 2.1 | **0.98** | quadratic → linear |

**The finding count is identical at every size, before and after: 1,009.** The
fix changes how the question is asked, not what is reported. That is the
evidence that it is a performance change and not a behaviour change — a faster
doctor that found fewer problems would be a worse doctor.

Query count moved 34 → 35: the one extra query is the single pass that reads
`source_event_ids_json` into the set.

The user's stated minimum was 10,000 events / 10,000 projections / 1,000
shadow records. That row is measured above, and so are 5× and 10× beyond it.

**Peak memory** during the 100,000-event scan: roughly 195 MB resident, read
from the OS process list rather than from the benchmark's own (then broken)
RSS function. That figure includes building the 297 MB database as well as
scanning it. The set itself is the ~10 MB of id strings described in section 9.

---

## 11. Scale — 10,000 / 50,000 / 100,000 events

4 producers + 4 workers, the configuration that peaked in section 4.

| events | produce | project | gap | p95 visibility | p99 | database | bytes/event | doctor |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 898/s | 703/s | 1.28× | 4,916 ms | 5,716 ms | 30.0 MB | 3,146 | 0 |
| 50,000 | 949/s | 613/s | 1.55× | 31,756 ms | 32,156 ms | 148.8 MB | 3,120 | 0 |
| 100,000 | 1,066/s | 556/s | 1.92× | **93,475 ms** | 93,900 ms | 297.5 MB | 3,120 | 0 |

**Storage is linear: 3.12 KB per event**, stable across all three sizes
(events, outbox row, ledger row and memory row together).

**Throughput is not.** Append holds — it even rises slightly — while
projection falls from 703 to 556 jobs/s as the database grows fivefold and then
tenfold. The producer/projector gap therefore widens from 1.28× to 1.92×, and
because the backlog is the integral of that gap over the run, **p95 visibility
grows faster than the workload does**: 4.9 s → 31.8 s → 93.5 s for 10× the
events.

This is the single most important result in the report. The pipeline has **no
steady state above the projection rate**. Latency under sustained overload is
not a property of the pipeline; it is a function of how long you overload it
for. An operating envelope has to be built from the projection rate, not from
the append rate, and the append rate is the one that looks impressive.

The queue drained to zero after producers stopped at every size, and the doctor
exited 0 at every size.

## 11b. Repeatability

The baseline measured 4p+4w at 10,000 events and got 1,164 events/s produced.
The scale run measured the identical configuration minutes later and got 328/s
— a factor of 3.5 on the same machine, same code, same corpus, same seed.

Quoting either as "the throughput" would be wrong, so the configuration was
run repeatedly.

Four back-to-back runs of 4p+4w at 10,000 events, with the databases removed
and five seconds of settling between them
(`reports/v082/reliability_repeatability.json`):

| run | produce | project | p95 visibility | peak queue |
|---:|---:|---:|---:|---:|
| 0 | 829.1/s | 703.9/s | 5,819 ms | 5,237 |
| 1 | 659.9/s | 618.3/s | 3,078 ms | 2,426 |
| 2 | 1,243.1/s | 709.9/s | 6,512 ms | 6,327 |
| 3 | 701.4/s | 655.0/s | 2,160 ms | 1,386 |

| metric | min | median | max | stdev | max/min |
|---|---:|---:|---:|---:|---:|
| producer throughput | 659.9/s | 765.2/s | 1,243.1/s | 266.4 | **1.88×** |
| projection throughput | 618.3/s | 679.5/s | 709.9/s | **43.3** | **1.15×** |
| p95 visibility | 2,160 ms | 4,449 ms | 6,512 ms | 2,100.9 | 3.02× |
| peak queue depth | 1,386 | 3,832 | 6,327 | 2,320.8 | 4.56× |

All four drained. All four doctor-clean. Zero lock errors.

**The projector is the stable number; the producer is the noisy one.**
Projection throughput varies by 15% run to run and by only ±43/s in absolute
terms. Append varies by 88%. Latency and queue depth inherit that noise
amplified, because both are driven by the *gap* between the two rates, and a
small absolute change in a difference is a large relative one.

That is what an operating envelope has to be built on: **the projection rate,
measured at its minimum, not the append rate at its best.**

**About the 328/s outlier.** The first scale run measured 328/s for *both*
producer and worker on this configuration — a third of everything else. It ran
immediately after the baseline suite deleted roughly 400 MB of databases from
the same temporary directory. None of the four runs above, each with settling
time and its own directory, reproduced anything near it. It is reported as a
contaminated measurement with an identified cause, not as the low end of the
range, and it is not used anywhere in the envelope.

## 11c. Soak

## 11c. Soak — one hour of continuous load

2 producers + 3 workers, 120 events/s target, 57 samples over the hour, worker
generation restarted every 20 minutes and the producer runtimes recycled every
20 cycles (`reports/v082/reliability_soak.json`, `soak_timeseries.jsonl`).

| | |
|---|---:|
| duration under load | 3,600 s |
| events appended | **366,715** |
| projections completed | **366,713** |
| sustained rate | ~104 events/s |
| **peak queue depth over the whole hour** | **6** |
| p95 visibility, median across windows | **80.06 ms** |
| p99 visibility, median across windows | **140.26 ms** |
| p95 best / worst window | 43.6 ms / 135.5 ms |
| p99 worst window | 845.5 ms |
| p95 first half → second half | 75.96 → 89.35 ms (**+17.6%**) |
| RSS first → last → peak | 89.4 → 100.1 → 106.5 MB (**+10.7 MB**) |
| worker generations | 3 (two restarts, both reclaimed) |
| producer runtime recycles | 36 |
| lock errors | **0** |
| dead letters | **0** |
| SQLite `integrity_check` | **ok** |
| database at the end | 1.05 GiB (**3.08 KB/event**) |
| deep doctor at the end | exit 0, **47.9 s** on 366,715 events |

**This is the measurement the rest of the report was missing.** Everything in
sections 4 and 11 ran the pipeline above its projection rate, so their
latencies describe a backlog. Here the input stays below it, the queue never
exceeds **6** jobs, and p95 visibility is **80 ms** — six times inside the
proposed 500 ms threshold, and sixty times below the 4.9-second figure the
saturated 10,000-event run produced.

**Latency drift is real but bounded.** p95 rose from 43.6 ms in the first
minute to a plateau around 72–90 ms, giving +17.6% between halves. It tracks
database size rather than elapsed time: it stopped rising once growth
flattened, and the worker restart at minute 20 brought it down rather than up.

**Memory did not leak.** +10.7 MB across 366,715 events and three worker
generations, peak 6.4 MB above the final reading.

**The WAL grows and does not shrink on its own.** 4.8 MB to 500 MB over the
hour — **46% of the database** — because a passive checkpoint can only copy
back as far as the oldest active reader, and with connections permanently open
it rarely gets to reset the file. It went to **zero the moment the last
connection closed**, so nothing is lost; but a long-lived process should be
expected to hold roughly half the database size again in WAL, and an operator
watching disk has to count it.

**One property this run failed to measure, and why.**
`queue_drained_after_stop` came back `False`, with two jobs pending out of
366,715. That is a defect in the harness, not in the kernel: this function
shared a single stop event between its producers and its workers, so the drain
check that followed had nobody draining the queue. Reporting that as a kernel
failure would be reporting a measurement that was never made.

It is fixed, and the property is measured three other ways:

* a 6-minute rerun with the fix — 39,698 appended, **39,698 completed, drained
  in 0.0 s** (`reliability_soak_verify.json`);
* the five mixed-live configurations in section 4, all drained;
* the three scale runs and four repeatability runs, all drained.

Twelve independent confirmations plus the rerun. The hour-long run's other
figures are unaffected: the workers had completed 366,713 of 366,715 before
they were told to stop.

---

## 12. Safe operating envelope

Sized from the **floor**, not the peak. The peak of a benchmark is the
luckiest run on an idle machine; an envelope built from it is a promise nobody
can keep.

The constraining stage is projection, and its lowest measured value across
every condition is **556 jobs/s** (100,000-event database, section 11) — lower
than the 618/s floor at 10,000 events, because projection slows as the database
grows.

```
SQLite single-node alpha, i5-12400F / NTFS / WAL / synchronous=NORMAL

  recommended producers            4
  recommended workers              4        (8 costs 41% of total throughput)
  projection floor (measured)      556 events/s
  safe sustained input rate        390 events/s      (30% headroom)
  burst rate                       550 events/s for up to 60 s
  maximum healthy queue depth      1,100             (~2 s of drain)
  expected p95 visibility          < 100 ms          at or below the safe rate
  storage                          3.12 KB per event, linear
```

**Why the safe rate is a third of what append can do.** Append sustains
~1,100 events/s and peaked at 2,594/s with no worker running. Sizing on that
number guarantees an unbounded queue: the projector cannot drain faster than
~556–710/s, so any sustained input above it grows the backlog forever and
visibility latency grows with it. Section 11 measures exactly that — 93 seconds
of p95 latency at 100,000 events, purely because the run was overloaded for 100
seconds.

**Alert on queue depth, not on latency.** Latency is a lagging indicator of a
backlog that has already formed. Depth crossing 1,100 is the earlier signal,
and `projection status` reports it.

## 12b. Thresholds

Every threshold proposed for this session, checked mechanically in
`reports/v082/reliability_summary.json`.

### Correctness — all required

| check | required | measured | |
|---|---|---|:---:|
| lost committed events | 0 | **0** | pass |
| lost committed debts | 0 | **0** | pass |
| duplicate cognitive projections | 0 | **0** | pass |
| duplicate target-local ledgers | 0 | **0** | pass |
| tenant / workspace leakage | 0 | **0** | pass |
| invalid projection keys | 0 | **0** | pass |
| unexplained shadow mismatches | 0 | **0** (10,000/10,000 MATCH) | pass |
| SQLite integrity | ok | **ok** | pass |
| crash recovery in the matrix | 100% | **7/7 scenarios** | pass |

### Reliability — all required

| check | required | measured | |
|---|---|---|:---:|
| queue drains after producers stop | yes | **yes, every run** | pass |
| stale leases after recovery | 0 | **0** | pass |
| orphaned CLAIMED jobs | 0 | **0** | pass |
| retry follows backoff | yes | **yes** (mean 1.21 attempts to complete) | pass |
| dead-letter at max attempts | 5 | **5** (mean 3.18) | pass |
| doctor deep: no unexplained FAIL/CRITICAL | 0 | **0** | pass |

### Performance — proposed, not required

| check | proposed | measured | |
|---|---|---|:---:|
| sustained projection throughput | ≥ 100/s | **556–710/s** | pass |
| p95 end-to-end visibility, unsaturated | ≤ 500 ms | **80.06 ms** | pass |
| p99 end-to-end visibility, unsaturated | ≤ 1,000 ms | **140.26 ms** | pass |
| shadow overhead p95 | ≤ 10% | **99.4%** | **fail** |

### Soak

| check | required | measured | |
|---|---|---|:---:|
| no data loss over an hour | yes | **366,713 of 366,715**, integrity ok | pass |
| RSS does not grow without bound | < 100 MB | **+10.7 MB** | pass |
| queue does not grow without bound | yes | **peak depth 6** | pass |
| no serious latency drift | < 50% | **+17.6%** | pass |
| doctor clean at end of run | exit 0 | **exit 0** | pass |
| queue drains after producers stop | yes | **yes** (rerun; see 11c) | pass |

**18 of 19 checks pass.** The one failure is the shadow overhead.

The shadow-overhead threshold is the only performance threshold missed, and it
is missed by a factor of ten. Section 5 records what it costs in absolute terms
— 0.30 ms at p95 — and does not argue that the threshold should move. Moving it
is a decision, not a measurement.

Latency is judged against the soak rather than against workload C. The
threshold says "when the queue is not overloaded"; in every mixed run the
producer outran the projector, so the queue was overloaded by construction and
its p95 measures backlog depth rather than pipeline latency.

## 13. Known gaps

Things this benchmark did not establish, in the order they would matter for a
cutover decision.

**One machine, one node, one engine.** Every figure here is a single
i5-12400F with SQLite in WAL mode. PostgreSQL — where `FOR UPDATE SKIP LOCKED`
replaces the claim UPDATE and multiple writers are not serialised — is
untested, and is the thing most likely to change the shape of the throughput
curves. Section 4's saturation point is a property of SQLite's single writer,
not of the pipeline design.

**One projection type.** `cognitive_memory` is the only type with a builder.
Nothing here says anything about `self_model_update`, `prospective_memory`,
`context_block` or `checkpoint_reference` beyond confirming that jobs for them
dead-letter with a stated reason rather than silently doing nothing.

**Latency samples come from jobs that completed.** A dead-lettered or skipped
job contributes no latency sample. Those are counted separately (section 7),
but the percentiles describe successful work only. In a healthy run that is
almost all of it; in a degraded one it would flatter the numbers.

**The removable commit is still there.** Section 8 names it, measures it at a
third of the per-job commits, and explains why removing it needs the fault
matrix re-run rather than a benchmark session. The planned
`perf(projection)` commit was therefore not made; the performance commit in
this series is `perf(doctor)`, and it is named for what it actually changed.

**Two `LIKE '%id%'` sites remain** (section 9). Neither is on the write path;
both make a caller-in-a-loop quadratic.

**No incremental doctor scan.** Deep is now linear, but it is still a full
scan: 8.5 s at 100,000 events, and a database ten times larger would take
roughly ten times as long. Quick, deep and tenant-scoped exist; incremental
does not.

**Peak memory is `WorkingSetSize` from the OS, not a profiler.** It tells you
the process did not leak; it does not tell you where the bytes went. And it
was only being read correctly from the soak onwards — see section 1.

**The default path is still legacy.** `observe()` enqueues nothing unless
projection mode is shadow or outbox. Everything measured here about the
projection pipeline describes a path that is not yet primary — which is the
point of measuring it before making it primary.

---

## 14. Cutover recommendation

# CONDITIONAL GO — `cognitive_memory` only

Correctness is green everywhere. Reliability is green everywhere. One
performance threshold is missed, and one whole class of deployment is
untested. That is the definition of conditional, and the conditions are below.

### Why not NO-GO

None of the NO-GO triggers fired. Across roughly 900,000 events written over
every workload in this report:

* **0** lost committed events, **0** lost committed debts
* **0** duplicate projections, **0** duplicate ledger rows
* **0** tenant or workspace leakage, **0** invalid projection keys
* **0** unexplained shadow mismatches — 10,000 of 10,000 `MATCH`
* **0** shadow rows reachable from production tables or from `recall()`
* `integrity_check` = ok after every run, including a 1.05 GiB database
* every queue drained after its producers stopped
* all seven recovery scenarios passed **under load**, including a real
  `TerminateProcess` mid-drain and a crash between projection commit and
  outbox completion — the exact window the ledger exists to close, which
  recovered via `already_built` as designed
* **0** unexplained dead letters; every one of the 502 in the fault workload
  was either an injected failure or a declared capability gap

### Why not full GO

**The shadow-overhead threshold is missed by a factor of ten** — 99.4% at p95
against a proposed 10%. In absolute terms it is 0.30 ms, and it may well be
acceptable. But the threshold was written down before the measurement, and
moving it afterwards to make a run pass is the one thing a benchmark must
never do. It is a decision for the owner, not for this report.

**PostgreSQL is untested**, and every throughput curve here is shaped by
SQLite's single writer: append does not scale past one producer, four workers
saturate, eight workers cost 41%. On a backend with `FOR UPDATE SKIP LOCKED`
none of that shape is guaranteed to hold — better or worse. A GO based on
these numbers would be a GO for one storage engine on one machine.

**The write path is measured but not tuned.** Three commits per job, one of
them removable, and the removal needs the fault matrix re-run first.

### Conditions

1. **`cognitive_memory` only.** The other four projection types have no
   builder and nothing here says anything about them.
2. **Legacy stays behind the feature flag, and rollback stays one setting
   away.** `projection_mode` already provides this; it must not be removed as
   part of the cutover.
3. **Operate inside the envelope in section 12**: 4 producers, 4 workers,
   ≤ 390 events/s sustained, alert on queue depth above 1,100.
4. **Size on the projector, never on the appender.** Append can absorb three
   times what the projector can drain; anything sized on the append figure
   produces an unbounded queue.
5. **Watch queue depth, not latency.** Latency is a lagging indicator of a
   backlog that has already formed.
6. **Budget for the WAL**: about half the database size again while the
   process is long-lived.
7. **Re-measure on the target hardware.** Everything here is one i5-12400F,
   and the same configuration varied by 1.88× run to run on it.

### Before a full GO

* decide on the shadow-overhead threshold — accept 0.30 ms, or reduce it
* run this same benchmark against PostgreSQL
* remove the redundant projection commit, with the fault matrix re-run as its
  acceptance criterion, and re-measure
* a longer soak: 6 hours and 24 hours, which the harness supports and this
  session did not run

**Even under GO, `observe()` must keep its legacy path.** A cutover that
cannot be undone in one setting is not a cutover, it is a migration.
