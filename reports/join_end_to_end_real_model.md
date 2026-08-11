# The join, end to end, with a real model

    date     2026-08-11
    model    gemma4:12b, local, via Ollama
    path     observe -> backfill(LEFT JOIN) -> worker -> cheap label
             -> LabelUpgrader -> real label
    result   6 of 6 correct, and the model corrected two cheap-label mistakes

First time the whole two-phase join has run against a real model rather than
stubs.

## What each phase produced

    content                              cheap (no model)      model (gemma4:12b)
    "oke em"                             3  junk               1  junk  Acknowledgment
    contract, 84,000,000 VND             7                     9  Contract Information
    "ports 8899 / 8000, don't kill"      7                     8  Server Configuration
    "cam on nhe"                         5  NOT junk   <-- wrong
                                                              1  junk  Gratitude
    "revenue 9,960,000, debt 12,500,000" 7                     9  Financial Status
    "don't touch drive D, it is dying"   5          <-- too low
                                                              8  Hardware Warning

The two rows marked wrong are the point of having a model at all. The cheap
labeller scores by "does it contain digits", so a thank-you with no digits and a
serious hardware warning with no digits both land on 5. The model separates
them: 1 and junk for one, 8 for the other.

Nothing was lost in between. The cheap label held the place, marked
`label_pending = 1`, and the upgrader found it by that marker.

## Cost — three times cheaper than Phase 1 reported

    Phase 1, one label per call    20.4 s per label
    batched, six per call           6.4 s per label

Phase 1 measured `label()`, which is one event per model call. The upgrader
batches, so one call covers the whole batch. That is a real reduction and it
changes what is affordable: labelling the 33 memories in the live database is
about three and a half minutes, not eleven.

It does not change the architecture conclusion. 6.4 s is still four orders of
magnitude above what a write path can hold, which is why the model runs here and
not there.

## Not claimed

Six items on one machine with one model. It shows the pipeline works and the
labels are sane; it is not a measurement of label quality at scale — that was
Phase 1 (12/12, bimodal, stable) — and it says nothing about whether labels
improve retrieval, which is Phase 3 and still unanswered.

    dead-lettered 0    failed 0    upgrade failures 0
