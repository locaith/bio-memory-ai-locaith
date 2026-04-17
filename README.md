# Bio-Agent OS V2 Foundation

Bio-Agent OS is a portable bio-inspired memory controller for coding agents, ERP agents, and long-running autonomous systems.

Core thesis:

- AI should not remember everything.
- AI should forget noise.
- AI should compress experience into reusable knowledge.
- Only stable lessons should become part of the self-model.

This repository is built for OpenClaw-style workflows, but the design is platform-agnostic.

## What Changed In This V2 Foundation

This upgrade moves the project from a raw concept toward a more auditable and portable memory stack:

- Added an `EpisodeStore` so memories have provenance and can be traced back to concrete events.
- Upgraded `Persona` into a scope-aware self-model with rule metadata:
  - `scope`
  - `confidence`
  - `support_count`
  - `contradiction_count`
  - `state`
  - `evidence_episode_ids`
- Upgraded the hippocampus from "summarize one event into one rule" into a memory compiler with four outputs:
  - episodic summary
  - semantic memory
  - procedural memory
  - identity rule candidate
- Added a `dream()` path in addition to normal sleep consolidation.
- Expanded provider support so users can run Bio-Agent OS with:
  - Gemini
  - Claude / Anthropic
  - OpenAI
  - Grok / xAI
  - Ollama
  - any OpenAI-compatible local AI server

## Architecture

### Layers

1. `EpisodeStore`
   Raw ground-truth experience stream with provenance.

2. `L1WorkingMemory`
   Short-term buffer for recent events and raw observations.

3. `L2SemanticMemory`
   Long-term semantic and procedural memory with decay.

4. `KnowledgeGraph`
   Relationship memory for entities and dependencies.

5. `Persona`
   Scope-aware self-model for stable rules that should guide future behavior.

6. `Hippocampus`
   Memory compiler that transforms experiences into structured long-term memory.

### Memory Lifecycle

`Perceive -> Consolidate -> Forget -> Reconcile -> Become`

- `Perceive`: capture an event as an episode plus a working-memory entry
- `Consolidate`: compile the event into episodic, semantic, procedural, and identity outputs
- `Forget`: prune transient noise through TTL and decay
- `Reconcile`: challenge or reinforce old rules when new evidence appears
- `Become`: only stable rules are injected into the self-model prompt

## Why This Matters

Most memory systems for AI agents still behave like retrieval wrappers:

- store logs
- embed chunks
- retrieve similar text later

Bio-Agent OS is trying to solve a different problem:

- how an agent stops repeating mistakes
- how an agent develops project-specific operating laws
- how an agent keeps identity without stuffing all history into context

For coding agents, the permanent memory should not be the raw terminal log. It should be the lesson extracted from the terminal log.

## Provider Support

### 1. Gemini

```bash
pip install "bio-agent-os[gemini]"
```

`.env`

```env
LLM_BACKEND=gemini
MODEL_ID=gemini-2.5-flash
GEMINI_API_KEY=your_key_here
```

### 2. Claude / Anthropic

```bash
pip install "bio-agent-os[anthropic]"
```

`.env`

```env
LLM_BACKEND=anthropic
MODEL_ID=claude-3-7-sonnet-latest
ANTHROPIC_API_KEY=your_key_here
```

### 3. OpenAI

```bash
pip install "bio-agent-os[openai]"
```

`.env`

```env
LLM_BACKEND=openai
MODEL_ID=gpt-4.1-mini
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
```

### 4. Grok / xAI

`.env`

```env
LLM_BACKEND=grok
MODEL_ID=grok-3-mini
XAI_API_KEY=your_key_here
XAI_BASE_URL=https://api.x.ai/v1
```

### 5. Ollama

```bash
pip install "bio-agent-os[ollama]"
```

`.env`

```env
LLM_BACKEND=ollama
MODEL_ID=gemma3:12b
OLLAMA_BASE_URL=http://localhost:11434
```

### 6. Local AI Server / AI Local / LM Studio / vLLM / OpenWebUI

If your local AI runtime exposes an OpenAI-compatible endpoint, Bio-Agent OS can use it directly.

This is the recommended path for users who already have a local model such as `gemma4:e2b` running.

`.env`

```env
LLM_BACKEND=openai
MODEL_ID=gemma4:e2b
LLM_API_KEY=local-dev-key
LLM_BASE_URL=http://127.0.0.1:1234/v1
```

If your local runtime expects the OpenAI-specific env names instead:

```env
OPENAI_API_KEY=local-dev-key
OPENAI_BASE_URL=http://127.0.0.1:1234/v1
```

This gives you a local hippocampus path without forcing a cloud model.

## Quick Start

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install with the provider you want:

```bash
pip install -e ".[gemini]"
```

or

```bash
pip install -e ".[openai]"
```

Copy the env file:

```bash
cp .env.example .env
```

Start the API:

```bash
python -m bio_agent_os.api.main
```

Default API:

- `GET /api/state`
- `POST /api/ingest`
- `POST /api/chat`
- `POST /api/sleep`
- `POST /api/dream`

## OpenClaw Integration

Bio-Agent OS is designed to fit the OpenClaw loop:

- ingest observations from tool runs or terminal output
- perform micro-sleep every N actions
- compile recurring failures into reusable rules
- inject only stable self-model rules back into the agent prompt

Example:

```python
import asyncio

from bio_agent_os import (
    EpisodeStore,
    GarbageCollector,
    Hippocampus,
    L1WorkingMemory,
    L2SemanticMemory,
    LLMEngine,
    Persona,
)
from bio_agent_os.adapters.openclaw_adapter import OpenClawBioAdapter


async def main():
    engine = LLMEngine.from_env()
    l1 = L1WorkingMemory(agent_name="openclaw-brain")
    l2 = L2SemanticMemory(agent_name="openclaw-brain")
    episodes = EpisodeStore(agent_name="openclaw-brain")
    persona = Persona(name="openclaw-brain")
    hippo = Hippocampus(engine=engine, l1=l1, l2=l2, episodes=episodes, persona=persona)
    gc = GarbageCollector(l1=l1, l2=l2)

    adapter = OpenClawBioAdapter(
        hippocampus=hippo,
        garbage_collector=gc,
        persona=persona,
    )

    await adapter.ingest_observation(
        "run_command",
        "npm install failed because of peer dependency mismatch",
    )
    await adapter.trigger_micro_sleep()

    print(adapter.inject_persona_to_openclaw())


asyncio.run(main())
```

## V2 Design Direction

The current V2 foundation now supports the first pieces of a stronger memory architecture:

- episodes with provenance
- stable vs unstable self-model rules
- semantic / procedural / episodic split during consolidation
- provider portability for global adoption

The next major milestones should be:

1. contradiction resolver with stronger rule conflict detection
2. belief graph with temporal validity windows
3. richer attention scoring inside L1
4. benchmark suite for long-running coding agents
5. stronger OpenClaw-native hooks and examples

## Tests

```bash
pytest
```

## Positioning

Bio-Agent OS is not another model.

It is a memory controller that can sit behind many models and many agent frameworks.

That is the path to becoming globally useful:

- portable across providers
- local-first when needed
- cloud-backed when needed
- compatible with OpenClaw and adjacent agent ecosystems

## License

MIT
