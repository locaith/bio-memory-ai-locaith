# Context Memory Fabric

This package converts canonical cognitive memories into compact, reusable and
hardware-aware context artifacts. It never treats context/KV cache as the
source of truth. Every block preserves memory and event provenance.

Current v0.8 alpha backends are SQLite/local. `ContextMemoryBackend` is the
vendor-neutral contract for Redis, object storage, NIXL/Dynamo/BlueField and
other future distributed adapters.
