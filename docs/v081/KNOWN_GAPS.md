# Known gaps and prohibited claims

## No external victory has been proven

AgentMemory, Mem0, Letta, Graphiti and Cognee were not installed and executed in
this build environment. The included cognitive baseline is search-centric and is
not any named competitor. Do not publish “beats AgentMemory” from these reports.

## Scale is local and single-process

The largest completed run is 100,000 synthetic memories on SQLite/FTS5 with a
bounded 100-candidate rerank. It does not measure concurrency, sharding,
replication, GPU KV-cache, cluster failures or billion-memory operation.

Next engineering step:

- PostgreSQL/pgvector or a dedicated hybrid index
- asynchronous write workers and backpressure
- concurrent read/write soak tests
- distributed context transport
- cache coherence and failover

## Context hardware integration is an interface, not a shipped NVIDIA backend

`ContextMemoryBackend` is ready for adapters, but no real NIXL, Dynamo,
BlueField or Context Memory Storage integration is included. No tokens-per-watt,
KV-cache reuse or GPU-memory claim is made.

## Cognitive modules are structured alpha implementations

World model, causal engine and counterfactual simulator are evidence structures,
not a learned universal world simulator. Dream consolidation is deterministic,
not autonomous scientific discovery.

## Multimodal coverage is schema-level

Modality is tracked, but CLIP image embeddings, speech/audio memory, video event
indexing, 3D spatial encoders and sensor fusion are not included.

## Security is not certification

The immune/governance benchmark is a deterministic smoke suite. Production needs:

- independent red-team and fuzzing
- signed sources and key management
- stronger model-based injection detection
- audit export, retention and deletion policy
- supply-chain and dependency review

## Regulated-domain tests are routing tests

Healthcare, legal and finance fixtures confirm retrieval and policy behavior.
They do not validate professional advice or regulatory compliance.

## v0.8.1 concurrency findings

- SQLite scales well to four processes on the tested five-CPU host, then saturates.
- Distributed-process TCP operation was tested only on localhost with a shared SQLite file.
- Event append and memory projection are not one cross-component atomic transaction.
- PostgreSQL, replication, network partition and Kubernetes tests remain mandatory before production HA claims.
