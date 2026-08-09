# Does the temporal advantage still reproduce?

    date        2026-08-09
    run         11:17:09 -> 23:30:13, 12.22 hours
    report      benchmark_reports/locomo_temporal_reconfirm_embed_2026_08_09.md
    baseline    benchmark_reports/locomo_modelcmp_gemma4_12b.md  (2026-06-13)
    model       gemma4:12b via Ollama, local
    answer      YES — and by a wider margin, on a harder question set

---

## 1. The result

    temporal F1        naive-rag    bio-memory    advantage
    2026-06-13            0.4156        0.6034         +45%
    2026-08-09            0.3431        0.5643         +64%

    exact match        naive-rag    bio-memory
    2026-08-09            0.0678        0.1525        2.2x

The claim worth defending — that this memory is better at reasoning about
*time* — holds on today's code. Time is exactly what a biological memory should
be better at, and it is where this one wins.

## 2. What is and is not comparable

The two runs are **not** the same measurement, and the absolute numbers should
not be read as "0.603 fell to 0.564".

    2026-06-13     3 conversations   34 temporal questions   sleep_every = 0
    2026-08-09     5 conversations   59 temporal questions   sleep_every = 20

Today's set adds conv-42 and conv-43, so only 34 of 59 questions are shared, and
June ran with consolidation **disabled** while today ran it every 20 turns.
Different questions and a different memory configuration.

What *is* comparable is each run against its own baseline on its own questions,
and that comparison is the one in §1: the gap held and widened. Both systems
scored lower today (naive-rag fell 0.416 -> 0.343 as well), which is what a
harder question set looks like when it moves both arms in the same direction.

## 3. One thing this run cannot prove about itself

It does not record its embedding configuration. The process launched before the
commit that added that (`ca462a9`), so the report carries only `backend` and
`model` — the same gap that makes the June reports uncheckable.

The evidence that embeddings were live is indirect but real: Ollama held
`nomic-embed-text` resident throughout with its keep-alive being *refreshed*
(expiry moved 11:29 -> 11:48 while the run progressed), which only happens if
something is calling it. The first attempt at this run was killed precisely
because it announced `semantic search is DEGRADED (hash mode, near-random
recall)`, and this one did not.

So: strong, and still not the same as recorded. From the next run on it will be
in the file.

---

## 4. The number that decides the architecture

This is the more consequential finding, and it arrived by accident — the
benchmark answered the question Phase 1 of the join plan was built to ask.

    ingest cost, same turns        naive-rag        bio-memory
    2026-06-13, 609 turns             28.0 s        29,360.6 s
    2026-08-09, 1,024 turns           57.2 s        40,820.0 s

    per turn                       0.06-0.09 s        39.9-48.2 s

Roughly **500 to 800 times** slower, because `BioMemorySystem.ingest_turn()`
calls `hippo.label_and_store()`, and that is a model call per turn.

Phase 1 set the threshold in advance, before any number existed, so it could not
be argued with afterwards:

> above a few hundred milliseconds per event, the hippocampus **cannot** sit on
> the write path and must run asynchronously through the outbox

The measurement is tens of seconds per event. The threshold is not merely
crossed, it is crossed by two orders of magnitude, and the conclusion does not
depend on which part dominates — label, compaction, embedding or consolidation.

That settles the design question the join plan was waiting on:

    in the write transaction   deterministic, no model   -> _cheap_label
    outside it, later          the model call            -> consolidate()

which is the correction already made to §2.1 of the join plan, now with a
measured number behind it rather than an argument from the builder contract.

---

## 5. Still owed

The per-event label latency here is an average over a whole ingest path. The
Phase 1 harness measures `label()` alone, with repeats, and also answers what
this run cannot: whether the labels are *stable*, and whether
`importance_score` is distributed across its range or piles onto 7-8. The GPU
is free now.

    python scripts/characterise_hippocampus.py --repeats 2 --batch 6 --rest 15
