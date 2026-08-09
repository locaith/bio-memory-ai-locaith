# What the hippocampus actually does — Phase 1

    date          2026-08-09
    commit        535bb6f
    harness       scripts/characterise_hippocampus.py
    corpus        tests/fixtures/hippocampus_corpus.json  (v1, 25 items)
    raw           reports/hippocampus_characterisation.json
    status        COMPLETE — failure behaviour, cost, spread and stability
    verdict       WORTH JOINING, ASYNCHRONOUSLY ONLY.
                  The labels are good. The cost forbids the write path.
                  Three defects found on the way are fixed and pinned by
                  tests/test_hippocampus_label_contract.py (10 tests; 9 of
                  them fail against the pre-fix code).

Reproduce:

    python scripts/characterise_hippocampus.py --offline

Costs nothing, needs no GPU, no model, and takes about two seconds.

---

## 1. Why this exists

The plan is to wire the hippocampus into a write path that took nine canary runs
and nine real defect fixes to make trustworthy. Nobody has ever measured the
hippocampus: no benchmark, no latency figure, no cost figure, no evidence about
the quality of the labels it assigns. Joining an unmeasured component to a
hard-won clean one is backwards, so Phase 1 measures and changes nothing.

Three verdicts were allowed in advance: worth joining, needs fixing first, not
worth joining. This is the second.

---

## 2. Failure behaviour — measured

`label()` is the entry point the join would call. Each row is a stub engine, so
nothing here depends on the model of the day:

    scenario                              ms   logged  returned
    model dead (connection refused)      0.0      yes   importance=5  junk=False
    model slow 30 s, caller waits 2 s  2009.4       no   TIMEOUT AT CALLER
    model says importance 99             5.4       no   importance=99
    model says importance "high"         0.7      yes   importance=5
    model says importance -3             0.3       no   importance=-3
    model omits a required field         0.4      yes   importance=5
    model answers prose, no JSON         0.4      yes   importance=5
    _cheap_label (no model at all)       0.0       no   importance=7  label_pending=True

Three defects, each with a specific consequence.

### 2.1 `importance_score` has no bounds

The schema field is

    importance_score: int = Field(description="Importance from 1 to 10")

The range lives in the *description*, which is prose sent to the model, not a
constraint. Pydantic enforces `int` and nothing else, so 99 and -3 are accepted
and returned unchanged. What the value gates:

    hippocampus.py:523     confidence < 0.60 and importance_score < 8   -> promote a rule
                                                                          into the self-model
    memory_health.py:175   importance_score >= 8                       -> raise reasoning effort

A 99 clears every gate that exists, permanently, and a -3 fails all of them.
Neither is reachable through a well-behaved model, and both are reachable
through a local 12B one asked for a number.

Pydantic does work where a constraint exists: `"high"` is rejected, the missing
field is rejected, prose is rejected. The type check is doing its job. The range
was simply never expressed as a check.

### 2.2 `label()` has no timeout

Thirty seconds of stub delay produced thirty seconds of waiting. The 2,009 ms in
the table is my caller's `wait_for`, not anything inside the function. A model
that hangs hangs the caller, with no upper bound.

This matters more after a join than it does now. On the write path, a call with
no timeout is a write with no upper bound — which is close to the shape of the
defect that ended Run 8: a scheduled TRUNCATE with no time budget held every
writer for 23,236 ms.

### 2.3 The failure fallback is indistinguishable from a real label

When anything goes wrong, `label()` returns

    {"topic": "unknown", "importance_score": 5,
     "is_junk_or_transient": False, "user_state": "unknown"}

A well-formed dict with a mid-scale score and no marker. The only trace is a
line appended to an internal log. A caller that receives this cannot tell a
failed label from a real one, so a stretch where the model is down becomes a
stretch of memories all quietly scored 5 — and later, nothing can identify them
to relabel.

The comparison is inside the same file. `_cheap_label`, the deliberate no-model
path, sets `label_pending: True` precisely so consolidation can find its work
later. The accidental path is less honest than the intentional one, and the fix
is the same one word.

---

## 3. Two places this harness measured itself instead of the code

Recorded because it is the same failure mode as the WAL torture gate that could
not reproduce its own failure, and it is worth being able to recognise.

**Stubbing above the validation.** The first version stubbed
`generate_structured`, which is where `schema.model_validate()` lives. Every
garbage payload sailed through, and the report would have listed defects the
engine actually catches. Fixed by stubbing `generate()` — the raw text — leaving
JSON extraction, the repair retry and validation in the path.

**Stubbing with the wrong backend.** The second version built the engine with
`LLMEngine.from_env()`, which defaults to `gemini`, whose client cannot
construct in this environment. Every scenario then "failed" because the client
was `None`, not because of the payload under test — a uniform column of
plausible results with a single wrong cause. Fixed by naming `ollama`
explicitly, which is also the backend that routes through `_structured_fallback`
where the interesting code is.

Both were caught by a result being *too* uniform to be true.

---

## 4. Cost, spread and stability — measured

12 corpus items, 2 repeats, gemma4:12b local, in batches of 6 with a rest
between them because the machine's power supply has not been replaced.

### 4.1 Cost — the number that decides the architecture

    p50   20,352 ms
    p95   24,751 ms
    max   29,031 ms

