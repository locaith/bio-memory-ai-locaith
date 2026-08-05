# Write path analysis — the state before v0.8.2

**Date:** 2026-08-04
**Commit analysed:** `9f940a6` (v0.8.1a1, main)
**Rule for this document:** no code was changed to produce it. Every figure
below was measured on the repository as it stands.

---

## 1. Baseline

```
.venv/Scripts/python.exe -m pytest -q
```

| Metric | Value |
|---|---:|
| tests passed | 225 |
| skipped | 1 |
| failed | 0 |
| suite wall time | 17.7 s |
| process wall time | 19.4 s |

Downstream dependant (Locaith Intelligence OS): 254 tests, passing against the
0.8.1a1 wheel.

---

## 2. Connection topology

`MemoryOS.__init__` opens **six independent SQLite connections to the same
database file**:

| Component | Class | Own connection |
|---|---|---|
| `self.events` | `SQLiteEventStore` | yes |
| `self.memories` | `SQLiteMemoryStore` | yes |
| `self.self_model` | `SelfModel` | yes |
| `self.prospective` | `ProspectiveMemory` | yes |
| `self.context_blocks` | `ContextBlockStore` | yes |
| `self.checkpoints` | `CheckpointManager` | yes |

Verified at runtime — all six object ids differ, and
`m.events.conn is m.memories.conn` is `False`.

**This is wider than the gap the v0.8.1 report describes.** That report names
one split, between the event store and the memory projection. There are in
fact six independent transaction scopes, so any invariant spanning two of these
components has the same exposure — including context blocks that reference a
memory, and checkpoints that reference a memory version.

A second consequence, visible immediately in testing: with `:memory:` each
connection gets its **own private database**. The six components are not
merely in separate transactions, they are in separate databases. Only a
file-backed path makes them share storage at all.

---

## 3. The write path, step by step

```
caller
  │
  ├─ MemoryOS.observe(...)
  │    ├─ immune.inspect(content)                    in-process, no I/O
  │    ├─ EventRecord(...) constructed               checksum computed
  │    ├─ events.conn: INSERT INTO cognitive_events
  │    └─ events.conn: COMMIT                        ◀── TRANSACTION 1 ENDS
  │
  │  ╔═══════════════════════════════════════════════════════════════╗
  │  ║  CRASH WINDOW                                                 ║
  │  ║  Event is durable. No projection exists. Nothing records      ║
  │  ║  that one is owed. On restart the system cannot tell this     ║
  │  ║  event from one that was never meant to be projected.         ║
  │  ╚═══════════════════════════════════════════════════════════════╝
  │
  └─ MemoryOS.remember(event=..., ...)
       ├─ immune.inspect(content, persistent=True)   may quarantine → returns
       ├─ governance.validate_promotion(memory)
       ├─ memories.conn: INSERT INTO cognitive_memories
       ├─ memories.conn: INSERT INTO cognitive_memory_fts
       └─ memories.conn: COMMIT                      ◀── TRANSACTION 2 ENDS
              │
              └─ retrieval visibility begins here
```

Measured directly:

```
1. observe()   -> event 9107dbd2 COMMITTED (transaction 1)
   state now: events=1  memories=0     <-- crash here leaves an orphan
2. remember()  -> projection COMMITTED (transaction 2), memories=1
```

### Commit counts per method

| Method | `commit()` calls |
|---|---:|
| `SQLiteEventStore.append` | 1 |
| `SQLiteEventStore.append_many` | 1 (batch is atomic within itself) |
| `SQLiteMemoryStore.put` | 1 |

---

## 4. What is currently guaranteed, and what is not

### Holds today

- **Events are immutable.** `cognitive_events` carries `BEFORE UPDATE` and
  `BEFORE DELETE` triggers that raise `ABORT`. No code path can rewrite
  history.
- **Event integrity is checkable.** Every row stores a SHA-256 over its
  canonical form; `verify_chain` recomputes it.
- **A batch append is atomic.** `append_many` uses one `executemany` and one
  commit.
- **Projections are versioned, never overwritten.** `supersede` closes the old
  version and inserts a new row.

### Does not hold

1. **Event and projection are not one atomic unit.** Two connections, two
   commits, an unguarded window between them.
2. **Nothing records that a projection is owed.** There is no queue, no outbox,
   no pending marker. An orphaned event is indistinguishable from an event that
   was deliberately never projected — and `observe()` without a following
   `remember()` is a legitimate, supported call.
3. **There is no idempotency key.** `source_event_ids_json` is a JSON array
   matched with `LIKE '%event_id%'`. Nothing prevents the same event producing
   two projections; nothing makes a retry converge.
4. **Orphans are undetectable without a full scan.** The only way to find them
   is a `LIKE` scan of every memory row per event — O(events × memories), with
   no index that helps.
5. **The exposure is six-way, not two-way.** Context blocks and checkpoints
   reference memories across their own connection boundaries.

---

## 5. Why "no data loss" is true but insufficient

The v0.8.1 crash-recovery test kills a writer and confirms integrity: 125
events, 125 memory rows, parity true, checksum chain valid. That result is
real, and it stays real — the raw event is never lost.

But the test kills the process **between batches**, not inside the window
above. The window is bounded by two commits on two connections, and no test
currently lands inside it. So the guarantee that has been demonstrated is
*durability of what was written*, not *completeness of what was owed*.

An orphaned event is not corruption. It is a silent omission: the memory the
user believes was stored is simply not retrievable, and no check reports it.
That is the failure mode worth fixing, and it is why the fix is a queue rather
than a wider transaction.

---

## 6. Why a shared transaction is not the answer

The obvious fix — give both stores one connection and one commit — was
considered and rejected:

- It couples six components into one transaction scope, so any slow projection
  holds the event write open. Event append is the hot path.
- It does not survive the components being split across processes or machines,
  which is the stated direction (PostgreSQL, distributed workers).
- It breaks the case where `observe()` legitimately has no projection.
- It would change the public constructor contract of every store.

A **transactional outbox** keeps event append fast and atomic with its own
intent record, and lets projection happen afterwards, retryably, in a way that
survives both a crash and a move to another process.

---

## 7. What v0.8.2 must add

| # | Requirement | Why this analysis demands it |
|---|---|---|
| 1 | `projection_outbox` written in the **same transaction** as the event | closes §4.1 and §4.2 |
| 2 | Deterministic projection key `SHA256(event_id + type + version)` | closes §4.3 |
| 3 | `UNIQUE(event_id, projection_type, projection_version)` | makes retry converge |
| 4 | Replay engine reading from immutable events | recovers pre-existing orphans |
| 5 | Reconciliation worker with lease, backoff, dead-letter | drains the outbox safely |
| 6 | Fault injection at each of the six boundaries | proves it, rather than asserting it |
| 7 | `doctor --deep` orphan and duplicate checks | makes omission visible |

The event store keeps its own connection for append. The outbox row must be
written **on that same connection, inside that same transaction** — which
means the outbox table belongs with the event store, not the projection store.

---

## 8. Constraints this work operates under

- No module is deleted, moved or rewritten without a demonstrated need.
- The public API does not change; 225 existing tests must stay green.
- Raw events are never deleted, under any circumstance including repair.
- SQLite stays supported for local development, tests and embedded use.
- No claim of distributed production is made from a single-host run.
