# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:e2b
- **started_at**: 2026-06-13 17:15:20
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 3
- max_sessions: 10
- max_questions_per_conversation: 30
- categories: None
- top_k: 10
- sleep_every: 0

- conversations evaluated: conv-26, conv-30, conv-41

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 90 | 0.3906 | 0.1444 | — | 31.09 | 578.11 |
| bio-memory | 90 | 0.4064 | 0.1667 | — | 9800.55 | 488.52 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.3866 | 0.0476 | — |
| temporal | 34 | 0.326 | 0.0588 | — |
| open-domain | 7 | 0.2612 | 0.1429 | — |
| single-hop | 28 | 0.5043 | 0.3214 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.5056 | 0.1429 | — |
| temporal | 34 | 0.3494 | 0.0882 | — |
| open-domain | 7 | 0.2612 | 0.1429 | — |
| single-hop | 28 | 0.4375 | 0.2857 | — |
