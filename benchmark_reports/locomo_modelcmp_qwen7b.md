# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-06-14 14:17:59
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
| naive-rag | 90 | 0.3082 | 0.1333 | — | 29.41 | 83.34 |
| bio-memory | 90 | 0.421 | 0.1889 | — | 2947.17 | 114.69 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.3262 | 0.1429 | — |
| temporal | 34 | 0.2149 | 0.0588 | — |
| open-domain | 7 | 0.1352 | 0.0 | — |
| single-hop | 28 | 0.4512 | 0.25 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 21 | 0.3071 | 0.0952 | — |
| temporal | 34 | 0.5247 | 0.1765 | — |
| open-domain | 7 | 0.268 | 0.1429 | — |
| single-hop | 28 | 0.4189 | 0.2857 | — |
