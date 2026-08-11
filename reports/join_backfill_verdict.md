# Labels found by a join — verdict

    date        2026-08-11
    A/A         67 cycles   baseline against itself, to learn what "no
                            difference" looks like on this machine
    A/B         77 cycles   baseline against baseline + backfill
    events      4,000 per arm per cycle, 308,000 labels enqueued, 0 left over
    model       none in this measurement
    verdict     PASS on all four criteria

---

## 1. The number that matters

    delta in observe p95            median      |delta| p90
      A/A — genuinely identical     +0.001 ms      0.036 ms
      this design                   -0.001 ms      0.034 ms
      previous design (36 h)        +0.196 ms         —

The new design's difference from baseline is not merely small. It is
**indistinguishable from the difference between baseline and itself** — same
median to three decimal places, same spread. That is what "`observe()` is
unchanged" is supposed to look like, and the A/A run is what makes it possible
to say so rather than assert it.

    baseline p95   0.301 ms  ->  with labels   0.299 ms

## 2. Scored

    1. |median delta| <= 0.020 ms          0.001            PASS
    2. max <= 1.5x baseline                18.72 vs 25.26   PASS
    3. WAL within 10%                      0.0%             PASS
    4. zero dead-lettered, zero failed     0                PASS

    backfill        0.12 s per 4,000 events
    enqueued        308,000 labels
    left unlabelled 0

The 0.020 ms threshold was derived before this run, from the A/A: five times the
measured noise. Not a round number chosen for looking strict — which is exactly
what went wrong with the 0.10 ms bar the previous design failed.

## 3. Two mistakes made and caught during this measurement

**A verdict on three cycles.** The first read of this A/B reported FAIL on
criterion 1 and 2. The run had been going for 77 seconds. The waiter used
`pgrep -f`, which does not see Windows process command lines from Git-bash, so
it reported "finished" almost immediately. What caught it was the number being
absurd on its face — the A/A produced 50 cycles in the same wall-clock time, so
3 could not be right. Fixed by waiting on `ended_at`, a field the script itself
writes when it is genuinely done, rather than inferring from a process table.

Had that reading been believed, the response would have been to redesign
something that was not broken.

**A diagnosis that was nearly wrong.** After seeing the noise floor, the
temptation was to say the failed 0.10 ms bar had been "below the noise". It was
not: noise in the median is 0.001-0.004 ms, so a 0.10 ms bar on the median is
about 25x the noise, and the +0.196 ms effect it caught was real and clearly
detectable. The measurement was good; the *requirement* was impossible. Those
are different failures, and confusing them leads to loosening thresholds instead
of fixing designs.

## 4. What changed, and why it was worth changing

Labels are no longer enqueued inside `observe()`. They are discovered:

    SELECT e.event_id FROM cognitive_events e
    LEFT JOIN projection_outbox o
           ON o.event_id = e.event_id AND o.projection_type = 'hippocampus_label'
    WHERE o.event_id IS NULL

The outbox still carries the job once enqueued, so leases, retries and
dead-letter accounting are all intact. What moved is *when* the enqueue happens:
once per batch, off the hot path, instead of once per observation while someone
is waiting.

An advantage that a queue could never have had: every memory already sitting in
a user's database was written before anything enqueued labels. A queue cannot
deliver work that was never put into it. A join sees those rows because they
exist. The 33 memories in the live database will be labelled by the same code
that labels the next 33,000.

## 5. The model half now exists too

`bio_agent_os/background_jobs/label_upgrader.py`, with the three boundaries from
section 2.3 of the plan finally enforced in code rather than described in prose:

    never inside a transaction     one commit per batch
    never a cloud model            RemoteLabellingRefused unless a human
                                   sets BIO_HIPPOCAMPUS_ALLOW_REMOTE_LABELS=1
    never a mismatched label       pair by index, drop the remainder, and
                                   leave a failed batch pending rather than
                                   stamping it done

Ten tests, including the one that matters most: a short batch must not shift
labels onto the wrong rows, because a confident label on the wrong memory is
worse than no label.

**Not yet run against a real model.** The upgrader is tested with stubs. Running
it on gemma4:12b costs ~20 s per label and the machine's power supply has not
been replaced, so that is a supervised, bounded run — not something to start and
walk away from.

    631 passed, 1 skipped.
