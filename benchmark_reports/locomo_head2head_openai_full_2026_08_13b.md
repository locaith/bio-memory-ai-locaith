# LoCoMo Benchmark Report

- **backend**: openai
- **model**: gpt-4o-mini
- **embedding**: {'backend': 'openai', 'model': 'text-embedding-3-small', 'dimensions': '1536', 'base_url_host': 'unset', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'bio-memory', 'mem0']
- **mem0_profile**: cloud
- **started_at**: 2026-08-13 12:00:48
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 5
- max_sessions: 10
- max_questions_per_conversation: 30
- categories: None
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26, conv-30, conv-41, conv-42, conv-43

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 150 | 0.397 | 0.1733 | — | 682.58 | 307.94 |
| bio-memory | 150 | 0.4424 | 0.1867 | — | 6117.52 | 302.18 |
| mem0 | 150 | 0.3958 | 0.1533 | — | 3052.64 | 251.28 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.3929 | 0.0345 | — |
| temporal | 54 | 0.3773 | 0.1481 | — |
| open-domain | 17 | 0.0723 | 0.0 | — |
| single-hop | 50 | 0.531 | 0.34 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.3691 | 0.069 | — |
| temporal | 54 | 0.5008 | 0.1852 | — |
| open-domain | 17 | 0.0634 | 0.0 | — |
| single-hop | 50 | 0.5508 | 0.32 | — |

### mem0 — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.4191 | 0.1034 | — |
| temporal | 54 | 0.4392 | 0.0926 | — |
| open-domain | 17 | 0.1223 | 0.0588 | — |
| single-hop | 50 | 0.4284 | 0.28 | — |
