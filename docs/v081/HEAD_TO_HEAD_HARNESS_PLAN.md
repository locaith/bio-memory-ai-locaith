# Fair Head-to-Head Harness Plan

## Systems

- Bio-AGI Memory OS v0.8
- AgentMemory
- Mem0
- Letta
- Graphiti/Zep
- Cognee
- Naive RAG and full-context baselines

## Fairness contract

Every system must use the same:

- dataset split
- embedding model and dimensions
- reader LLM
- reranker or no reranker
- context-token budget
- hardware and concurrency
- timeout and retry policy
- source documents and timestamps

## Benchmark families

1. LongMemEval retrieval and official end-to-end QA
2. LoCoMo end-to-end QA
3. coding-life task completion and repeated-error reduction
4. temporal/bitemporal truth
5. policy, approved exception and expired exception
6. memory poisoning and cross-tenant leakage
7. context economics: injected tokens, cache hit, restore time
8. scale: 10K, 100K, 1M and concurrent tenants
9. multi-agent knowledge promotion and revocation

## Required report

- accuracy/F1/EM/recall/MRR as applicable
- p50/p95/p99 latency
- token and storage cost
- false/stale memory rate
- task success after experience
- poisoning success rate
- setup complexity and failure rate

## Rule

A competitor receives no score unless its actual released package executes.
README claims and differently configured published numbers are context only.
