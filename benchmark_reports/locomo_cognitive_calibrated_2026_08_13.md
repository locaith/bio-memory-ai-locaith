# LoCoMo Benchmark Report

- **backend**: openai
- **model**: gpt-4o-mini
- **embedding**: {'backend': 'openai', 'model': 'text-embedding-3-small', 'dimensions': '1536', 'base_url_host': 'unset', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'cognitive']
- **mem0_profile**: None
- **started_at**: 2026-08-13 21:20:12
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
| naive-rag | 150 | 0.404 | 0.18 | — | 343.63 | 172.38 |
| cognitive | 150 | 0.0048 | 0.0 | — | 332.74 | 179.03 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.3871 | 0.069 | — |
| temporal | 54 | 0.3916 | 0.1481 | — |
| open-domain | 17 | 0.0723 | 0.0 | — |
| single-hop | 50 | 0.5399 | 0.34 | — |

### cognitive — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.0 | 0.0 | — |
| temporal | 54 | 0.0 | 0.0 | — |
| open-domain | 17 | 0.0319 | 0.0 | — |
| single-hop | 50 | 0.0036 | 0.0 | — |
