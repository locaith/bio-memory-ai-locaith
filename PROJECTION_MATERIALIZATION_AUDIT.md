MATERIALIZATION ARCHITECTURE GAP

A materializing consumer exists, executes, and writes `cognitive_memories`. It
does not resurrect forgotten data — both tombstone bolts hold, and I killed a
mutant to prove the second one is load-bearing. What is missing is the wiring:
nothing that ships starts the consumer, the documented operator command exits 2,
production is pinned to `legacy`, and the live store has never had a worker
constructed against it. Three defects sit on the path and become live the moment
anyone wires it.

Audit date 2026-08-17. HEAD `44acdc7`, branch `fix/v082-doctor-bounded-snapshots`,
tree clean at the time of measurement. Repo read-only throughout; every write in
this document went to throwaway databases under the system temp scratchpad. The
real store at `.bio-agent-os/` was copied, never opened for writing. No consumer
was implemented, no projection was inserted by hand, no outbox was drained in
anger, #7 / Group B / D1 / the grader / frozen answers / thresholds untouched.

Verdicts are **PROVEN**, **DISPROVEN**, or **OPEN**. An untested hypothesis is
OPEN and is not a finding.

---

## 1. Static pipeline — the `???` is filled

The premise this audit was commissioned under was:

> A grep for `projection_outbox` across `bio_agent_os/` found only diagnostics
> reads. No code there consumes the outbox and writes `cognitive_memories`.

**DISPROVEN.** The full path, every arrow executed at least once in probe 1:

```
observe() / append()  ──enqueue──▶  projection_outbox
   event_store.py:138                (transactional, same tx as the event)
   projection_engine.py:311,:398

projection_outbox  ──claim──▶  ReconciliationWorker.run_once()
                                 reconciliation_worker.py:479  self.outbox.claim(...)

   ──▶ ReconciliationWorker.process()          reconciliation_worker.py:308
         :313  ledger check   (exactly-once effect)
         :334  buried()       ◀── BOLT 2, the at-apply tombstone check
         :343  dependencies
         :357  replayability
         :367  event load
         :394  builder.build(event, job, projection_conn)

   ──▶ CognitiveMemoryBuilder.build()          reconciliation_worker.py:113
         :138  self.memories.put(memory, commit=False)

   ──▶ SQLiteMemoryStore.put()                 memory_store.py:291
         INSERT INTO cognitive_memories(...)

   ──▶ projection_conn.commit()                reconciliation_worker.py:428
         ledger row + projection, one transaction

   ──▶ outbox.complete()                       reconciliation_worker.py:441
```

**Why the original grep could not find it.** `reconciliation_worker.py` never
types the table name on its consumption path. It claims through the
`ProjectionOutbox` *object* (`self.outbox.claim`) and writes through the
`SQLiteMemoryStore` *object* (`self.memories.put`). One indirection on each
side. The single literal `projection_outbox` in the file is at line 283, an
unrelated dependency-status SELECT — so the grep did hit the file and the hit
was classified as diagnostics. A table-name search is structurally blind to a
codebase that wraps its tables in classes; that is the transferable lesson, not
"the grep was too narrow".

Executed proof, throwaway DB, `projection_mode=outbox`, product APIs only:

```
after observe()   cognitive_memories = 0   outbox = {pending: 1}   ledger = NO TABLE
after run_once()  cognitive_memories = 1   outbox = {completed: 1} ledger = 1
   row.content  = 'So dien thoai cua Hoang Yen la 0977123456.'
   row.metadata = projection_type=cognitive_memory  source_event_id=861d3577…
>>> MATERIALIZATION EXECUTED: True
```

**PROVEN.** `replay()` only enqueues — that part of the original finding was
right. It was never the whole pipeline.

---

## 2. Runtime reachability, per entrypoint

| # | Entrypoint | Started by | Drains the outbox? | Evidence |
|---|---|---|---|---|
| 1 | `bio-agent-os` → `bio_agent_os.cli:main` | Dockerfile CMD, compose | **NO** | `pyproject.toml:37`. Executed: `bio-agent-os --db x.db projection worker --once` → **exit 2**, `invalid choice` |
| 2 | `bio-agi-memory` → `cognitive.cli:main` | manual | **NO** | sole subcommand is `doctor` |
| 3 | `bio-memory-hook` → `cognitive.hook_cli:main` | **LIVE**, `.claude/settings.json` SessionStart / UserPromptSubmit | **NO** | `grep -cE "outbox\|worker\|drain\|projection" hook_cli.py` = **0** |
| 4 | FastAPI (`serve-api`) | Dockerfile :8055 | **NO** | lifespan calls `init_components()` only; no thread, no task |
| 5 | MCP (`serve-mcp`) | manual | **NO** | 0 references |
| 6 | `cognitive/doctor_cli.py` — the **only** module exposing `projection worker` / `projection drain` | **NOTHING** | would, if started | `cmd_worker` at `:229` → `worker_for(...).run_forever/run_once`. Repo-wide grep for `doctor_cli` across `.py .toml .md .yml .json` = **zero inbound references**. Reachable only as `python -m bio_agent_os.cognitive.doctor_cli` |
| 7 | `background_jobs/{hippocampus,garbage_collector,graph_builder}` | `core/runtime.py` | **NO** | legacy L1/L2/KG; never touches the outbox |
| 8 | `scripts/canary_supervisor.py`, `scripts/join_soak.py`, `scripts/staging_canary.py` | manual | yes if run | canary/soak tooling, not a service |
| 9 | systemd / schtasks / supervisord / cron / CI job | **none exist** | — | no unit, no schedule, no workflow step |

