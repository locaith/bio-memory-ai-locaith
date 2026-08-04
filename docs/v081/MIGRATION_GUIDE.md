# Migration guide from Bio-Agent OS v0.6.1

## Safety rules

1. Create an isolated branch and back up every database.
2. Do not replace legacy L1/L2/Persona reads immediately.
3. Enable v0.8 shadow writes before switching production reads.
4. Compare event counts, checksums, recall outputs and tenant scopes.
5. Keep a rollback feature flag until migration and soak tests pass.

## Apply overlay

```bash
python tools/apply_upgrade.py --target /path/to/bio-memory-ai-locaith
```

The script:

- backs up existing `bio_agent_os/cognitive`
- backs up existing `bio_agent_os/context_fabric`
- copies v0.8 code, tests, benchmarks and review documents
- appends public exports after backing up `bio_agent_os/__init__.py`
- does not commit or push anything

## Suggested feature flags

- `BIO_COGNITIVE_KERNEL_ENABLED`
- `BIO_COGNITIVE_SHADOW_WRITE`
- `BIO_COGNITIVE_SHADOW_READ`
- `BIO_CONTEXT_COMPILER_ENABLED`
- `BIO_CONTEXT_PACKET_CACHE_ENABLED`
- `BIO_CONTEXT_SHARING_ENABLED`
- `BIO_EPISTEMIC_ENFORCEMENT`
- `BIO_MEMORY_IMMUNE_QUARANTINE`
- `BIO_CLAUDE_HOOK_CAPTURE`

## Legacy data migration

For every legacy item:

1. append an immutable source event
2. preserve tenant, workspace, actor, source and original time
3. create a projected `CognitiveMemory`
4. mark imported text `REPORTED` unless machine evidence exists
5. preserve source references and legacy IDs
6. do not label summaries `VERIFIED` solely because they existed
7. run shadow retrieval and compare expected outputs

## Context migration

Do not import legacy prompt caches as canonical memory. Rebuild context blocks
from migrated cognitive memories so every block has current provenance and
security labels.

## Database review required

Claude must test:

- FTS5 availability and backfill
- multiple SQLite connections to one file
- WAL behavior on Windows
- concurrent hook writes
- cache invalidation across processes
- rollback from the backed-up directories and database copy
