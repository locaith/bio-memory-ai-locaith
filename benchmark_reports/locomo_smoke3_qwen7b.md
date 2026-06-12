# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-06-12 08:03:33
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
| no-memory | 12 | 0.0 | 0.0 | — | 0.0 | 11.38 |
| naive-rag | 12 | 0.327 | 0.1667 | — | 4.03 | 10.25 |
| bio-memory | 12 | 0.2183 | 0.0833 | — | 150.27 | 9.93 |

### no-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 3 | 0.0 | 0.0 | — |
| temporal | 7 | 0.0 | 0.0 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 1 | 0.0 | 0.0 | — |

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
| multi-hop | 3 | 0.2222 | 0.0 | — |
| temporal | 7 | 0.1361 | 0.0 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 1 | 1.0 | 1.0 | — |
