# LoCoMo Benchmark Report

- **backend**: gemini
- **model**: gemini-2.5-flash
- **embedding**: {'backend': 'openai', 'model': 'gemini-embedding-001', 'dimensions': '3072', 'base_url_host': 'generativelanguage.googleapis.com', 'degraded_hash_mode': False}
- **systems**: ['naive-rag', 'bio-memory', 'mem0']
- **mem0_profile**: gemini
- **started_at**: 2026-08-12 20:38:14
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 5
- max_sessions: 10
- max_questions_per_conversation: 30
- categories: [2]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26, conv-30, conv-41, conv-42, conv-43

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 59 | 0.545 | 0.2034 | — | 385.23 | 198.93 |
| bio-memory | 59 | 0.6042 | 0.1864 | — | 6398.25 | 249.67 |
| mem0 | 59 | 0.3636 | 0.1186 | — | 5720.32 | 183.34 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 59 | 0.545 | 0.2034 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 59 | 0.6042 | 0.1864 | — |

### mem0 — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 59 | 0.3636 | 0.1186 | — |
