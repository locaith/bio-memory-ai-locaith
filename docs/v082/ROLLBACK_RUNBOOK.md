# Rollback runbook

**Version:** v0.8.2-rc1 · **Applies to:** `cognitive_memory` projection only

Rollback is one environment variable. Everything else in this document is about
what happens to the data, and what does **not** happen to it.

---

## The rollback

```bash
BIO_AGENT_PROJECTION_MODE=legacy
```

Restart the process. That is the whole procedure.

No migration runs. Nothing is rewritten. No schema changes. There is no
reverse migration to get wrong, because the outbox never replaced anything —
it was added alongside.

**Legacy is already the default.** With the variable unset, `observe()`
enqueues nothing and behaviour is what shipped in v0.6.1. Tests assert this
directly, including that an unrecognised value falls back to legacy rather
than to something clever.

---

## What survives a rollback

| | |
|---|---|
| Events already committed | **kept** |
| Outbox debt already committed | **kept**, waiting for a replay |
| Projections already built | **kept** |
| Ledger rows | **kept** |
| Shadow projections | **kept** |
| New observations after rollback | owe nothing; legacy path only |

A committed debt is a fact about what was owed at the time. Deleting it on
rollback would destroy the only record that the work was ever due, and would
make a roll-forward silently incomplete. It stays.

The doctor reports the leftover debt as `OUTBOX_PENDING` at **WARN**, not as
corruption. A deliberate rollback is not damage, and a diagnosis that cannot
tell the two apart is not useful during an incident.

---

## Ordered procedure

### 1. Stop claiming new work

```bash
bio-agent-os --db <path> projection pause --reason "rollback to legacy" --operator <you>
```

In-flight jobs finish. The flag is read once at the top of a worker cycle and
never inside one — a projection that has started completes, because an
interrupted transaction is the crash case and there is no reason to create one
on purpose.

Events and their outbox debt keep being written. Nothing is lost by pausing.

### 2. Decide: drain or leave

**Drain** if you want the queue empty before switching, and the queue is
small enough that finishing is quick:

```bash
bio-agent-os --db <path> projection drain --timeout 300
```

Drain overrides the pause deliberately — a paused system that cannot be
drained can only be waited out. It reports what it could not finish rather
than looping; backoff windows, dependencies waiting on a parent, and dead
letters are all legitimate reasons for a queue not to reach zero, and none of
them is resolved by trying harder.

**Leave it** if the queue is deep or something is wrong with the projection
path. The debt is durable and replayable later.

### 3. Switch the flag

```bash
BIO_AGENT_PROJECTION_MODE=legacy
```

Restart producers and workers.

### 4. Confirm

```bash
bio-agent-os --db <path> projection status
bio-agent-os --db <path> doctor --deep
```

Expect:

* `projection_mode` = `legacy`
* new events enqueue nothing
* any pending jobs are the debt from before the rollback
* doctor exits 0, or 1 with `OUTBOX_PENDING` explained by the rollback

### 5. Record it

The pause row keeps `reason`, `operator` and `changed_at`. Note the time of
the switch and the queue depth at that moment — a later roll-forward needs to
know what was outstanding.

---

## Rolling forward again

1. `BIO_AGENT_PROJECTION_MODE=shadow` (or `outbox` for the canary tenants)
2. `bio-agent-os projection resume`
3. Start a worker: `projection worker --forever`
4. The retained debt drains on its own. `already_built` in the worker metrics
   accounts for anything that had in fact been projected before the rollback —
   the ledger prevents a second projection.
5. `doctor --deep` should return to exit 0 with no pending jobs.

Nothing needs replaying by hand. If it does, `projection replay --event-id`
exists and is dry-run by default.

---

## What rollback does not fix

**A corrupt database.** Rollback changes which write path runs; it does not
repair anything. If `doctor` reports `SQLITE_INTEGRITY` CRITICAL, restore from
backup — and first re-run the doctor, because a stale read snapshot on a busy
connection has been observed reporting a false `malformed inverted index`. The
doctor now runs its integrity pragma on a fresh connection for that reason,
but a hand-run `PRAGMA integrity_check` on a long-lived connection can still
mislead.

**A tenant leak.** If `LEDGER_TENANT_MISMATCH` or `SHADOW_TENANT_MISMATCH`
appears, stop and investigate. Rollback leaves the rows exactly where they are
and repair refuses to touch a tenant automatically.

**Data written by the outbox path.** Projections built while outbox mode was
on stay. They are the same projections legacy would have built — 10,000 of
10,000 matched in shadow — but they were built by the new path, and rolling
back does not un-build them.

---

## Timing

| Step | Measured |
|---|---|
| pause takes effect | next worker cycle |
| drain, 10,000 queued jobs, 1 worker | about 11 s |
| flag switch | process restart |
| doctor `--deep`, 366,715 events | 48 s |
| doctor `--incremental` | seconds |

---

## The one thing not to do

Do not remove the legacy path to "clean up" after a successful canary. A
cutover that cannot be undone in one setting is not a cutover, it is a
migration — and this phase has not earned one.
