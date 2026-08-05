# Shadow mode — cognitive_memory

**Date:** 2026-08-05 · **Branch:** `feat/v082-reliability-kernel`
**Scope:** `cognitive_memory` only. The other four projection types have no
builder and are reported **unsupported**, not passed.

The legacy write path works and people depend on it. The outbox path is
believed equivalent, and belief is not evidence. Shadow mode runs both from one
canonical input and compares the results, with shadow output kept where
production cannot read it.

---

## Modes

```
BIO_AGENT_PROJECTION_MODE=legacy   (default)
BIO_AGENT_PROJECTION_MODE=shadow
BIO_AGENT_PROJECTION_MODE=outbox   (code path only; never the default)
```

An unrecognised value falls back to **legacy**. A typo in an environment
variable must not quietly change how memories are written.

| Mode | `observe()` behaviour | Jobs enqueued |
|---|---|---|
| legacy | exactly as shipped | 0 |
| shadow | unchanged, plus one atomic job | 1 × `cognitive_memory` |
| outbox | test and benchmark only | — |

---

## Capability registry

Nothing assumes a projection type is ready.

| Type | Status | Shadow | Replayable | Store |
|---|---|---|---|---|
| `cognitive_memory` | **supported** | yes | yes | memories |
| `self_model_update` | unsupported | no | no | — |
| `prospective_memory` | unsupported | no | no | — |
| `context_block` | unsupported | no | no | — |
| `checkpoint_reference` | unsupported | no | no | — |

Two consequences, both deliberate:

- **Nothing is enqueued for a type with no builder.** Creating a job that can
  only dead-letter turns a missing capability into runtime noise.
- **`replayable` is False for every unsupported type**, whatever the dependency
  registry declares about its semantics. A type that cannot be built cannot be
  rebuilt.

---

## Isolation

Shadow projections live in their own table, `shadow_memories`. A flag on
`cognitive_memories` would have been smaller, and one forgotten `WHERE` away
from leaking shadow rows into a user's recall. No amount of care makes that
risk go away; a separate table does.

Each row carries `source_event_id`, `legacy_memory_id`, `shadow_projection_key`,
`projection_version`, `tenant_id`, `workspace_id`, `canonical_input_hash`,
`output_hash`, `comparison_status`, `created_at`.

Shadow keys are hashed in a **different key space** (`shadow\x1f…`) from
production projection keys. Sharing the space would let a shadow ledger row
satisfy a production job's idempotency check, and the two paths would stop
being independent.

Verified: production table count unchanged, production recall returns no shadow
row, legacy memory version not bumped, tenant and workspace preserved.

---

## Canonical input

Both paths derive from `CanonicalObservation`, reconstructed from the immutable
event rather than captured alongside it — so a replay months later derives
exactly the same thing from exactly the same row.

```
event_id · tenant_id · workspace_id · content · metadata · epistemic_status
source · observed_at · effective_at · security_label · provenance
```

`input_hash()` is a SHA-256 over the canonical JSON. `projected_fields()` is a
pure function of it: no clock, no randomness, no model call. That is what makes
a difference in the comparator a real signal rather than noise.

---

## Comparator

Semantic, field by field — a whole-blob JSON diff would fail on key order and
pass on a swapped tenant, which is precisely backwards.

Compared: `content`, `memory_type`, `epistemic_status`, `tenant_id`,
`workspace_id`, `confidence`, `valid_from`, `valid_to`, `security_label`,
`source_event_id`, `projection_version`, `canonical_input_hash`.

Fifteen outcome classes, including `MISSING_LEGACY`, `MISSING_SHADOW`,
`TENANT_MISMATCH`, `SECURITY_MISMATCH`, `PROVENANCE_MISMATCH`,
`TEMPORAL_MISMATCH`, `NON_DETERMINISTIC_OUTPUT` and `BUILDER_UNSUPPORTED`.

### Normalisation rules — the complete list

`MATCH_NORMALIZED` is only reachable through these three:

1. Unicode NFC composition
2. leading and trailing whitespace removed
3. internal whitespace runs collapsed to a single space

Anything else is a real difference and is reported as `CONTENT_MISMATCH`. The
1,000-observation run produced **zero** normalised matches — every match was
exact.

---

## Results — 1,000 observations

```
benchmarks/shadow_comparison_run.py --observations 1000 --seed 20260805
```

Four tenants, four workspaces, eight content shapes including Vietnamese,
Japanese, accented Latin, multi-line text, ragged whitespace and a 400-character
string. The runtime is **closed and reopened** before draining, so the worker
recovers a queue it did not create.

| Metric | Value |
|---|---:|
| observations | 1,000 |
| legacy projections | 1,000 |
| shadow projections | 1,000 |
| comparisons | 1,000 |
| **MATCH** | **1,000 (100.00%)** |
| MATCH_NORMALIZED | 0 |
| every other mismatch class | **0** |
| non-deterministic outputs | 0 (100 sampled × 10 rebuilds) |
| drain time | 0.74 s (1,344 obs/sec) |

| Latency | p50 | p95 | p99 |
|---|---:|---:|---:|
| legacy observe (baseline) | 0.298 ms | 0.566 ms | 8.075 ms |
| shadow observe | 0.413 ms | 0.868 ms | 7.656 ms |

**Overhead: +0.115 ms at p50** — the cost of one extra INSERT inside the event
transaction. Relative that is +38.5%, which sounds large and is: the baseline is
sub-millisecond, so the absolute figure is the one that matters operationally.

Raw results: `reports/shadow_comparison.json`, `reports/shadow_comparison.md`.

---

## Bug found by the crash test

`ShadowMemoryBuilder` called `put(commit=False)`. The shadow row was therefore
not durable when the `AFTER_PROJECTION_COMMIT` fault point fired — it was being
committed incidentally by a later `target_id` update. Atomicity by accident.

The process-level crash test caught it: the child died with the shadow row
uncommitted and nothing survived. The builder now commits, so the ledger row
(inserted uncommitted by the worker moments earlier on the same connection) and
the shadow projection become durable together — the same guarantee the
production path has.

---

## Against the acceptance bar

| Criterion | Result |
|---|---|
| 0 missing legacy | ✅ |
| 0 missing shadow | ✅ |
| 0 duplicate shadow projection | ✅ (10× replay, 2 concurrent workers) |
| 0 tenant mismatch | ✅ |
| 0 workspace mismatch | ✅ |
| 0 security mismatch | ✅ |
| 0 provenance mismatch | ✅ |
| 0 unexplained content mismatch | ✅ |
| 0 non-deterministic output | ✅ |
| 100% crash recovery | ✅ (process-level, `AFTER_PROJECTION_COMMIT`) |
| 100% checksum integrity | ✅ |

**Shadow mode for `cognitive_memory` meets the bar.**

---

## What this does not say

- **Four of five projection types are untested**, because they cannot be built.
  `self_model_update`, `prospective_memory`, `context_block` and
  `checkpoint_reference` appear in every report as `unsupported`. They are
  scheduled for v0.8.3 and nothing here should be read as covering them.
- The run is **single-node, single-process, 1,000 observations, under three
  seconds**. It is not a soak test, not multi-machine, and not a statement
  about sustained load.
- Shadow mode has never been enabled outside tests and benchmarks. The default
  is legacy and stays legacy.
- The legacy overhead figure comes from a 300-observation baseline on the same
  machine in the same run; it is indicative, not a controlled benchmark.
