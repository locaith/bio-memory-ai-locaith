# Bio-AGI Memory OS v0.8.1 Alpha
## Concurrent and Distributed-Process Benchmark Report

Date: 2026-08-03

## Environment

- 5 logical CPUs
- 5.9 GiB RAM
- Python 3.13.5
- Linux x86_64
- SQLite WAL on a shared local filesystem
- 10,000 seeded cognitive memories
- No Docker, PostgreSQL or Redis was available

The distributed test used three independent service processes communicating through authenticated TCP connections on localhost. It measures process and RPC behavior, but it is not a multi-machine or distributed-storage certification.

## Changes made before the final run

1. SQLite connection timeout increased to 30 seconds.
2. `busy_timeout=30000` enabled.
3. WAL, `synchronous=NORMAL`, memory temp store and WAL checkpoint configuration applied consistently.
4. Retrieval access telemetry changed from one commit per returned memory to one batched transaction per recall.
5. Repeatable concurrent, mixed-workload, TCP cluster and crash-recovery benchmarks added.

## Read concurrency, 10,000 memories

| Processes | QPS | p50 | p95 | p99 | Accuracy | Errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 32.51 | 30.34 ms | 32.28 ms | 36.19 ms | 400/400 | 0 |
| 2 | 61.46 | 31.08 ms | 40.46 ms | 52.96 ms | 400/400 | 0 |
| 4 | **122.38** | 31.44 ms | **34.23 ms** | 38.22 ms | 400/400 | 0 |
| 8 | 119.39 | 51.20 ms | 89.28 ms | 121.39 ms | 400/400 | 0 |

Four processes are the best operating point on this five-CPU environment. Eight processes oversubscribe CPU and increase SQLite telemetry contention.

## Improvement over v0.8.0 baseline

| Processes | Baseline QPS | v0.8.1 QPS | QPS change | Baseline p95 | v0.8.1 p95 |
|---:|---:|---:|---:|---:|---:|
| 1 | 29.99 | 32.51 | +8.4% | 34.81 ms | 32.28 ms |
| 2 | 54.72 | 61.46 | +12.3% | 49.54 ms | 40.46 ms |
| 4 | 117.08 | 122.38 | +4.5% | 40.12 ms | 34.23 ms |
| 8 | 115.88 | 119.39 | +3.0% | 110.35 ms | 89.28 ms |

## Concurrent batch writes

Each worker submitted batches of 25 memories.

| Processes | Stored | Throughput | Batch p50 | Batch p95 | Batch p99 | Errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 500 | 7,844.60 memories/s | 1.91 ms | 2.24 ms | 2.24 ms | 0 |
| 2 | 1,000 | 6,852.62 memories/s | 2.23 ms | 9.75 ms | 14.49 ms | 0 |
| 4 | 2,000 | **12,332.29 memories/s** | 3.13 ms | 15.70 ms | 22.64 ms | 0 |
| 8 | 4,000 | 8,676.85 memories/s | 4.32 ms | 45.90 ms | 182.61 ms | 0 |

The high number is batch-ingestion throughput, not individual fully independent transactions per second.

## Mixed production-style workload

Workload: 80% recall, 20% single-memory writes, concurrent tenant-isolation checks.

| Processes | Operations/s | Read p50 | Read p95 | Read p99 | Write p95 | Correct | Tenant leaks | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | 74.91 | 31.37 ms | 33.75 ms | 34.99 ms | 1.96 ms | 160/160 | 0 | 0 |
| 4 | **140.82** | 32.41 ms | **36.75 ms** | 48.20 ms | 1.60 ms | 320/320 | 0 | 0 |
| 8 | 127.76 | 61.01 ms | 114.84 ms | 147.30 ms | 6.32 ms | 640/640 | 0 | 0 |

## Distributed-process TCP simulation

- 3 independent MemoryOS service processes
- 6 clients
- 360 RPC operations
- 90% reads, 10% writes
- Shared SQLite WAL backend

Results:

- 97.37 operations/s
- p50 31.49 ms
- p95 42.99 ms
- p99 51.23 ms
- 324/324 correct reads
- 36/36 stored writes
- 0 RPC errors

This proves multiple networked service processes can operate against the current shared backend on one host. It does not prove multi-host consistency, failover, replication or network-partition tolerance.

## Forced crash recovery

A writer process was terminated after at least five committed chunks.

- SQLite integrity check: `ok`
- Event rows: 125
- Memory projection rows: 125
- Event/projection parity: yes
- Event checksum chain: valid

The test killed the process after committed chunks. A separate fault-injection test is still required for termination precisely between event commit and memory-projection commit, because those stores currently use separate SQLite transactions.

## Production verdict

### Confirmed on this machine

- Correct concurrent retrieval through four worker processes
- Concurrent batch ingestion with no lost writes in the tested runs
- Zero cross-tenant leakage in the mixed fixture
- Stable TCP multi-process service operation
- Database and checksum integrity after forced worker termination

### Not yet certified

- PostgreSQL production behavior
- Multi-machine distributed storage
- Replication and leader failover
- Network partitions
- Kubernetes autoscaling
- Thousands of simultaneous clients
- Atomicity if a process dies between event and projection commits
- Long-duration soak tests

## Recommended deployment ceiling for the current SQLite alpha

- One machine
- Four worker processes on a five-CPU host
- Prefer batched writes
- Target under 100 sustained mixed operations/s for latency headroom
- Move to PostgreSQL before multi-host deployment or strict HA requirements
