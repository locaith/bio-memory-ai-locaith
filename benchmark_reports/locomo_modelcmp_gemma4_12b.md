# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:12b
- **started_at**: 2026-06-13 07:47:35
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
| naive-rag | 90 | 0.4609 | 0.1556 | — | 28.01 | 2535.54 |
| bio-memory | 90 | 0.4983 | 0.2111 | — | 29360.62 | 2134.16 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.5522 | 0.0952 | — |
| temporal | 34 | 0.4156 | 0.0882 | — |
| open-domain | 7 | 0.1898 | 0.0 | — |
| single-hop | 28 | 0.5152 | 0.3214 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.4049 | 0.1429 | — |
| temporal | 34 | 0.6034 | 0.2059 | — |
| open-domain | 7 | 0.2612 | 0.1429 | — |
| single-hop | 28 | 0.5001 | 0.2857 | — |
