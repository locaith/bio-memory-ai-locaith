# Bio-AGI Memory OS v0.8 Architecture

## Three-plane design

```text
Canonical Cognitive Plane
  immutable events · bitemporal facts · beliefs · procedures · world/self model
                         |
                         v
Context Compiler Plane
  select · verify · deduplicate · budget · arrange · preserve provenance
                         |
                         v
Context Memory Fabric
  blocks · cache · checkpoint · prefetch · sharing · tier placement
                         |
                         v
Storage/Transport Backends
  SQLite today · RAM/NVMe/distributed/NIXL/Dynamo/BlueField adapters later
```

## Core invariants

1. **Observed history is immutable.** Derived memories may be superseded; raw
   source events cannot be silently updated or deleted.
2. **Context cache is never canonical truth.** Every block points back to memory
   and event IDs and may be safely rebuilt.
3. **Simulation never becomes history silently.** `SIMULATED` content is excluded
   from normal context unless explicitly requested.
4. **Self-belief requires verifier evidence.** Self-model capability updates
   accept only verifier-passed outcomes.
5. **High-risk actions prefer verified policy and procedure.** Rejected,
   challenged, expired and unverified memories receive explicit penalties.
6. **Tenant boundaries apply to memory, context, checkpoints and sharing.**
7. **Cache invalidates after canonical writes.** A fast stale packet is a failure,
   not an optimization.
8. **Forgetting is reversible.** Archive and restore produce new versions.

## Write path

```text
Hook / API / sensor / bulk batch
        -> Memory immune inspection
        -> Immutable Event Store
        -> Cognitive projection
        -> Governance promotion checks
        -> World-model projection
        -> Context-cache invalidation
```

## Read path

```text
Query + goal + agent state + access context
        -> FTS5 candidate generation
        -> Cognitive reranking
        -> Context block compilation
        -> Token-budget selection
        -> Tier promotion / packet cache
        -> Model context
```

## Continuity path

```text
Goal + completed/pending steps + hypotheses + tool state
        -> AgentCheckpoint
        -> durable store
        -> exact tenant-scoped restore
```

## Multi-agent path

```text
Verified compiled packet
        -> explicit share registry
        -> same-tenant target agent
        -> revocable context blocks
```

## Module map

### `bio_agent_os/cognitive`

- `models.py`: cognitive object and epistemic model
- `event_store.py`: immutable evidence log and batch append
- `memory_store.py`: versioned projections, FTS5 candidates and batch put
- `immune.py`: write-time memory defence
- `governance.py`: permissions, promotion and exception validity
- `retrieval.py`: cognitive reranking and explanations
- `world_model.py`, `self_model.py`, `prospective.py`
- `causal.py`, `counterfactual.py`, `dream_engine.py`
- `reconstruction.py`: timeline and active-claim reconstruction
- `hooks.py`, `hook_cli.py`: Claude lifecycle capture

### `bio_agent_os/context_fabric`

- `models.py`: blocks, packets, checkpoints, tiers and prefetch plans
- `block_store.py`: content-addressed store, packet cache and sharing
- `context_compiler.py`: minimal context construction
- `checkpoint.py`: save/restore continuity
- `prefetch.py`: anticipatory retrieval
- `tiering.py`: placement policy
- `backend.py`: vendor-neutral backend contract
- `metrics.py`: context economics and latency counters