**PROVEN — the consumer is real and no shipped entrypoint executes it.**

The gap is a naming collision that was never closed. `doctor_cli.py` sets
`prog="bio-agent-os"`, the same name as the installed console script, which is a
*different module*. So every documented operator invocation is dead:

- `docs/v082/OPERATIONS.md:16,:22` · `CANARY_RUNBOOK.md:27,:38` ·
  `ROLLBACK_RUNBOOK.md:71,:94,:117` · `README.md:491,:502`
- and `diagnostics.py:757`, where the doctor's own repair advice is
  `suggested_action="run \`bio-agent-os projection worker --once\`"`

The doctor tells an operator to run a command that exits 2.

**Second, independent reason nothing drains in production.**
`BIO_AGENT_PROJECTION_MODE` is set in `scripts/canary_supervisor.py` and
`scripts/staging_canary.py` and **nowhere else** — not in `.env`, not in
`docker-compose.yml`, not in the platform. Grepped the consuming platform at
`c:/locaith/Final Platform Agent AI OS Intelligent Tuan Anh`: **no occurrence**.
`shadow.py:54` therefore resolves `LEGACY`, `facade._projection_types()` returns
`()`, and `observe()` enqueues nothing.

**Third, execution-grade proof on the live store.** Read-only copy of
`.bio-agent-os/memory.db` (copied to scratchpad; original untouched), 19 tables:

```
cognitive_events     232      projection_outbox    PRESENT, 0 rows
cognitive_memories   205      projection_ledger    ABSENT
shadow_memories        0      memory_tombstones    ABSENT
                              hippocampus_labels   ABSENT
```

`ReconciliationWorker._migrate()` (`:222`) creates `projection_ledger`
unconditionally in `__init__`. The table is absent, so **a worker has never been
constructed against the live store** — PROVEN, not inferred. `memory_tombstones`
is absent too, so the 16/08 privacy fix has never touched it either.

---

## 3. Every outbox reader, file:line

`W?` = does this site cause a write to `cognitive_memories`.

### Production — `bio_agent_os/cognitive/`

| File:line | What it does | W? |
|---|---|---|
| **reconciliation_worker.py:479** | `outbox.claim()` — **leases rows out of the queue** | **YES** |
| **reconciliation_worker.py:113–138** | `CognitiveMemoryBuilder.build` → `memories.put` | **YES** |
| reconciliation_worker.py:314, :441 | `complete()` | via above |
| reconciliation_worker.py:335 | `skip()` — tombstoned — **defect D1, §6** | — |
| reconciliation_worker.py:418 | `skip()` — builder declined (correct call) | — |
| reconciliation_worker.py:345,:351,:358,:370,:376,:400,:433 | `fail()` | — |
| reconciliation_worker.py:283 | SELECT status, dependency gate | no |
| outbox.py:213–304 | `claim` / `complete` / `fail` / `skip` — the mechanism | no |
| outbox.py:308–368 | `get` / `by_event` / `pending` / `dead_letters` / counts / lag | no |
| **projection_control.py:135** | `drain()` → `worker_for()` | **YES (indirect)** |
| projection_control.py:141,:145,:157 | outstanding count, counts | no |
| **doctor_cli.py:229** | `cmd_worker` → `run_once` / `run_forever` | **YES (indirect)** |
| doctor_cli.py:147 | `cmd_drain` | **YES (indirect)** |
| reconciliation.py:258,:304,:310,:316,:322 | doctor recipes: stale lease → pending, attempts exceeded → dead_letter, completed-without-projection → pending, child-with-dead-parent → dead_letter | no |
| projection_engine.py:161,:201,:257,:336,:371 | status SELECTs, orphan scan, `_reset()` → pending | no |
| projection_engine.py:311,:398 | `enqueue` (producer) | no |
| event_store.py:35,:138 | constructs the outbox; enqueue (producer) | no |
| hippocampus_label.py:207,:213,:225,:245 | enqueue + LEFT JOIN reads | no |
| **shadow_runner.py:37** | `ReconciliationWorker` on the **production** outbox with `ShadowMemoryBuilder` | no — writes `shadow_memories`, but **consumes production jobs** |
| diagnostics.py (14 sites) | read-only checks | no |
| doctor_cursor.py:45,:241 · doctor_incremental.py:112 | cursor `MAX(rowid)` reads | no |
| privacy_registry.py:27,:200–203 | metadata declaration only | no |

### Scripts, benchmarks, tests

| File:line | What it does | W? |
|---|---|---|
| **scripts/join_soak.py:124** | `worker_for(memory_os)` | **YES** |
| scripts/canary_supervisor.py:602 · staging_canary.py:294 | enqueue; shadow worker | no / shadow |
| **benchmarks/reliability/harness.py:73,:129,:134** | `InstrumentedWorker(ReconciliationWorker)` | **YES** |
| **benchmarks/reliability/verify_single_commit.py:61,:73** | `worker_for` | **YES** |
| **benchmarks/reliability/workloads.py:585** | `CognitiveMemoryBuilder` | **YES** |
| benchmarks/reliability/{workloads:668,685, recovery:308, doctor_scaling:69, profile_writepath} | enqueue, fault injection, counts | no |
| tests (11 modules, incl. `test_reconciliation_worker`, `test_fault_matrix`, `test_projection_control`, `test_replay_resurrection`, `test_memory_resurrection`, `test_provable_forgetting`, `test_shadow_mode`) | drive `worker_for` / `ReconciliationWorker` | **YES** |

