# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:12b
- **embedding**: {'backend': 'openai', 'model': 'nomic-embed-text', 'dimensions': '768', 'base_url_host': 'localhost:11434', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'mem0']
- **mem0_profile**: local
- **started_at**: 2026-08-11 22:44:26
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 1
- max_sessions: 2
- max_questions_per_conversation: 6
- categories: [2]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 4 | 0.25 | 0.0 | — | 4.34 | 84.96 |
| mem0 | 4 | 0.4167 | 0.0 | — | 1161.47 | 39.04 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 4 | 0.25 | 0.0 | — |

### mem0 — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 4 | 0.4167 | 0.0 | — |
