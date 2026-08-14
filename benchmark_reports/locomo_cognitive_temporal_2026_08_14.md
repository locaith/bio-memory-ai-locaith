# LoCoMo Benchmark Report

- **backend**: openai
- **model**: gpt-4o-mini
- **embedding**: {'backend': 'openai', 'model': 'text-embedding-3-small', 'dimensions': '1536', 'base_url_host': 'unset', 'degraded_hash_mode': False}
- **systems**: ['cognitive']
- **mem0_profile**: None
- **started_at**: 2026-08-14 20:56:30
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
| cognitive | 150 | 0.3618 | 0.1733 | — | 389.94 | 175.37 |

### cognitive — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 29 | 0.2741 | 0.069 | — |
| temporal | 54 | 0.3802 | 0.1296 | — |
| open-domain | 17 | 0.0634 | 0.0 | — |
| single-hop | 50 | 0.4942 | 0.34 | — |
