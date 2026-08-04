# Results Summary: Bio-AGI Memory OS v0.8.0 Alpha

Generated: 2026-08-02

## Verification status

- Automated tests: **99/99 passed**
- Clean wheel build: **passed**
- Isolated wheel installation and context compilation: **passed**
- Doctor checks: **7/7 passed**
- Actual external competitor run: **not performed**

## Cognitive differentiation

| Metric | Search-only baseline | Bio cognitive |
|---|---:|---:|
| Accuracy | 58.33% | **100.00%** |
| Cases | 12 | 12 |

The baseline is an internal lexical/semantic approximation, not AgentMemory or another named product.

## Context efficiency

| Metric | Result |
|---|---:|
| Memories in fixture | 808 |
| Naive full-context estimate | 33,701 tokens |
| Compiled packet | **411 tokens** |
| Reduction | **98.78%** |
| Required signal recall | **100%** |
| First compile | 13.39 ms |
| Cached compile | **0.41 ms** |
| Checkpoint restore | **0.037 ms** |

## Breadth and safety

- Domain fixtures: **20/20 top-1**, **20/20 compiled-context inclusion**
- Safety/governance: **7/7 passed**
- Domains include coding, ERP, legal, healthcare, finance, IoT, education,
  cybersecurity, support, personal assistant, Blender, wind energy, DevOps,
  database, robotics, science, sales, HR, manufacturing and Vietnamese.

## Indexed scale smoke results

| Memories | Queries | Top-1 | Ingest/s | p50 | p95 | Cold start |
|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 100 | 100% | 13,996 | 29.6 ms | 30.8 ms | 32.4 ms |
| 50,000 | 50 | 100% | 12,713 | 156.3 ms | 161.3 ms | 159.8 ms |
| 100,000 | 20 | 100% | 11,823 | 311.2 ms | 325.2 ms | 318.0 ms |

These are single-process synthetic SQLite/FTS5 runs with bounded candidate reranking.

## Honest conclusion

v0.8 is a materially stronger engineering alpha: it adds a real context plane,
indexed retrieval, continuity and measurable context reduction. It has **not**
been proven superior to AgentMemory, Mem0, Letta, Graphiti or Cognee because
those packages were unavailable in this build environment. The included
preflight status is `READY_BUT_NOT_RUN`.
