# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gpt-oss:120b-cloud
- **started_at**: 2026-06-12 12:02:24
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
| bio-memory | 300 | 0.1392 | 0.0467 | — | 9545.43 | 458.03 |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.0813 | 0.0 | — |
| temporal | 118 | 0.2307 | 0.0847 | — |
| open-domain | 36 | 0.115 | 0.0833 | — |
| single-hop | 92 | 0.0652 | 0.0109 | — |