`git grep -l projection_outbox` = **40 files** repository-wide.
`git grep "DELETE FROM projection_outbox"` = **empty**. Rows are only ever
status-transitioned; nothing anywhere deletes a queue row.

---

## 4. The old replay tests — which boundary each crossed

Three test files, written in this order, two of them complete and the newest one
short of the finish line.

| File | Added | Rebuild helper | Boundary crossed |
|---|---|---|---|
| `tests/test_memory_resurrection.py` | `2b3eb52` 08-14 23:12 | `_rebuild_everything` :93 → `rebuild_version()` **+ `worker_for(memory_os)`** :101 | **MATERIALIZATION** |
| `tests/test_replay_resurrection.py` | `47f72bc` 08-15 22:38 | `_rebuild` :115 → `replay()` + `rebuild_version()` **+ `drain(os_, 30s)`** :121 | **MATERIALIZATION** |
| `tests/test_forget_resurrection.py` | `48d8006` 08-17 17:04 | `_rebuild` :182 → `ProjectionReplayEngine(...).replay(dry_run=False)` **and nothing else** | **ENQUEUE ONLY** |

The two older files reach `cognitive_memories` through the product's own worker.
Both carry the correct positive control — `test_it_is_the_tombstone_doing_the_work`
(`test_replay_resurrection.py:132–155`) lifts the tombstone and asserts the
memory **must** come back, `_holding(os_) == 1`, so a green result cannot be green
for the wrong reason.

The newest file replays and stops. That single missing line is the origin of the
whole "no materializer" claim: its author looked at a helper that never drained,
saw nothing materialize, and concluded nothing could.

Two further defects in that file, both measured:

- **The tripwire cannot fire.** `_materialised()` (`:195–207`) counts
  `WHERE status != 'done'`. `JobStatus` is
  `pending / in_progress / completed / dead_letter / skipped` — **there is no
  `'done'`**. Every row always matches, so the helper returns `True` only when
  the outbox is *empty*. A fully drained, fully materialized queue still reads
  `False`. The `xfail(strict=True)` marker written to "go red the day someone
  adds a consumer" is therefore permanently satisfied — and the consumer it was
  watching for had already existed for twelve days when the marker was written.
- **Adding the drain does not make the mutant test pass.**
  `test_the_mutant_resurrects_and_this_test_must_catch_it` blinds
  `projection_engine.buried_among` only. Measured in §6: blinding bolt 1 alone
  never resurrects, because bolt 2 catches it. That test needs both bolts blinded
  (or a fixture where only bolt 1 applies) — not just a drain.

Current suite state, executed:
`tests/test_forget_resurrection.py tests/test_replay_resurrection.py`
→ **16 passed, 2 xfailed in 30.78 s**.

---

## 5. Git chronology of the subsystem

| When | Commit | What landed |
|---|---|---|
| 08-04 23:00 | `9564f66` | `docs/v082/WRITE_PATH_ANALYSIS.md` — outbox **and** reconciliation worker both specified, `:178–182` |
| 08-04 23:04 | `8c6c5fc` | transactional projection outbox (producer). Its own message: *"The queue is in place; nothing drains it yet."* |
| 08-04 23:14 | `8f50977` | replay engine + projection registry |
| **08-04 23:41** | **`d42cc65`** | **leased reconciliation worker — the consumer. 37 minutes after the producer.** Message: *"This drains the queue."* |
| 08-05 07:34 | `cde408e` | process-level projection crash matrix |
| 08-05 11:10 | `e6e9dff` | load benchmark verdict: **CONDITIONAL GO**, not GO |
| 08-05 11:27 | `9fd828f` | remove redundant target transaction commit |
| 08-05 12:15 | `6b23671` | pause / resume / **drain** with canary SLOs |
| 08-06 07:13 | `98a3801` | run the WAL manager whoever drives the worker |
| 08-10 07:24 | `9abeed5` | hippocampus label join wired into the write path |
| 08-14 22:44 | `3501b20` | provable forgetting |
| 08-14 23:12 | `2b3eb52` | three meanings of forgetting; first resurrection test |
| 08-15 22:38 | `47f72bc` | **records replay resurrection as observed fact** |
| **08-16 09:10** | **`c7696ff`** | **tombstones — the fix. Both bolts land here, and so does defect D1.** |
| 08-16 21:47 | `e245738` | privacy perimeter |
| 08-17 17:04–17:57 | `48d8006` → `44acdc7` | #7 acceptance, gate 5 recorded NOT_EVALUABLE |

**PROVEN — a consumer was never removed.**
`git log --all --diff-filter=D --name-only` matching `worker|consum|outbox|project|drain`
returns nothing. `reconciliation_worker.py` has only additive commits.

**PROVEN — the classic "producer written, consumer deferred" pattern is not what
happened here.** Producer, replay engine and consumer landed inside 41 minutes,
followed by a fault matrix, shadow mode, a load benchmark, canary controls and
three runbooks. The consumer is better tested than the producer.

