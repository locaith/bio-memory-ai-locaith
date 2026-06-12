# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gemma4:e2b
- **started_at**: 2026-06-12 07:36:27
- **dataset**: data\evals\locomo10.json
- **dataset_url**: https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json

## Configuration
- max_conversations: 1
- max_sessions: 2
- max_questions_per_conversation: 10
- categories: None
- top_k: 10
- sleep_every: 20

- conversations evaluated: conv-26

## Results

| System | Questions | F1 (answerable) | EM | Abstention (adversarial) | Ingest s | Answer s |
|---|---|---|---|---|---|---|
| no-memory | 10 | 0.0 | 0.0 | — | 0.0 | 22.83 |
| naive-rag | 10 | 0.331 | 0.1 | — | 3.26 | 58.83 |
| bio-memory | 10 | 0.0 | 0.0 | — | 240.96 | 9.14 |

### no-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 2 | 0.0 | 0.0 | — |
| temporal | 4 | 0.0 | 0.0 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 3 | 0.0 | 0.0 | — |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 2 | 0.0 | 0.0 | — |
| temporal | 4 | 0.1667 | 0.0 | — |
| open-domain | 1 | 0.2857 | 0.0 | — |
| single-hop | 3 | 0.7857 | 0.3333 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 2 | 0.0 | 0.0 | — |
| temporal | 4 | 0.0 | 0.0 | — |
| open-domain | 1 | 0.0 | 0.0 | — |
| single-hop | 3 | 0.0 | 0.0 | — |
