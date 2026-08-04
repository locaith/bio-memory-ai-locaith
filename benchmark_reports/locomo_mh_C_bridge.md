# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: qwen2.5:7b-instruct
- **started_at**: 2026-07-30 19:21:02
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 6
- max_sessions: None
- max_questions_per_conversation: None
- categories: [1]
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26, conv-30, conv-41, conv-42, conv-43, conv-44

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| naive-rag | 172 | 0.2486 | 0.0698 | — | 140.67 | 154.25 |
| bio-memory | 172 | 0.2577 | 0.0407 | — | 18505.21 | 295.85 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 172 | 0.2486 | 0.0698 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 172 | 0.2577 | 0.0407 | — |
