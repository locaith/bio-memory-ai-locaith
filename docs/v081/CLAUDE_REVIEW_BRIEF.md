# Claude Opus 5 Review and Continuation Brief

You are reviewing **Bio-AGI Memory OS v0.8.0 Alpha**, an additive overlay for
`locaith/bio-memory-ai-locaith`.

Do not praise it. Attempt to break it, reproduce every number and continue the
work only after identifying false assumptions.

## Run first

```bash
python -m unittest discover -s tests -v
python -m bio_agent_os.cognitive.cli doctor
python -m benchmarks.cognitive_benchmark
python -m benchmarks.context_efficiency_benchmark
python -m benchmarks.domain_coverage_benchmark
python -m benchmarks.safety_benchmark
python -m benchmarks.load_benchmark --memories 10000 --queries 100
python -m benchmarks.external_head_to_head
```

Optional scale reproduction:

```bash
python -m benchmarks.load_benchmark --memories 50000 --queries 50
python -m benchmarks.load_benchmark --memories 100000 --queries 20
```

## Review priorities

1. **Migration safety**
   - Test existing v0.6.1 SQLite databases.
   - Confirm FTS backfill and additive columns do not lose data.
   - Review multiple SQLite connections pointing to the same file.

2. **Context correctness**
   - Prove cached packets invalidate after every canonical write path.
   - Fuzz token budgets, Unicode, empty query and huge block content.
   - Ensure `SIMULATED` blocks cannot leak into normal high-risk context.
   - Verify provenance survives deduplication and sharing.

3. **Retrieval quality**
   - Inspect hand-written weights for overfitting.
   - Add real embeddings and reciprocal-rank fusion without removing the
     policy/time/verification reranker.
   - Test paraphrases where FTS has zero lexical overlap.

4. **Security**
   - Persistent prompt injection across sessions.
   - Malicious counterevidence and fake approvals.
   - Cross-tenant memory, block, cache, checkpoint and share leakage.
   - Restricted blocks shared to lower-clearance agents.
   - Secret exposure through rendered context and logs.

5. **Concurrency and durability**
   - Concurrent SQLite writers, lock contention and crash recovery.
   - Packet-cache coherence under multiple processes.
   - Checkpoint partial-write and restore failure.

6. **External head-to-head**
   - Install real AgentMemory, Mem0, Letta, Graphiti and Cognee in containers.
   - Enforce the fairness contract in `HEAD_TO_HEAD_HARNESS_PLAN.md`.
   - Never substitute published README numbers for actual runs.

7. **NVIDIA/context infrastructure continuation**
   - Implement a real backend adapter only against official SDKs.
   - Measure KV/prefix reuse, TTFT, GPU memory and tokens/watt.
   - Keep canonical cognitive truth independent of hardware cache.

## Recommended commit sequence

1. `feat(memory): add indexed candidate generation and bulk ingestion`
2. `feat(context): add content-addressed context block store`
3. `feat(context): add budgeted compiler and cache invalidation`
4. `feat(context): add checkpoint restore and predictive prefetch`
5. `feat(context): add tenant-scoped multi-agent sharing`
6. `test(memory): add context and multi-domain coverage suite`
7. `bench(memory): add context economics safety and scale reports`
8. `docs(memory): add v0.8 architecture review and continuation state`

## Reject merge if

- an external or simulated input can become authoritative policy without approval
- cache returns stale context after a memory write
- any tenant can read another tenant's memory, block, checkpoint or share
- source evidence can be edited or silently dropped
- the public report claims a real competitor was beaten without executing it
- migration breaks existing public modules or databases

## Next implementation targets

P0:

- real containerized competitor harness
- semantic embedding + BM25/FTS RRF
- concurrency/locking soak tests
- PostgreSQL adapter
- context access clearance for shared blocks

P1:

- multimodal image/audio/video memory
- distributed context transport
- organization memory promotion workflow
- official LongMemEval and LoCoMo end-to-end runs

P2:

- official NIXL/Dynamo backend
- GPU prefix/KV cache metrics
- million-memory and multi-node benchmark

## v0.8.1 concurrency review

Read `reports/PRODUCTION_CONCURRENCY_REPORT_V081.md` and reproduce `benchmarks/production_concurrency_benchmark.py`.

Highest-priority review questions:

1. Can event append and memory projection be committed atomically or replayed deterministically after a mid-transaction crash?
2. Replace shared SQLite with PostgreSQL and rerun the same benchmark matrix.
3. Add multi-host tests with network delay, process death, leader loss and partition injection.
4. Confirm the batched retrieval telemetry does not lose required audit semantics.
5. Add long-duration soak and connection-pool tests.

Do not describe the localhost TCP test as multi-machine distributed production.
