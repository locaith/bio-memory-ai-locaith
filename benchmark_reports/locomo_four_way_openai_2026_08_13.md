# LoCoMo Benchmark Report

- **backend**: openai
- **model**: gpt-4o-mini
- **embedding**: {'backend': 'openai', 'model': 'text-embedding-3-small', 'dimensions': '1536', 'base_url_host': 'unset', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'cognitive', 'bio-memory', 'mem0']
- **mem0_profile**: cloud
- **started_at**: 2026-08-13 18:38:19
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
| naive-rag | 150 | 0.3954 | 0.1667 | — | 328.84 | 162.01 |
| bio-memory | 150 | 0.4123 | 0.1933 | — | 4620.55 | 286.89 |
| cognitive | 150 | 0.0841 | 0.0467 | — | 336.37 | 168.7 |
| mem0 | 150 | 0.3863 | 0.1467 | — | 2669.98 | 238.33 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.3975 | 0.0345 | — |
| temporal | 54 | 0.364 | 0.1296 | — |
| open-domain | 17 | 0.0723 | 0.0 | — |
| single-hop | 50 | 0.538 | 0.34 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.3409 | 0.069 | — |
| temporal | 54 | 0.4576 | 0.2037 | — |
| open-domain | 17 | 0.0662 | 0.0 | — |
| single-hop | 50 | 0.5225 | 0.32 | — |

### cognitive — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.0 | 0.0 | — |
| temporal | 54 | 0.1052 | 0.0556 | — |
| open-domain | 17 | 0.0319 | 0.0 | — |
| single-hop | 50 | 0.1278 | 0.08 | — |

### mem0 — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.4516 | 0.1034 | — |
| temporal | 54 | 0.3609 | 0.0741 | — |
| open-domain | 17 | 0.0634 | 0.0 | — |
| single-hop | 50 | 0.4858 | 0.3 | — |
