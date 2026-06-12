# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:e2b
- **started_at**: 2026-06-12 07:46:58
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
| no-memory | 12 | 0.0 | 0.0 | — | 0.0 | 26.11 |
| naive-rag | 12 | 0.377 | 0.1667 | — | 3.76 | 71.27 |
| bio-memory | 12 | 0.0833 | 0.0833 | — | 544.95 | 73.8 |

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
| multi-hop | 3 | 0.2222 | 0.0 | — |
| temporal | 7 | 0.3673 | 0.1429 | — |
| open-domain | 1 | 0.2857 | 0.0 | — |
| single-hop | 1 | 1.0 | 1.0 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 3 | 0.0 | 0.0 | — |
| temporal | 7 | 0.0 | 0.0 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 1 | 1.0 | 1.0 | — |
