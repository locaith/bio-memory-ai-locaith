# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:12b
- **started_at**: 2026-08-09 11:17:09
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
| naive-rag | 59 | 0.3431 | 0.0678 | — | 57.19 | 1626.67 |
| bio-memory | 59 | 0.5643 | 0.1525 | — | 40819.99 | 1478.31 |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 59 | 0.3431 | 0.0678 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| temporal | 59 | 0.5643 | 0.1525 | — |