**PROVEN — the real shape is: built, benchmarked, deliberately never cut over.**
`docs/v082/RC1_RELEASE_NOTES.md:1–27`: *"Legacy remains the default write path…
Nothing here turns the projection pipeline on for anybody. Not a cutover. Not
production."* No commit after the CONDITIONAL GO ever flips the flag.

---

## 6. The dynamic experiment

Six probe scripts, all against throwaway databases created per scenario, all
driving product APIs — `MemoryOS.observe`, `.remember`, `.recall`,
`forgetting.forget_derived`, `ProjectionReplayEngine.replay` /
`.rebuild_version`, `worker_for(...).run_once`, `projection_control.drain`,
`tombstones.place` / `.lift`, `ProjectionOutbox.enqueue` / `.skip`.

### 6.1 The mutation matrix — is bolt 2 load-bearing?

Two fixtures, because they exercise different bolts. A = the phone number, plus
two control memories that must survive every run (they did: 3 → 2 in every cell).

**F1 — legacy write, no job queued.** Replay has to create the job, so bolt 1 is
in play.
**F2 — outbox write, job queued *before* the forget.** Replay enqueues nothing
(`enqueued=0`, the key already exists), so **only bolt 2 can stop it.**

| mutation | F1 legacy/orphan | F2 queued-before-forget |
|---|---|---|
| clean | not resurrected | not resurrected |
| bolt 1 blinded (`projection_engine.buried_among` → `set()`) | not resurrected | not resurrected |
| **bolt 2 blinded** (`reconciliation_worker.buried` → `False`) | not resurrected | **RESURRECTED** |
| both blinded | **RESURRECTED** | **RESURRECTED** |

Per-cell numbers for the two decisive cells:

```
f2-clean    replay enqueued=0 skipped_tombstoned=1 | worker completed=2 tombstoned=1
            >>> A AFTER REPLAY+DRAIN = 0
f2-blind2   replay enqueued=0 skipped_tombstoned=1 | worker completed=3 tombstoned=0
            >>> A AFTER REPLAY+DRAIN = 1     ← one mutation, opposite outcome
```

**PROVEN — bolt 2, the at-apply check at `reconciliation_worker.py:334`, is
executed and individually necessary.** This is exactly the "enforcement AFTER
enqueue" the laws demand, and it is now demonstrated by a killed mutant rather
than asserted by a comment. Bolt 1 is redundant in F1 and irrelevant in F2; bolt
2 is the load-bearing one. Both are needed: F1-both-blinded shows bolt 1 is the
only thing standing when a replay creates the job on a store with no queued work.

A caveat that matters for anyone re-running this: a fixture in which the worker
has *already* built the projection produces `replay enqueued=0` and
`rebuild_version enqueued=0` in every cell, because `forgetting.DERIVED_TABLES`
does not include `projection_ledger` — the ledger row survives the forget, so
the event is no longer an orphan and nothing is re-offered. My first mutation run
had exactly that shape and showed no resurrection in any cell, which would have
been a false all-clear. The mutant only becomes killable on a fixture where work
is genuinely owed.

### 6.2 D1 — the tombstone skip never lands

```python
reconciliation_worker.py:335   self.outbox.skip(job.job_id, "sự kiện đã bị đặt bia mộ")
outbox.py:287              def skip(self, event_id: str, projection_type: str, *, reason: str = "") -> bool:
```

`job_id` arrives as `event_id`; the Vietnamese reason string arrives as
`projection_type`. The `WHERE event_id=? AND projection_type=?` matches zero
rows. The return value is discarded. Line 418 in the same file calls it
correctly — the asymmetry is what makes this a slip rather than a convention.

Measured, side by side on the same stuck row:

```
as shipped   skip(job_id, reason)              -> False   status stays in_progress
as line 418  skip(event_id, ptype, reason=...) -> True    status becomes 'skipped'
```

Consequences, every one executed:

```
after a direct enqueue + drain on a tombstoned event:
   worker metrics  claimed=1 tombstoned=1        memory NOT resurrected
   outbox row      status='in_progress' attempts=1 locked_by='u1b' completed_at=None
   skipped rows    0

drain(timeout=6s) -> {'drained': False, 'timed_out': True, 'remaining': 1,
                      'idle_rounds': 30, 'note': 'jobs remain: check for backoff
                      windows, dependencies waiting on a parent, or dead letters
                      — none of which draining harder resolves'}
   wall clock 6.0s — the full timeout, every time. Default timeout is 300 s.

eight cycles, lease 0.05 s, real sleeps, max_attempts=5:
   attempts 1,2,3,4,5,6,7,8   status in_progress throughout
   dead_lettered = 0 in every cycle       A_present = 0 in every cycle

with the arguments corrected by hand:
   drain -> {'drained': True, 'seconds': 0.0, 'remaining': 0}
```

Four separate harms: (a) the job is never retired and is re-claimed forever;
(b) `attempts` grows without bound and **never dead-letters**, because `fail()` —
the only path that applies `max_attempts` — is never called here; (c)
`projection_control.drain()` can never return `drained=True` on any store that
has ever deleted anything, and `ROLLBACK_RUNBOOK.md:71` uses drain-to-zero as the
gate for declaring a rollback complete; (d) `metrics.tombstoned` reports success
while the database says `in_progress` — the metric and the row disagree, and
`lag_seconds()` counts only `pending`, so the leak is invisible in the health
signal.

