> ⚠️ **INVALID — corrupted run. Do not cite.**
> The Ollama cloud model returned HTTP 429 mid-run and the (then unguarded)
> client swallowed errors into empty strings, yielding 300 empty predictions
> and a meaningless F1 of 0.0. Fixed in commit `02bdffd` (fail-fast on HTTP
> errors). Retained only as evidence of the failure that motivated the fix.

# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gpt-oss:120b-cloud
- **started_at**: 2026-06-12 14:52:54
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 10
- max_sessions: 10
- max_questions_per_conversation: 30
- categories: None
- top_k: 10
- sleep_every: 0

- conversations evaluated: conv-26, conv-30, conv-41, conv-42, conv-43, conv-44, conv-47, conv-48, conv-49, conv-50

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| bio-memory | 300 | 0.0 | 0.0 | — | 6156.47 | 247.46 |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.0 | 0.0 | — |
| temporal | 118 | 0.0 | 0.0 | — |
| open-domain | 36 | 0.0 | 0.0 | — |
| single-hop | 92 | 0.0 | 0.0 | — |
