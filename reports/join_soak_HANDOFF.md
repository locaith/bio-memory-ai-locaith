# Join soak — what is running, and how to judge it

    launched     2026-08-10 07:29 (Mon)
    deadline     2026-08-11 ~19:30 (Tue evening), 36 hours
    pid          30596, detached — survives this session ending
    script       scripts/join_soak.py
    results      reports/join_soak.json      (rewritten every cycle)
    log          .staging/join_soak.log      .staging/join_soak.err
    commit       3203329
    model        NONE. Nothing GPU-bound runs unattended: the PSU has not been
                 replaced and there have been three hard power-offs.

Check it is alive:

    Get-Process -Id 30596

If it died early, `reports/join_soak.json` still holds every completed cycle —
it is written after each one, so partial results are real results.

---

## The criteria, fixed before the data exists

Section 2.2 of the join plan sets a bar about **not getting worse**. Writing the
thresholds down now, while the answer is unknown, is the only way they can mean
anything on Tuesday.

    PASS requires all four

    1. observe p95      joined arm no worse than baseline by more than 0.10 ms
                        on the median cycle, and both under the 1.0 ms SLO
    2. observe max      joined arm's median-cycle max no worse than 1.5x baseline
    3. WAL peak         joined arm within 10% of baseline
    4. failures         0 dead-lettered, 0 failed, across every cycle

    FAIL on any of

    - a label job dead-lettering at all
    - p95 crossing 1.0 ms in the joined arm while baseline stays under
    - WAL in the joined arm growing without bound across cycles

Judge on the **median across cycles**, not the mean and not the worst. A 36-hour
run on a desktop that is also doing other things will have outlier cycles, and
picking the worst cycle would fail a healthy system while picking the best would
pass a sick one.

Early signal from the first cycles — encouraging, and not yet evidence:

    p95   base 0.31-0.35  ->  joined 0.40-0.41 ms
    max   base 22-130     ->  joined 16-26 ms

The p95 gap is about 0.06 ms, which is the cost of one extra outbox row per
observation. Whether it holds over thousands of cycles is the question the run
exists to answer.

---

## Reading the result

    python -c "
    import json, statistics
    d = json.load(open('reports/join_soak.json', encoding='utf-8'))
    arms = {'baseline': [], 'joined': []}
    fails = 0
    for c in d['cycles']:
        for a in c['arms']:
            if 'observe_ms' not in a:
                fails += 1; continue
            arms[a['arm']].append(a)
    for name, rows in arms.items():
        p95 = statistics.median(r['observe_ms']['p95'] for r in rows)
        mx  = statistics.median(r['observe_ms']['max'] for r in rows)
        wal = statistics.median(r['wal_peak_bytes'] for r in rows)
        dl  = sum(r.get('dead_lettered', 0) for r in rows)
        fl  = sum(r.get('failed', 0) for r in rows)
        print(f'{name:9} cycles={len(rows):5} p95={p95:.3f} max={mx:.2f} '
              f'wal={wal/1e6:.1f}MB dead={dl} failed={fl}')
    print('errored arms:', fails)
    "

---

## What this run does NOT answer

Three things, and none of them should be quietly claimed on Tuesday:

**The model half is unmeasured on the write path.** By design — it is not on the
write path. The upgrader that turns `label_pending = 1` rows into real labels
does not exist yet. That is the next piece of code.

**Whether labels improve retrieval.** That is Phase 3, a different experiment.
Phase 1 showed the labels are *good* (12/12 on junk vs keep, bimodal importance,
specific topics). A good label is not the same as a better answer, and nothing
so far tests the second.

**Behaviour at canary scale.** This is a single writer. The canary programme
closed because five concurrent writers is not the shape that ships — one process
is. This soak measures the shape that ships.

---

## Next, in order

    1. read the soak against the four criteria above
    2. build the upgrader: drain label_pending in batches, model call OUTSIDE
       any transaction, bounded batch size, hard timeout already in place
       (BIO_HIPPOCAMPUS_LABEL_TIMEOUT_S, currently 60 s and provisional —
       Phase 1 measured p95 = 24.8 s, so ~40 s is the defensible value)
    3. Phase 3: does ranking with labels beat ranking without? LoCoMo as the
       external set, real Locaith questions as the internal one
    4. only then: RC2 packaging

Phase 1 report: `reports/hippocampus_characterisation.md`
LoCoMo re-confirmation: `reports/locomo_temporal_reconfirm_2026_08_09.md`
Plan: platform repo, `docs/architecture/hippocampus-join-plan.md`