**This is not a resurrection.** `buried()` re-gates the build on every cycle and
the memory stayed deleted in all eight. It is a liveness and queue-hygiene defect,
and it falsifies `privacy_registry.py:203`, which records the outbox as
*"drained and skipped on tombstone"* — the skipped half is false.

Introduced by `c7696ff`, the tombstone commit itself, whose own comment says
dead-lettering *"would put a permanent error in the queue for every deletion"*.
The code instead leaves a permanently stuck `in_progress` row there.

### 6.3 D2 — outbox mode double-writes, and a forget by id reaches only one copy

The production ingest shape is `observe()` then `remember()` (`hooks.py:71,:101`).
In outbox mode `observe()` enqueues and `remember()` still writes. Nothing
suppresses either. Measured:

```
after observe+remember :  1 memory   bfff9e69 episodic 'So dien thoai …0977123456.'
after worker run_once  :  2 memories bfff9e69  +  e9254172 episodic, identical content
```

The builder's row is a second, independent copy under a new `memory_id`.
`forget_derived(memory_id=…)` resolves targets by id (`forgetting.py:384`), so it
deletes the one the caller knows about and leaves the other:

```
forget_derived(memory_id=bfff9e69)
   memories_deleted = 1     tombstoned = 1
   verified_clean   = False
   survives_rebuild = True
   note   = 'CÒN SÓT ở 2 chỗ — chưa xoá sạch'
   residue = [cognitive_memories.content ×2]
after: 1 row still holds the number
recall("so dien thoai Hoang Yen", ctx) -> 1 hit: 'So dien thoai cua Hoang Yen la 0977123456.'
```

**PROVEN — content the caller asked to forget survives and is returned by
`recall()`.** The verifier catches it and says so plainly; the call still returns
normally and nothing forces the caller to read the report. `forget_derived(subject=…)`
does not have this problem — `find_by_subject` scans content, finds both copies,
and `verified_clean` comes back `True` with both rows gone.

### 6.4 D3 — a forget while the only copy is still in the queue

```
observe() in outbox mode:  memories 0, outbox {pending: 1}
forget_derived(subject="Hoang Yen")
   memories_deleted = 0   tombstoned = 0
   note = 'không tìm thấy ký ức nào khớp'
   tombstones placed = 0          ← nothing recorded, so nothing to enforce later
worker run_once  -> completed=1
   holding = 1                    ← the content appears AFTER the forget
```

**PROVEN.** Because no memory row exists yet, `_resolve` returns empty, the
function returns early at `forgetting.py:593` and never reaches `_bury()`. Both
bolts are irrelevant: there is no tombstone. The forget did report that it found
nothing, so the caller is not lied to — but "I asked to forget it and then it
appeared" is the user-visible outcome. Latent while production is legacy.

### 6.5 Lift semantics

As shipped, because the tombstoned job is still sitting `in_progress` (D1),
lifting a tombstone and running **one ordinary worker cycle with no replay at
all** restores the memory: `holding` 0 → 1, outbox `{completed: 2}`. With D1
fixed the row would be `skipped` and a restore would require an explicit replay.
`test_memory_resurrection.py:144` treats lift-plus-rebuild as the restore path,
which implies the second is intended. **OPEN — which semantics is correct is the
owner's design call.** I report the divergence and do not adjudicate it.

### 6.6 Bounded list — what was tried

**Tried:** `observe`, `remember`, `recall`, `forget_derived` by `memory_id`, by
`subject`, and with `needle` alone; `ProjectionReplayEngine.replay(dry_run=False)`;
`.rebuild_version()`; `worker_for(...).run_once()` singly, in loops, and with
expiring leases; `projection_control.drain()` at 6 s; direct `ProjectionOutbox.enqueue`
of a job at a higher projection version (the bolt-2 bypass); `outbox.skip()` with
both argument orders; `tombstones.place` / `.lift` / `.tombstones`; mutation of
`projection_engine.buried_among` and `reconciliation_worker.buried`, singly and
together, across two fixtures; store construction in `legacy`, `shadow` and
`outbox` modes; `outbox.counts()` and `.lag_seconds()`; the installed
`bio-agent-os` console script; `pytest` on both resurrection test files.

**Not tried — a later reader must not read these as cleared:** `run_forever()`;
the `hippocampus_label` builder under a tombstone; `shadow_runner` consuming
production jobs; `consolidation.py:248`; `memory_store.supersede():418`;
multi-tenant tombstone scoping; a concurrent `forget` racing a mid-build worker;
`ReconciliationEngine.run(deep=True, repair=True)` against a stuck job (my call
signature was wrong and I did not retry); the PostgreSQL `claim()` path; the four
projection types with no builder; driving `doctor_cli` through real argv.

---

## 7. Tombstone enforcement map — before-enqueue vs at-write

Exactly one writer, exactly three readers, repository-wide.

| | Site | File:line | Position | Verdict |
|---|---|---|---|---|
| W | `place()` via `_bury()` | `forgetting.py:489` (on `memories.conn`) | — | — |
| 1 | `buried_among` in `replay()` | `projection_engine.py:291` | **BEFORE enqueue** | PROVEN executed; redundant in F1, irrelevant in F2 |
| 2 | `buried_among` in `rebuild_version()` | `projection_engine.py:364` | **BEFORE enqueue** | PROVEN executed (`skipped_tombstoned=1`) |
| 3 | `buried` in `ReconciliationWorker.process()` | `reconciliation_worker.py:334` | **AT WRITE — after enqueue, immediately before `builder.build()`** | **PROVEN load-bearing, mutant killed** |

