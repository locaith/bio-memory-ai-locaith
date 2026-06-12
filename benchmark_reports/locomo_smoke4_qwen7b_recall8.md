# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-06-12 08:08:52
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 1
- max_sessions: 3
- max_questions_per_conversation: 12
- categories: None
- top_k: 10
- sleep_every: 0

- conversations evaluated: conv-26

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 12 | 0.327 | 0.1667 | — | 3.86 | 10.2 |
| bio-memory | 12 | 0.246 | 0.1667 | — | 132.81 | 11.39 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 3 | 0.1333 | 0.0 | — |
| temporal | 7 | 0.3197 | 0.1429 | — |
| open-domain | 1 | 0.2857 | 0.0 | — |
| single-hop | 1 | 1.0 | 1.0 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 3 | 0.0 | 0.0 | — |
| temporal | 7 | 0.2789 | 0.1429 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 1 | 1.0 | 1.0 | — |
