> ⚠️ **SUPERSEDED — produced on PRE-FIX code. Do not cite as a current result.**
> This run predates the ranking, fact-preserving consolidation, and dense
> hippocampal-recall fixes. Retained only to document the honest development
> trajectory (F1 0.0 → 0.498). Current headline: `locomo_overnight_qwen7b_v3.md`.

# LoCoMo Benchmark Report

- **backend**: ollama
- **model**: gpt-oss:120b-cloud
- **started_at**: 2026-06-12 09:25:53
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
| no-memory | 300 | 0.0124 | 0.0067 | — | 0.0 | 433.55 |
| naive-rag | 300 | 0.4507 | 0.21 | — | 52.65 | 672.0 |
| bio-memory | 300 | 0.0901 | 0.0267 | — | 7508.82 | 381.75 |

### no-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.0093 | 0.0 | — |
| temporal | 118 | 0.0042 | 0.0 | — |
| open-domain | 36 | 0.0706 | 0.0556 | — |
| single-hop | 92 | 0.002 | 0.0 | — |

### naive-rag — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.3561 | 0.0926 | — |
| temporal | 118 | 0.5148 | 0.2034 | — |
| open-domain | 36 | 0.196 | 0.1111 | — |
| single-hop | 92 | 0.5236 | 0.3261 | — |

### bio-memory — per category

| Category | Count | F1 | EM | Abstention |
|---|---|---|---|---|
| multi-hop | 54 | 0.0771 | 0.0185 | — |
| temporal | 118 | 0.1163 | 0.0254 | — |
| open-domain | 36 | 0.023 | 0.0 | — |
| single-hop | 92 | 0.0903 | 0.0435 | — |