Site 3 lives in `process()`, not in any builder, so it is builder-agnostic and
covers the shadow path too.

**Cross-connection visibility.** Tombstones are written on `memories.conn`; sites
1–3 read `self.conn`, which for the worker is `events.conn`. `MemoryOS.__init__`
(`facade.py:69–72`) resolves one path for both stores — one file, two connections
— so committed tombstones are visible across them. That is load-bearing and
**undefended**: `buried()` swallows `sqlite3.OperationalError` and returns
`False` (`tombstones.py:119–123`) — it **fails open**. `worker_for` never splits
the file, but `ReconciliationWorker(conn_A, projection_conn=conn_B)` accepts two
arbitrary connections and a split-file deployment would resurrect silently with
no error. Nothing constructs one today. **OPEN — hypothesis, not a finding.**

`diagnostics.py` has **zero** occurrences of `buried` or `tombstone`. The doctor
has no tombstone awareness at all; its `COMPLETED_WITHOUT_PROJECTION` repair
resets a job to pending so the worker rebuilds it. Today that finding's
precondition stays false because the ledger row survives a forget. **OPEN — not
probed.**

---

## 8. Every write path into `cognitive_memories`, and which consult tombstones

Two INSERT statements exist in production code, both in `memory_store.py`.

| Statement | Caller | Tombstone consulted? |
|---|---|---|
| `put()` :291 | **`reconciliation_worker.py:138`** (the builder) | **YES** — bolt 2 at `process():334`, at apply time |
| `put()` :291 | **`facade.remember():477`** | **NO** |
| `put()` :291 | `consolidation.py:248` | NO — reachable only from surviving rows. **OPEN** |
| `put()` :291 | `memory_store.supersede():418` | NO — `latest()` returns None on a deleted row. **OPEN** |
| `put_many()` :363 | **`facade.bulk_ingest():577`** | **NO** |
| `shadow.py:269` | `ShadowMemoryStore.put` → `shadow_memories` | n/a, different table |

The rebuild path is guarded at both ends. **The ordinary write path is not
guarded at all** — a fresh `remember()` of a tombstoned event's content writes it
straight back. Whether that is a hole or correct by design (a new assertion of a
fact should be allowed) is a policy question, but `privacy_registry.py:88`
records `cognitive_memories` as `replay="blocked by tombstone"` and says nothing
about the direct path, so the gap is undocumented either way.

---

## 9. Safety-claim matrix

Every claim this project has made about forgetting and replay, against what is
actually proven, by what, at which boundary.

| # | Claim | Verdict | Proven by | Boundary crossed |
|---|---|---|---|---|
| 1 | A forget removes the memory from the serving layer | **PROVEN** | `test_replay_resurrection.py:68,:73`; probe: `deleted=2 → holding 0` | projection rows |
| 2 | The event log keeps the payload; a forget is reversible and says so | **PROVEN** | `test_replay_resurrection.py:100`; `reversible=True`, `reversible_via` measured | event log |
| 3 | A tombstone stops a **replay** rebuilding the memory | **PROVEN** | `test_replay_resurrection.py:124`; probe F1-clean, `skipped_tombstoned=1`, A absent | enqueue |
| 4 | It is the tombstone doing the work, not an inert pipeline | **PROVEN** | `test_replay_resurrection.py:132` (lift → must return, `_holding==1`); probe §6.5 reproduces the lift | materialization |
| 5 | **A deletion landing AFTER the job was queued still holds** | **PROVEN** | probe F2 mutation matrix: bolt 2 blinded → resurrected, clean → not. `test_replay_resurrection.py:246` asserts the outcome but crosses only "is it back", never the queue row | **materialization, at apply** |
| 6 | Bolt 2 is load-bearing rather than decorative | **PROVEN** — new | single-mutation causal delta, §6.1 | materialization |
| 7 | A tombstoned job is retired from the queue | **DISPROVEN** | `skip()` returns `False`; row `in_progress`, attempts 1→8, `skipped` rows = 0 | queue row |
| 8 | A store that has ever deleted anything can be drained | **DISPROVEN** | `drained=False, timed_out=True, remaining=1`, full timeout burned | drain contract |
| 9 | `forget_derived` removes every copy of the content | **DISPROVEN** in outbox mode | by `memory_id`: 1 of 2 copies deleted, survivor returned by `recall()`. Honestly reported as `verified_clean=False` | recall |
| 10 | A forget beats a queued job that has not been drained | **DISPROVEN** | `deleted=0, tombstoned=0`, no tombstone placed, content materializes afterwards | forget entry |
| 11 | `replay()` only enqueues | **PROVEN** | probe 1; `enqueued=1`, memories still 0 | enqueue |
| 12 | No code consumes the outbox and writes `cognitive_memories` | **DISPROVEN** | `reconciliation_worker.py:479 + :113`; memories 0 → 1 | materialization |
| 13 | The dangerous path is unreachable | **DISPROVEN** | reachable from `doctor_cli:229`, `projection_control.drain`, `join_soak.py:124`, the harness, 11 test modules | reachability |
| 14 | No shipped entrypoint executes the consumer | **PROVEN** | exit 2; zero inbound refs; hook 0 refs; ledger table absent on the live store | reachability |
| 15 | Production has ever run the consumer | **DISPROVEN** | `projection_ledger` absent from the live store; `_migrate()` creates it unconditionally | live store |
| 16 | The `_NO_MATERIALISER` tripwire can fire when a consumer appears | **DISPROVEN** | `status != 'done'`; no such status; `False` even on a fully drained queue | test oracle |
| 17 | Gate 5 was correctly recorded NOT_EVALUABLE | **DISPROVEN as to its stated reason** | the consumer existed; gate 5 is evaluable and is evaluated in §6.1 | — |
| 18 | `buried()` fails open across split connections | **OPEN** | nothing constructs a split-file worker today | — |
| 19 | The doctor's `COMPLETED_WITHOUT_PROJECTION` repair can re-offer a tombstoned job | **OPEN** | not probed; diagnostics has zero tombstone awareness | — |
| 20 | Lift-plus-ordinary-cycle is a vulnerability or the intended restore | **OPEN** | owner's design call | — |
| 21 | The other four projection types behave under tombstone | **OPEN** | only `cognitive_memory` and `hippocampus_label` have builders; not probed | — |