The threshold was set in advance, before any number existed, so the result could
not be argued with afterwards: **above a few hundred milliseconds per event, the
hippocampus cannot sit on the write path.** Twenty seconds is that threshold
crossed by roughly two orders of magnitude.

Independently corroborated by the LoCoMo run finishing the same day, where
`ingest_turn()` — which calls `label_and_store()` — cost 39.9-48.2 s per turn
against 0.06-0.09 s for naive-rag. Two different harnesses, same conclusion.

### 4.2 Spread — the scale carries real information

    importance   1  2  2  2  3  |  7  8  8  8  9  9
    items        the five junk  |  the seven worth keeping

Bimodal, not piled on 7-8, which was the specific worry. And the separation is
perfect on this corpus: **12 of 12 correct** on the junk/keep decision, with no
item landing on the wrong side.

The topics are specific rather than generic — "Contract with Binh Minh Company",
"Bank Account Information", "Server Port Configuration", "SQLite WAL behavior".
These are labels something downstream could actually rank on.

    fallback rate   0%   nothing failed, nothing was refused

### 4.3 Stability — drifts, but not across any decision

Ten of twelve items produced an identical label twice. The two that drifted
moved by exactly one point:

    junk-01   importance 1 <-> 2   both far below every gate
    fact-01   importance 8 <-> 9   both above the >= 8 gate

Neither crossed the junk boundary, and neither crossed `importance_score >= 8` —
the gate that promotes a rule into the self-model. So on this corpus the drift
is real and **decision-irrelevant**. That is a claim about twelve items at
temperature 0.1, not a guarantee; an item sitting on 7/8 would flip, and nothing
here says how often that happens.

### 4.4 What this means

The quality question and the cost question have opposite answers, and both are
useful:

    labels      good — accurate, specific, well spread, stable enough
    cost        twenty seconds an event, which forbids the write path

So the hippocampus is worth joining, and the only way it can be joined is the
asynchronous two-phase one. Reproduce:

    python scripts/characterise_hippocampus.py --repeats 2 --batch 6 --rest 12 --limit 12

---

## 5. What this changes in the join plan

Section 2.1 of `hippocampus-join-plan.md` describes adding a
`HIPPOCAMPUS_LABEL` member to `ProjectionType` and giving it a builder. Reading
the worker since then, that shape does not work as written, for a reason
independent of everything above:

    reconciliation_worker.py:357-369
      self._record_ledger(job, target_id=None)     # opens the write transaction
      result = builder.build(event, job, conn)     # builder runs INSIDE it
      ...                                          # worker commits

and the contract on `ProjectionBuilder` requires a builder to be deterministic
and to not commit. A model call is neither cheap nor deterministic. Putting one
there would hold a write transaction open for the duration of an inference —
which is exactly the class of defect the last nine runs were spent removing, and
it would also make replay unable to reproduce what it replayed.

The write path already anticipates this. `_cheap_label` exists, is deterministic,
costs no model call, and marks its output `label_pending: True`. So the join is
two-phase, not one:

    builder     deterministic, cheap, inside the transaction  -> _cheap_label
    consolidate model call, outside any transaction, later    -> the real label

This is not a change of direction — it is the shape `consolidate()` already has.
It does mean §2.1 should be rewritten before any code is written against it.

---

## 6. Verdict

**Worth joining — asynchronously, and only asynchronously.**

Phase 1 allowed three verdicts in advance: worth joining, needs fixing first,
not worth joining. The honest answer is the first two together. The labels are
good enough to be worth having. The cost settles *how* it can be had. And three
defects had to be fixed before any of it went near a write path.

The one thing this does not measure is whether the labels **improve retrieval**
— that is Phase 3, and it is a different experiment. A good label is not the
same as a better answer.

### The three defects, and what was done about them

Three defects, all small, all in the
component about to be put on a write path. All three are now fixed:

    1. ge=1, le=10 on importance_score            hippocampus.py:31
    2. LABEL_TIMEOUT_SECONDS, env-configurable    hippocampus.py, label()
    3. label_pending + label_failed on fallback   hippocampus.py, label()

Re-measured on the same harness:

    scenario                                      before          after
    model says importance 99                      99 stored       falls back, marked
    model says importance -3                      -3 stored       falls back, marked
    model slow 30 s, label()'s own timeout        waited 30 s     stopped at 511 ms
    model dead                                    importance=5    importance=5 + marked
    _cheap_label                                  unchanged       unchanged

Fix 3 reuses the key that already exists rather than inventing one:
`relabel_pending()` selects on `label_pending`, so a failed label now enters the
backlog that consolidation already drains. `label_failed` records why. The
timeout default of 60 s is deliberately generous and provisional — the real
per-event p95 is the half of Phase 1 that has not run yet, and tightening it is
a one-line change to an environment variable.

Pinned by `tests/test_hippocampus_label_contract.py`, 10 tests. Checked against
the pre-fix code by stashing the fix: 9 fail, and the one that passes is the
guard asserting a *valid* score still comes through untouched — which was
already true and which any lazy fix (always return the fallback) would break.
The suite also runs in 0.5 s where it took 30.4 s before, because the hang test
no longer hangs.

None of this was a reason to abandon the join. All of it was a reason not to do
the join first. Phase 1 said measure before joining, and the measuring cost two
seconds of stubs and no GPU at all.
