# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-07-30 11:40:03
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 2
- max_sessions: 4
- max_questions_per_conversation: None
- categories: [1]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26, conv-30

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 7 | 0.1475 | 0.0 | — | 12.12 | 16.7 |
| bio-memory | 7 | 0.3218 | 0.1429 | — | 680.59 | 8.18 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 7 | 0.1475 | 0.0 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 7 | 0.3218 | 0.1429 | — |