---

## 10. Test-level classification

**Component semantics** — proves a function does what its docstring says, on
inputs the test constructs:

- `tests/test_projection_outbox.py`, `test_projection_engine.py`,
  `test_projection_control.py` — queue mechanics, replay reports, pause/resume
- `test_forget_resurrection.py` — replay-only; its two rebuild tests prove
  nothing about materialization and are marked as such, for the wrong reason
- `tests/test_provable_forgetting.py`, the tombstone-audit tests in
  `test_replay_resurrection.py:160–244`

**Production rebuild** — drives the product's own recovery path end to end:

- `tests/test_memory_resurrection.py` (`rebuild_version` + `worker_for`)
- `tests/test_replay_resurrection.py:114–158, :246–274` (`replay` +
  `rebuild_version` + `drain`)
- `tests/test_reconciliation_worker.py`, `test_fault_matrix.py`,
  `benchmarks/reliability/*`

**A green test is evidence only for the boundary it crossed.** The sharpest
example in this repo: `test_a_deletion_after_the_job_was_queued_still_holds`
(`test_replay_resurrection.py:246`) is green, and correctly so — it asserts
`_holding(os_) == 0` and the data does stay deleted. It never inspects the outbox
row. `pytest --durations` shows it consuming **30.11 s** against a 30 s drain
timeout while every sibling test takes 0.02 s. Defect D1 has been paying its own
timeout inside a passing test since 16/08. Same score, different behaviour.

---

## 11. Outbox health on a fresh store

Fresh `MemoryOS`, one `observe()` + one `remember()`, no worker:

| mode | `cognitive_memories` | `shadow_memories` | `projection_outbox` | `projection_ledger` | `memory_tombstones` |
|---|---|---|---|---|---|
| `legacy` | 1 | 0 | `{}` — nothing enqueued | ABSENT | ABSENT |
| `shadow` | 1 | 0 | `{pending: 1}` | ABSENT | ABSENT |
| `outbox` | 1 | 0 | `{pending: 1}` | ABSENT | ABSENT |

`outbox.counts()` → `{'pending': 1, 'in_progress': 0, 'completed': 0,
'dead_letter': 0, 'skipped': 0}`; `lag_seconds()` → `0.0`.

Three things worth stating plainly. **The table is created in every mode** — a
`projection_outbox` present with zero rows means nothing was ever enqueued, not
that the feature is absent; that is exactly the live store's signature.
**`projection_ledger` is absent until a worker is constructed**, which makes it a
reliable one-bit answer to "has this store ever had a worker". And **shadow mode
enqueues into the production outbox**, so `shadow_runner` claiming from it means
a shadow run consumes production jobs and satisfies them with `shadow_memories`
rows — noted, not probed.

---

## 12. INTENDED vs IMPLEMENTED vs EXECUTED

| Layer | Verdict | Evidence |
|---|---|---|
| **INTENDED** | consumer specified before the producer was written | `WRITE_PATH_ANALYSIS.md:178–182`, `9564f66`, 08-04 23:00 |
| **IMPLEMENTED** | present and complete at HEAD | `CognitiveMemoryBuilder:100`, `ReconciliationWorker:180`, `worker_for:532`, ledger schema `:51` |
| **EXECUTED — tests & benchmarks** | **PROVEN executed** | 11 test modules, `harness.py:73`, `verify_single_commit.py:61`, `join_soak.py:124`, and every probe in this document |
| **EXECUTED — operator surface** | **BROKEN** | the documented command exits 2; `doctor_cli` has zero inbound references |
| **EXECUTED — production** | **PROVEN NEVER** | mode pinned to `legacy`; env var unset everywhere; live store: 0 outbox rows, no `projection_ledger`, no `memory_tombstones` |

The gap is between IMPLEMENTED and EXECUTED-in-production, and it is deliberate
at the flag level (`RC1_RELEASE_NOTES.md`: *"Not a cutover. Not production."*)
and accidental at the operator level (a console-script name collision nobody
noticed, and a doctor that recommends the broken command).

---

## 13. Corrections to historical claims

Stated plainly, in order of how much they misled.

