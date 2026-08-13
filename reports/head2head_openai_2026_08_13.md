# Head to head with mem0 — the first clean number

    date        2026-08-13
    benchmark   LoCoMo, 5 conversations, 10 sessions, 150 questions, all categories
    answering   gpt-4o-mini for every system
    embedding   text-embedding-3-small for every system
    cost        $1.6206
    contamination  none — mem0 logged zero extraction failures

The point of running on gpt-4o-mini is that it is the model mem0 publishes its
own LoCoMo numbers with. Same model, same questions, same answering engine, same
embedder: the only thing that differs is the memory.

---

## 1. Result

    system        overall   temporal   single-hop   multi-hop   open-domain
    naive-rag      0.3970     0.3773       0.5310      0.3929        0.0723
    bio-memory     0.4424     0.5008       0.5508      0.3691        0.0634
    mem0           0.3958     0.4392       0.4284      0.4191        0.1223

**Overall: 0.4424 against mem0's 0.3958 — 11.8% ahead.**

Where the advantage actually lives:

    temporal      0.5008 vs 0.4392   +14% over mem0, +33% over plain RAG
    single-hop    0.5508 vs 0.4284   +29% over mem0

Temporal is the claim this project has been making since June, and it now holds
against a competitor rather than against a baseline.

## 2. Where it loses

Both losses are real and neither is new.

**multi-hop 0.3691** — behind mem0 (0.4191) and behind plain RAG (0.3929).
Questions that need several memories joined together are a genuine weakness, and
this is the second measurement two months apart to say so. June measured 0.405
against naive-rag's 0.552 on the same category.

**open-domain 0.0634** — behind mem0 (0.1223), last of the three. All three
systems are poor here (0.06-0.12), which suggests the category is hard for this
whole class of approach, but that does not make being last acceptable.

## 3. Why this one counts and the previous one did not

The Gemini run on 2026-08-12 showed bio-memory 0.6042 against mem0 0.3636 — a
much prettier number, and unusable. mem0 hit 114 `Error parsing extraction
response` failures over 1,024 turns, losing 11.1% of its input to a
mem0/Gemini integration fault. A win against a competitor that was quietly
broken is not a win.

This run: **zero** extraction errors. mem0 was given its optional extras
(spaCy, BM25 via fastembed), its documented models, and the same answering
engine as everything else. If it lost, it lost on the merits.

## 4. What this number is not

It measures **`bio-memory`** — the biological stack, with L1/L2, episodes and the
hippocampus. It does **not** measure `cognitive/`, which is what the Claude Code
hook actually runs and what a user installing this today would get.

That gap is the honest caveat on every figure above, and it is the reason
`CognitiveMemorySystem` was built today. The next run puts all four on one table.

Until then the accurate sentence is: *"the biological memory beats mem0 overall
and on temporal reasoning, measured on mem0's own model"* — not *"the product
beats mem0"*.

## 5. Reproduce

    python scripts/run_locomo_eval.py --backend openai --model gpt-4o-mini \
      --systems naive-rag,bio-memory,mem0 --mem0-profile cloud \
      --max-conversations 5 --max-sessions 10 --max-questions 30 \
      --budget-usd 4.50 --tag head2head_openai_full

Raw: `benchmark_reports/locomo_head2head_openai_full_2026_08_13b.json`
