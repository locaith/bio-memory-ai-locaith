# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-06-12 16:44:20
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
| no-memory | 300 | 0.0123 | 0.0033 | — | 0.0 | 145.12 |
| naive-rag | 300 | 0.2543 | 0.0833 | — | 44.81 | 231.9 |
| bio-memory | 300 | 0.3256 | 0.1067 | — | 8449.92 | 315.8 |

### no-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.0093 | 0.0 | — |
| temporal | 118 | 0.0042 | 0.0 | — |
| open-domain | 36 | 0.0607 | 0.0278 | — |
| single-hop | 92 | 0.0056 | 0.0 | — |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.3145 | 0.037 | — |
| temporal | 118 | 0.1362 | 0.0254 | — |
| open-domain | 36 | 0.068 | 0.0278 | — |
| single-hop | 92 | 0.4435 | 0.2065 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.2455 | 0.037 | — |
| temporal | 118 | 0.3717 | 0.0763 | — |
| open-domain | 36 | 0.0943 | 0.0556 | — |
| single-hop | 92 | 0.404 | 0.2065 | — |
