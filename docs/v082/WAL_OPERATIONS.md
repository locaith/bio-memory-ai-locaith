# WAL operations

**Applies to:** SQLite single-node. **Version:** v0.8.2-rc1

The soak measured the write-ahead log at 500 MB after an hour of continuous
load — 46% of the database — falling to zero only when the last connection
closed. Nothing was lost. Nothing was reclaimed either, and a process that
stays up never would.

---

## Why it grows

A checkpoint copies WAL frames back into the database file. A **passive**
checkpoint can only copy back as far as the oldest reader's snapshot, because
anything newer is what that reader is still reading. With connections
permanently open — and the runtime holds six per process — the checkpointer
rarely gets a moment where no reader is holding a snapshot, so the file grows.

`wal_autocheckpoint` is set to 1000 pages (about 4 MB). It fires often; it just
cannot finish.

This is normal SQLite behaviour, not damage. But disk is finite, and an
operator watching free space has to count the WAL.

---

## Commands

```bash
bio-agent-os --db <path> storage wal-status
bio-agent-os --db <path> storage wal-status --json

bio-agent-os --db <path> storage checkpoint --mode passive
bio-agent-os --db <path> storage checkpoint --mode restart
bio-agent-os --db <path> storage checkpoint --mode truncate --maintenance
```

`--timeout-ms` (default 5000) bounds how long a blocking mode waits for readers
before reporting `busy`. The runtime's own `busy_timeout` is 30 seconds, which
is right for an append and far too long for someone holding a terminal.

### Exit codes for `wal-status`

| Code | Meaning |
|---|---|
| 0 | below the soft limit |
| 1 | above the soft limit |
| 2 | above the hard limit |

`checkpoint` exits 1 when a reader blocked it, 0 otherwise.

---

## The four modes

| Mode | Waits for readers | Shrinks the file | Use |
|---|:---:|:---:|---|
| `PASSIVE` | no | no | the automatic path; safe at any time |
| `FULL` | writers only | no | rarely needed directly |
| `RESTART` | yes | no | the file stops growing; next writer starts a new WAL |
| `TRUNCATE` | yes | **yes** | maintenance only, after readers are drained |

**TRUNCATE is never automatic.** It waits for every reader, and a background
job that waits on a reader is a background job that stalls the process it runs
in. A test asserts no automatic path ever selects it.

---

## The automatic policy

Runs between worker cycles, never on the append path, and never inside a batch.

```
below soft limit (256 MB)   PASSIVE
above soft limit            PASSIVE, and a warning
above hard limit (512 MB)   RESTART if no reader is registered
                            otherwise PASSIVE, and a critical warning
```

Configuration:

```bash
BIO_AGENT_WAL_SOFT_LIMIT_MB=256
BIO_AGENT_WAL_HARD_LIMIT_MB=512
BIO_AGENT_WAL_CHECKPOINT_INTERVAL_SECONDS=60
```

An unparsable value logs a warning and falls back to the default rather than
crashing the runtime.

---

## Alerts

| Code | Severity | Fires when | Do this |
|---|---|---|---|
| `WAL_ABOVE_SOFT_LIMIT` | WARN | WAL ≥ 256 MB | passive checkpointing continues; look for a long-lived reader |
| `WAL_ABOVE_HARD_LIMIT` | CRITICAL | WAL ≥ 512 MB | `checkpoint --mode restart`, or truncate in maintenance once drained |
| `WAL_CHECKPOINT_REPEATEDLY_BLOCKED` | WARN | 5 consecutive busy checkpoints | a long-lived read snapshot is preventing reclamation |
| `NOT_IN_WAL_MODE` | INFO | journal mode is not WAL | checkpointing does not apply |

---

## Reader age is reported only when it is known

SQLite has no API for "how old is the oldest reader". The manager reports:

* `busy` — SQLite's own answer, the ground truth for "a reader blocked this";
* `consecutive_busy_checkpoints` — how long that has been true;
* `oldest_reader_age_seconds` — **only** for readers registered with
  `note_reader()`, and **`None`** when nothing has registered.

`None`, not `0`. A zero would read as "no reader is holding anything back",
which is the opposite of "we are not tracking readers".

---

## Runbook

### The WAL is above the soft limit

Expected under sustained load. Confirm the queue is healthy
(`projection status`) and that the file is not still climbing. No action.

### The WAL is above the hard limit

1. `storage wal-status` — check `consecutive_busy_checkpoints`.
2. If 0, the automatic RESTART should already be reclaiming. Re-check in a
   minute.
3. If non-zero, something holds a long read. Find it: a long-running report, a
   backup, an interactive session, an incremental doctor on a very large
   database.
4. Once it is gone: `storage checkpoint --mode restart`.
5. To actually shrink the file, drain the runtime and
   `storage checkpoint --mode truncate --maintenance`.

### Checkpoints keep coming back busy

A reader is pinned. Python's `sqlite3` leaves a read transaction open after a
`SELECT` until the next `commit()` or `rollback()`, so a connection that read
something and then went idle **is** a long-lived reader. That is the usual
cause, and closing or committing on that connection releases it.

### Disk is running out

The WAL and the database are separate files. Budget for the database plus
roughly half again while a process is long-lived. The WAL returns to zero when
the last connection closes, so a planned restart reclaims it.

---

## What has been measured

| | |
|---|---:|
| WAL after 1 hour of continuous load (366,715 events) | 500 MB |
| as a share of the database | 46% |
| after the last connection closed | **0** |
| events lost | **0** |
| `integrity_check` afterwards | **ok** |

Tests cover: the log grows with writes; passive reclaims and loses nothing;
truncate empties the file; the data survives a reopen; blocking modes are
refused unless asked for; a live reader blocks truncate and is reported; five
consecutive blocks raise the alert; the policy never truncates and never
restarts while a reader is registered; the worker still drains afterwards;
interleaved checkpoints leave the ledger intact; tenant isolation survives; and
a process killed with `os._exit` mid-checkpoint leaves `integrity_check` ok
with every event readable.
