# LoCoMo Benchmark Report

- **backend**: gemini
- **model**: gemini-2.5-flash
- **embedding**: {'backend': 'openai', 'model': 'gemini-embedding-001', 'dimensions': '3072', 'base_url_host': 'generativelanguage.googleapis.com', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'cognitive']
- **mem0_profile**: None
- **started_at**: 2026-08-13 12:05:24
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 1
- max_sessions: 4
- max_questions_per_conversation: 10
- categories: [2]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 9 | 0.5582 | 0.1111 | — | 25.27 | 20.55 |
| cognitive | 9 | 0.3867 | 0.1111 | — | 24.53 | 22.34 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 9 | 0.5582 | 0.1111 | — |

### cognitive — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 9 | 0.3867 | 0.1111 | — |