**C1.** *"No code in `bio_agent_os/` consumes the outbox and writes
`cognitive_memories`."* — **FALSE.** `reconciliation_worker.py` has done exactly
that since `d42cc65`, 2026-08-04 23:41, twelve days before #7 was accepted on
this premise. The grep that produced the claim searched for a table name in a
codebase that reaches tables through objects.

**C2.** *"The dangerous path is unreachable, which is not the same as safe."* —
**FALSE on the antecedent.** The path is reachable and I executed it. The
conclusion drawn from it was directionally safe — it understated proof rather
than overstating safety — but it is wrong, and it is recorded inside an accepted
decision (`docs/decisions/2026-08-17-accept-seven.md:50–60, 76–93`).

**C3.** *Gate 5, NOT_EVALUABLE, for want of a materializer.* — **The reason is
wrong. Gate 5 is evaluable, and §6.1 evaluates it: bolt 2 is executed,
individually necessary, and mutation-killed.** Whether the gate's final
disposition should change is the owner's call; the recorded justification cannot
stand as written.

**C4.** *`enqueued=5, skipped_tombstoned=0` quoted as evidence a mutant killed
nothing.* — Those are a **mutant's** numbers, not a clean system's. A clean
system on that fixture reports a non-zero `skipped_tombstoned`. The figure was
read as a null result when it was the mutation working.

**C5.** *`tests/test_forget_resurrection.py`'s two rebuild tests are honest
placeholders that will go red when a consumer appears.* — **FALSE twice.** The
consumer already existed, and the oracle tests `status != 'done'` against a
`JobStatus` enum that has no `'done'`, so it can never return `True` on a drained
queue. The tripwire cannot fire in either direction.

**C6.** *`privacy_registry.py:203` — `projection_outbox` is "drained and skipped
on tombstone".* — **Half false.** Tombstoned jobs are neither skipped nor
drained; they are pinned `in_progress` forever.

**C7 — the one claim that needs NO correction.** The 15/08 finding that a replay
resurrected a deleted memory, and the 16/08 claim that tombstones fixed it, were
both measured at the **production rebuild** boundary, through the product's own
worker, with the right positive control. `test_memory_resurrection.py` and
`test_replay_resurrection.py` stand as written. The error is entirely in the
newest file and in what was inferred from it.

---

## 14. Recommended next action

In order. Each is small; none is in this audit's scope to perform.

1. **Fix D1** — `reconciliation_worker.py:335` →
   `self.outbox.skip(job.event_id, job.projection_type, reason="sự kiện đã bị đặt bia mộ")`,
   matching line 418. One line. Add a regression test asserting terminal status
   `skipped` **and** `drain(...)["drained"] is True` after a tombstoned drain —
   the assertion the existing green test never makes.
2. **Repair the tripwire** — `test_forget_resurrection.py`: `_rebuild()` gains a
   `drain`, `_materialised()` drops `'done'` for
   `status NOT IN ('pending','in_progress')` or simply reads `drained`, and both
   `xfail(strict=True)` marks come off. Rewrite the mutant test to blind both
   bolts, or to use the F2 fixture where bolt 2 stands alone.
3. **Amend the ACCEPT #7 record** — correct the gate 5 justification and the
   "unreachable" limitation with §6.1's matrix. Do not silently restate the
   verdict; state what was believed, what is true, and what was measured.
4. **Decide D2 before any cutover.** Outbox mode currently double-writes every
   memory. Either `observe()` in outbox mode must suppress the legacy
   `remember()` write, or the builder must upsert on `projection_key`. Until then
   `verified_clean=False` on a forget-by-id is the *correct* report of a real
   duplicate, and cutover would double the store.
5. **Close D3** — `forget_derived` should consult `projection_outbox` for pending
   jobs on the subject's events and place a tombstone even when no memory row
   exists yet, rather than returning early at `forgetting.py:593`.
6. **Fix the operator surface** — either register `projection` under
   `bio_agent_os.cli:main`, or add a `[project.scripts]` entry for `doctor_cli`,
   or change every runbook and `diagnostics.py:757` to say
   `python -m bio_agent_os.cognitive.doctor_cli`. Today the doctor recommends a
   command that exits 2.
7. **Then, and only then, re-evaluate cutover.** The CONDITIONAL GO of 08-05 was
   conditional on shadow overhead and PostgreSQL; D1–D3 are three more
   conditions, and D1 makes the rollback runbook's own completion gate
   unreachable.

---

## 15. Commits

Measurement and documentation only. No production code was modified by this
audit.

- `docs(audit): projection materialization — the consumer exists, nothing runs it`
  — this file.
- `test(projection): record that bolt 2 is load-bearing and that a tombstoned
  job never retires` — `tests/test_projection_materialization_audit.py`, the
  mutation matrix and the D1 measurement promoted from the scratchpad. The D1
  case is `xfail(strict=True)`: it passes today by failing, and goes red the day
  line 335 is fixed, which is the behaviour a tripwire is supposed to have.

Not pushed. Probe scripts remain in the session scratchpad at
`…/scratchpad/integ/probe{1,2,3,4,5,6}*.py` and are not committed.

---

## 16. Tree

Clean at `44acdc7` when the audit began — `git status --porcelain` empty. The
only files this audit adds are the two listed in §15. The live memory store at
`c:/locaith/Final Platform Agent AI OS Intelligent Tuan Anh/.bio-agent-os/` was
copied for reading and is byte-identical to what it was before.
