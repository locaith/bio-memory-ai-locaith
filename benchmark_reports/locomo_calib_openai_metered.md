# LoCoMo Benchmark Report

- **backend**: openai
- **model**: gpt-4o-mini
- **embedding**: {'backend': 'openai', 'model': 'text-embedding-3-small', 'dimensions': '1536', 'base_url_host': 'unset', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'bio-memory', 'mem0']
- **mem0_profile**: cloud
- **started_at**: 2026-08-13 00:09:25
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 1
- max_sessions: 3
- max_questions_per_conversation: 8
- categories: [2]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 7 | 0.44 | 0.1429 | — | 27.57 | 8.69 |
| bio-memory | 7 | 0.2867 | 0.0 | — | 238.1 | 11.78 |
| mem0 | 7 | 0.7551 | 0.4286 | — | 191.44 | 15.06 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 7 | 0.44 | 0.1429 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 7 | 0.2867 | 0.0 | — |

### mem0 — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 7 | 0.7551 | 0.4286 | — |
