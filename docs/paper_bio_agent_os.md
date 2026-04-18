# Bio-Agent OS: A Biologically-Inspired Memory Architecture for Autonomous Coding Agents with Homeostatic Attention, Belief Lifecycle Management, and NLI-Backed Contradiction Resolution

**Locaith Solution Tech**

> *Submitted to NeurIPS 2026 Workshop on Memory and Retrieval in Foundation Models*

---

## Abstract

Long-running autonomous agents require persistent memory systems that go beyond simple key-value caching or append-only context windows. We present **Bio-Agent OS**, an open-source memory framework that draws from neuroscience to provide coding and ERP agents with a biologically faithful memory architecture. Our system implements (1) a multi-tier memory pipeline mirroring human memory consolidation (L1 Working Memory → L2 Semantic Memory → Belief Graph), (2) a homeostatic attention scheduler that dynamically adjusts focus weights based on agent stress and failure streaks, (3) an Ebbinghaus-decay forgetting curve for synaptic pruning, (4) a six-state belief lifecycle with governed exception governance, and (5) a hybrid heuristic+NLI contradiction detector with persistent caching. We evaluate on an 8-pair multi-domain conflict detection benchmark and a 6-task end-to-end memory consolidation suite using Gemma-4 E2B as the local inference backbone. Results show that the hybrid NLI detector achieves **8/8 (100%) accuracy with 1.0 precision and 0 false positives**, compared to 4/8 (50%) for heuristic-only detection. The NLI cache eliminates 100% of redundant inference calls on repeat evaluations. Bio-Agent OS is the first open-source framework to combine production-grade persistence (SQLite + PostgreSQL), bio-faithful memory dynamics, and enterprise-grade rule governance in a single installable package.

**Keywords:** agent memory, bio-inspired AI, belief management, contradiction detection, NLI, attention scheduling, memory consolidation

---

## 1. Introduction

The rapid adoption of autonomous coding agents—systems like OpenClaw, SWE-Agent, and Devin—has exposed a critical infrastructure gap: **agents lack memory systems that can learn, forget, contradict, and self-correct across sessions**. Current approaches fall into three categories:

1. **Context window stuffing**: Prepending all prior observations into the prompt. This is bounded by the context window size and provides no mechanism for forgetting or prioritization.

2. **Vector-store retrieval**: Systems like Mem0 (Khattab et al., 2024) and Zep store memories as embeddings and retrieve by similarity. While effective for recall, they treat all memories as equally valid and provide no lifecycle management.

3. **Graph-based memory**: Letta (Packer et al., 2024) and Graphiti use relational structures but lack biological dynamics—there is no forgetting, no attention homeostasis, and no mechanism for handling contradictory beliefs.

Bio-Agent OS addresses these limitations by modeling memory after the human brain's consolidation pipeline. We draw from three neuroscience principles:

- **Synaptic consolidation** (Dudai, 2004): Short-term memories in L1 working memory are selectively encoded into long-term L2 semantic memory during "sleep cycles," analogous to hippocampal replay.
- **Ebbinghaus forgetting** (Ebbinghaus, 1885): Memories decay exponentially with time. Unimportant or unreinforced memories are pruned via a configurable decay function W(t) = W₀ · e^(−λt).
- **Homeostatic plasticity** (Turrigiano, 2008): The attention scheduler dynamically adjusts its weighting scheme based on cumulative stress, failure streaks, and time since last failure—a computational analog of neuromodulatory gain control.

Additionally, we introduce a novel **Governed Exception Pattern** for enterprise environments where rules must coexist with approved overrides (e.g., "Never force push" alongside "Allow force push during approved hotfix with audit logging"). This is, to our knowledge, the first agent memory system to distinguish *contradictions* from *governed exceptions* at the architectural level.

### 1.1 Contributions

1. A multi-tier memory architecture (L1 → L2 → Belief Graph) with biologically-inspired consolidation, forgetting, and attention mechanisms.
2. A six-state belief lifecycle (`proposed → reinforced → stable → challenged → deprecated → archived`) with evidence-linked provenance.
3. A homeostatic attention scheduler with dynamic stress-responsive weight adjustment and temporal stress decay.
4. A hybrid heuristic+NLI contradiction detector with persistent SQLite-backed caching, achieving 100% accuracy on an 8-pair multi-domain benchmark.
5. The Governed Exception Pattern: a formal mechanism for distinguishing conditional approved overrides from true contradictions.
6. Open-source implementation with 38 tests, Docker packaging, PostgreSQL adapter, plugin system, and REST API.

---

## 2. Related Work

### 2.1 Agent Memory Frameworks

**Mem0** (Chhablani et al., 2024) provides a "memory layer" for LLM applications using vector databases (Qdrant, Pinecone). Memories are stored as embedding vectors and retrieved by cosine similarity. Mem0 lacks lifecycle management—memories are never challenged, deprecated, or forgotten. There is no mechanism for detecting or resolving contradictory memories. Mem0 provides no attention scheduling; all memories compete equally regardless of urgency.

**Letta** (formerly MemGPT; Packer et al., 2024) introduces a virtual memory hierarchy for LLM context management, with "main context" and "archival memory" tiers managed by the agent itself. While architecturally innovative, Letta's memory management is purely LLM-driven (the agent decides what to store/retrieve), providing no principled forgetting mechanism, no belief lifecycle, and no bio-inspired dynamics.

**Zep/Graphiti** (Graphiti, 2024) uses a temporal knowledge graph to represent memory, supporting time-aware queries. While its temporal modeling is strong, Zep lacks contradiction detection, governed exception handling, and stress-responsive attention.

**OpenAI's built-in memory** for ChatGPT provides user-level memory persistence but is a closed, non-programmable system with no lifecycle management, no conflict resolution, and no developer API.

### 2.2 Biological Memory Models in AI

Computational models of human memory consolidation have a long history (McClelland et al., 1995; Kumaran et al., 2016). However, these models have rarely been applied to practical agent infrastructure. Notable exceptions include:

- **MERLIN** (Wayne et al., 2018): A neural architecture with external memory and attention, but designed for reinforcement learning rather than agent tool use.
- **Generative Agents** (Park et al., 2023): Simulates human-like memory with importance scoring, recency decay, and reflection. Bio-Agent OS extends this approach with synaptic pruning, homeostatic weights, belief lifecycle, and governed exceptions.

### 2.3 Contradiction Detection in Knowledge Bases

Natural Language Inference (NLI) has been applied to knowledge base completion and fact verification (Thorne et al., 2018). However, existing approaches treat contradiction as binary (entailment vs. contradiction). Bio-Agent OS introduces a three-way classification: **contradiction**, **governed exception**, and **neutral**, reflecting the enterprise reality where approved overrides must coexist with default policies.

---

## 3. Architecture

Bio-Agent OS consists of five layers, mirroring the human memory consolidation pipeline:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Agent Process                               │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  L1 Working Memory (Attention Scheduler + Homeostasis)       │  │
│  │  ┌─────────────┐  ┌────────────────┐  ┌──────────────────┐  │  │
│  │  │ Raw Events  │→ │ Focus Set      │→ │ Context String   │  │  │
│  │  │ (TTL=2)     │  │ (top-k scored) │  │ (injected into   │  │  │
│  │  └─────────────┘  └────────────────┘  │  agent prompt)   │  │  │
│  │                                        └──────────────────┘  │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│                              │ sleep cycle (hippocampal replay)      │
│  ┌──────────────────────────▼────────────────────────────────────┐  │
│  │  Hippocampus (Sleep Consolidation Engine)                     │  │
│  │  label → compile → canonicalize → promote → reconcile         │  │
│  └──────┬───────────┬────────────────────┬───────────────────────┘  │
│         │           │                    │                          │
│         ▼           ▼                    ▼                          │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────────────────────┐ │
│  │ Episodes │ │ L2 Semantic  │ │ Persona (Self-Model)            │ │
│  │ (ground  │ │ Memory       │ │  ┌─────────────────────┐       │ │
│  │  truth)  │ │ (procedural, │ │  │ Core Rules (human)  │       │ │
│  │          │ │  semantic,   │ │  │ Project Rules (auto) │       │ │
│  │          │ │  exception)  │ │  │ Adaptive Rules (low) │       │ │
│  │          │ │              │ │  └─────────────────────┘       │ │
│  └──────┬───┘ └──────────────┘ └──────────┬──────────────────────┘ │
│         │                                  │                        │
│         ▼                                  ▼                        │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Knowledge Graph (Belief Network)                               ││
│  │  ┌────────┐  supports  ┌──────────┐  governed_exception_for    ││
│  │  │Episode │───────────→│Rule Node │←────────────────────────┐  ││
│  │  └────────┘            └──────────┘                         │  ││
│  │                             │                          ┌────┴──┐││
│  │                       conflicts_with              │Override││
│  │                             │                     │Rule   │││
│  │                             ▼                     └───────┘││
│  │                        ┌──────────┐                        ││
│  │                        │Challenged│                        ││
│  │                        │Rule      │                        ││
│  │                        └──────────┘                        ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  Background Jobs                                                ││
│  │  • Garbage Collector (Ebbinghaus decay + TTL-based pruning)     ││
│  │  • Graph Builder (entity/relation extraction)                   ││
│  │  • Dream Cycle (Hippocampus.dream())                           ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

**Figure 1.** Bio-Agent OS architecture. Arrows indicate data flow during consolidation.

### 3.1 L1 Working Memory

L1 implements an attention-based short-term buffer. Each entry has fields: `content`, `source`, `metadata`, `timestamp`, `nights_passed`, `ttl`, `salience`, `recency_score`, `novelty`, `severity`, `task_relevance`, `unresolved_status`, and `attention_score`.

Unlike a pure FIFO queue, L1 uses a **weighted attention function** to compute a composite score for each entry:

```
attention(e) = G · (w_task · task_relevance(e)
                   + w_novelty · novelty(e)
                   + w_unresolved · unresolved(e)
                   + w_recency · recency(e)
                   + w_severity · severity(e))
```

where *G* is the global gain and *w*_i are learnable weights (see §4.1 on homeostasis).

### 3.2 Hippocampus (Sleep Consolidation)

The Hippocampus performs consolidation in five stages:

1. **Label**: An LLM assigns `topic`, `importance_score`, `is_junk_or_transient`, and `user_state` to raw input.
2. **Compile**: The LLM extracts structured memory: `episodic_summary`, `semantic_memory`, `procedural_memory`, `exception_memory`, `identity_rule`, `confidence`, `scope`.
3. **Canonicalize**: Domain-specific rule templates ensure consistent formatting (e.g., "Never use git push -f on X branch in production.").
4. **Promote**: Rules are added to the Persona with deduplication. Repeated observations increment `support_count` and advance the state machine.
5. **Reconcile**: The ContradictionResolver is invoked to detect and resolve conflicts (see §4.3).

### 3.3 L2 Semantic Memory

L2 stores three types of long-term memories:
- **Semantic**: Generalized knowledge ("Peer dependency mismatches are common after Vite upgrades")
- **Procedural**: Action templates ("Check lockfile versions before changing dependencies")
- **Exception**: Critical caveats ("Tenant X breaks if Vite is upgraded without pinning plugins first")

Each memory is stored as an embedding vector (via Qdrant or in-memory fallback) with metadata including `importance`, `mode_hints`, `risk_level`, `stress_state`, `workspace_id`, and `project_version`.

**State-dependent retrieval** applies contextual boosting:
- Mode match (e.g., `debug` mode favors exception memories): +3.0
- Exception preference in failure/deploy states: +2.5
- Workspace match: +1.5
- Stress-state match: +1.0

### 3.4 Persona (Self-Model)

The Persona maintains a three-layer identity:

| Layer | Source | Mutability | Example |
|:---|:---|:---|:---|
| **Core** | Human-approved | Immutable | "Never bypass authentication checks." |
| **Project** | Agent-learned, evidence-backed | Mutable with evidence | "Never force push in production." |
| **Adaptive** | Agent-observed, low confidence | Highly mutable | "This workspace dislikes wildcard imports." |

Rules carry provenance metadata: `evidence_episode_ids`, `support_count`, `contradiction_count`, `confidence`, `state`, `created_at`, `valid_from`, `valid_to`, `superseded_by`.

### 3.5 Knowledge Graph (Belief Network)

The KG stores typed relations between rules, episodes, and entities:

| Relation | Meaning |
|:---|:---|
| `supports` | Episode provides evidence for a rule |
| `conflicts_with` | Two rules logically contradict |
| `governed_exception_for` | Override rule is a conditional exception of a default rule |
| `approved_by_policy` | Override is sanctioned by a specific policy |
| `requires_human_approval` | Override cannot be enacted without human sign-off |
| `expires_override_at` | Override is valid only within a temporal window |

---

## 4. Key Mechanisms

### 4.1 Homeostatic Attention Scheduling

Conventional attention weights are static hyperparameters. In biological neural systems, neuromodulatory gain adjusts dynamically based on arousal and stress (Turrigiano, 2008). We implement this as a **homeostasis function** that computes dynamic weights from the recent entry history:

```python
stress = 0.45·unresolved_ratio + 0.35·severity_avg + 0.20·failure_streak
decay = max(0.35, 1.0 − min(hours_since_failure / 8.0, 0.65))
stress_level = clamp(stress · decay)

# Dynamic weights
severity_weight = 0.15 + 0.20·stress_level      # [0.15, 0.35]
unresolved_weight = 0.20 + 0.10·stress_level     # [0.20, 0.30]
recency_weight = max(0.05, 0.15 − 0.05·stress)   # [0.10, 0.15]
novelty_weight = max(0.10, 0.20 − 0.05·stress)   # [0.15, 0.20]
global_gain = 1.0 + stress_level                  # [1.0, 2.0]
```

**Behavioral implications:**
- Under normal operation: All five factors contribute roughly equally.
- Under stress (failure streak): Severity and unresolved status dominate attention, recency and novelty are suppressed. Global gain amplifies all scores.
- After recovery (8+ hours without failure): Stress decays via `decay_factor`, with a floor of 0.35 to maintain vigilance.

This produces an agent that *focuses harder on critical failures* when under stress and *relaxes* after a recovery period—analogous to the human fight-or-flight response.

### 4.2 Ebbinghaus-Decay Forgetting (Synaptic Pruning)

The Garbage Collector applies time-decay to L1 entries past their TTL:

```
W(t) = W₀ · e^(−λ · (t − TTL))
```

where *W₀* is the initial importance score, *λ* is the decay rate (default 0.3), and *t* is nights passed. If W(t) < *threshold* (default 3.0), the entry is pruned.

This produces a forgetting curve where low-importance events are forgotten within 2–3 sleep cycles, while high-importance events (importance ≥ 8) persist for 5+ cycles before being "forgotten" (or encoded into L2 before that).

### 4.3 Hybrid Contradiction Detection

Rule conflicts are detected using a two-tier system:

**Tier 1: Heuristic detector** (zero latency)
1. Polarity analysis: Classify each rule as *negative* (contains "never", "don't", "avoid") or *positive* (contains "allow", "always", "must").
2. Semantic core extraction: Remove polarity markers, retain content tokens.
3. Token overlap: If overlap ≥ 0.6 and opposite polarity → *contradiction*.
4. Governed exception check: If one rule is a conditional exception (contains "only", "approval", "audit", "hotfix") and the other is a general negative policy → *governed_exception*.

**Tier 2: NLI detector** (LLM-backed, cached)
When the heuristic is uncertain (returns "neutral" but domain overlap exists), the system escalates to an NLI classifier:

```
Prompt: "Classify the relation between Rule A and Rule B:
         - contradiction: cannot both be followed
         - governed_exception: one is a conditional override
         - neutral: neither"
```

The NLI decision is persisted in a SQLite cache table with a normalized, sorted key:

```
cache_key = sorted([f"{scope}::{normalize(text_A)}", 
                     f"{scope}::{normalize(text_B)}"])
```

This ensures symmetric lookup (A,B) = (B,A) and eliminates redundant inference for previously classified pairs.

### 4.4 The Governed Exception Pattern

In enterprise environments, policies rarely exist in isolation. A deployment policy ("Never force push") may have legitimate exceptions ("Allow force push during approved hotfix"). Existing memory systems classify this as a contradiction and deprecate the weaker rule.

Bio-Agent OS recognizes this pattern at the architectural level:

1. **Detection**: If rule A is a general negative policy and rule B is a conditional positive with ≥ 2 condition markers ("approval", "audit", "only", "hotfix", etc.), the pair is classified as a *governed exception*.
2. **Graph annotation**: The exception rule receives edges: `governed_exception_for(B → A)`, `approved_by_policy(B → policy_node)`, `requires_human_approval(B → human_approval)`, `expires_override_at(B → window)`.
3. **Safety guard injection**: The retrieval service injects both the default rule and the approved override into the agent's context, with explicit conditions for when the override applies.

This preserves both rules and enables nuanced reasoning about when overrides are appropriate.

### 4.5 Belief Lifecycle State Machine

```
 proposed ──(support)──→ reinforced ──(threshold)──→ stable
     |                       |                          |
     └──(conflict,weaker)─── └──(conflict,weaker)──────└──→ challenged
                                                              |
                                                     (stronger rule)
                                                              |
                                                              ▼
                                                         deprecated
                                                              |
                                                         (archived)
```

**Figure 2.** Belief lifecycle state transitions. Support count increments advance the state; conflict with a stronger rule triggers challenge or deprecation.

Transitions are triggered by:
- **Support**: Repeated evidence from independent episodes → `proposed → reinforced → stable`
- **Challenge**: A conflicting rule with higher confidence → `* → challenged`
- **Deprecation**: Explicit supersession by a stronger rule → `* → deprecated`
- **Governed exception**: The exception rule is reinforced without deprecating the default → both survive

---

## 5. Evaluation

### 5.1 Detector Benchmark

We evaluate the contradiction detector on an 8-pair benchmark spanning four enterprise domains:

| # | Pair Name | Domain | Ground Truth |
|:-:|:---|:---|:---|
| 1 | semantic-deploy-window | Deploy Scheduling | contradiction |
| 2 | tenant-approved-override | Tenant Governance | governed_exception |
| 3 | neutral-stack-choice | Architecture | neutral |
| 4 | security-time-conflict | Security Rotation | contradiction |
| 5 | migration-approved-override | DB Migration | governed_exception |
| 6 | tenant-neutral-separation | Mixed/Neutral | neutral |
| 7 | deploy-window-conflict | Deploy Scheduling | contradiction |
| 8 | security-approved-override | Security Override | governed_exception |

**Table 1.** Detector benchmark pairs. Each pair is designed to test a specific failure mode.

#### Results

| Detector | Accuracy | Precision | False Positive | False Negative |
|:---|:---:|:---:|:---:|:---:|
| Heuristic-only | 4/8 (50%) | 1.00 | 0 | 4 |
| Hybrid (heuristic + NLI) | **8/8 (100%)** | **1.00** | **0** | **0** |

**Table 2.** Detector benchmark results across 2 evaluation runs with Gemma-4 E2B. Both runs produced identical results.

The heuristic detector fails on all three **temporal/scheduling contradictions** (pairs 1, 4, 7) because these pairs share no polarity-marked keywords—the contradiction is purely semantic ("overnight" vs. "10 AM"). The heuristic also misses the security override (pair 8) due to insufficient token overlap after polarity stripping.

The hybrid detector correctly classifies all 8 pairs by escalating uncertain cases to the NLI tier, which recognizes the semantic incompatibility of temporal constraints and the governed exception structure of approved overrides.

#### Cache Efficiency

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| NLI live calls | 8 | 8 |
| NLI cache hits | 8 | 8 |
| Repeat-pass cache confirmed | 8/8 | 8/8 |

**Table 3.** NLI cache statistics. The repeat pass (re-classifying all 8 pairs after initial classification) achieves 100% cache hit rate, eliminating all redundant LLM calls.

### 5.2 End-to-End Consolidation

We evaluate the full memory pipeline on a 6-task sequence simulating a real coding agent workflow:

| Task | Mode | Content |
|:---|:---|:---|
| debug-1 | debug | Build failed with peer dependency mismatch after Vite upgrade |
| debug-2 | debug | npm install failed because plugin major version mismatch |
| policy-1 | deploy | Team policy: never force push on frontend in production |
| deploy-1 | deploy | Deploy release candidate, avoid risky branch operations |
| hotfix-1 | deploy | Hotfix runbook: allow force push with approval + audit |
| hotfix-2 | deploy | Incident response validated hotfix exception |

Each task goes through: `ingest → label → compile → consolidate → reconcile`.

#### Results (Gemma-4 E2B, 2 runs)

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| Total LLM calls | 13 | 12 |
| Total tokens | 15,816 | 14,410 |
| Total latency (s) | 92.9 | 78.1 |
| Retention rate (3 probes) | 3/3 (1.0) | 2/3 (0.67) |
| Task success rate (3 checks) | 2/3 (0.67) | 1/3 (0.33) |
| Rules generated | 6 | 6 |
| Governed exception edges | 2 | 2 |

**Table 4.** End-to-end consolidation results. Six tasks produce six rules, with the hotfix rules correctly linked as governed exceptions of the force-push ban.

#### Retention Probes

Three retrieval probes test whether the agent can recall specific memories after consolidation:

1. **dependency-retention**: "vite plugin dependency mismatch procedure" → expects L2 results mentioning "dependency"
2. **policy-retention**: "frontend force push policy" → expects graph results mentioning "push -f"
3. **hotfix-exception-retention**: "hotfix branch exception with approval" → expects L2 exception memory about "hotfix"

Run 1 achieves 3/3 retention. Run 2 achieves 2/3 (the hash-based embedding fallback produces lower-quality retrieval for some queries).

#### Attention Homeostasis Under Stress

After processing 6 consecutive deploy/debug tasks, the attention state shows stress accumulation:

```
stress_level: 0.744
global_gain: 1.744
failure_streak: 6
severity_weight: 0.299  (baseline: 0.15, +99%)
unresolved_weight: 0.274 (baseline: 0.20, +37%)
recency_weight: 0.113   (baseline: 0.15, −25%)
novelty_weight: 0.163   (baseline: 0.20, −19%)
```

**Table 5.** Attention homeostasis state after 6 stress-inducing tasks. Severity and unresolved weights increase dramatically, while recency and novelty are suppressed. Global gain is 1.744×, amplifying all attention scores.

### 5.3 Approved Override Suite (Multi-Domain)

We additionally evaluate on a 9-task multi-domain suite spanning tenant governance, DB migration, and security override:

| Domain | Default Policy | Approved Exception |
|:---|:---|:---|
| Tenant (ERP) | Never rename customer codes after onboarding | Allow rename for Tenant A with finance approval |
| Migration (DB) | Never run destructive migration in business hours | Allow during recovery window with DBA approval |
| Security (Auth) | Never disable MFA in production | Allow temporary bypass with incident ticket + expiry |

#### Results

| Metric | Run 1 | Run 2 |
|:---|:---:|:---:|
| Reinforced rules | 3 | 3 |
| Governed exception edges | 2 | 2 |
| Approved-by-policy edges | 2 | 2 |
| Expiring override edges | 1 | 1 |
| Suite resolved | ✅ | ✅ |

**Table 6.** Multi-domain approved override results. All three domains correctly produce governed exception pairs with appropriate governance edges.

---

## 6. Comparison with Existing Frameworks

| Feature | Bio-Agent OS | Letta v3 | Mem0 v2 | Zep/Graphiti |
|:---|:---:|:---:|:---:|:---:|
| Memory tiers | 4 (L1/L2/Graph/Persona) | 2 (main/archival) | 1 (flat store) | 2 (temporal KG) |
| Forgetting mechanism | Ebbinghaus decay | ✗ | ✗ | ✗ |
| Attention homeostasis | Dynamic weights + stress decay | ✗ | ✗ | ✗ |
| Belief lifecycle | 6 states | ✗ | Overwrite | ✗ |
| Contradiction detection | Hybrid (heuristic + NLI) | ✗ | ✗ | ✗ |
| NLI cache | SQLite-backed, persistent | ✗ | ✗ | ✗ |
| Governed exceptions | ✓ (with graph governance) | ✗ | ✗ | ✗ |
| Human approval gate | ✓ (ApprovalQueue) | ✗ | ✗ | ✗ |
| Lineage/provenance | Episode → Rule → Override chain | Basic | ✗ | Temporal |
| Multi-DB (SQLite + PG) | ✓ (auto-translation) | PG only | PG only | PG only |
| Plugin system | ✓ (OpenClaw, SWE-Agent) | ✓ | ✓ | ✗ |
| Docker ready | ✓ | ✓ | ✓ | ✓ |
| Open source | MIT | Apache 2.0 | Apache 2.0 | MIT |

**Table 7.** Feature comparison with major agent memory frameworks.

---

## 7. Discussion

### 7.1 Strengths

**Bio-fidelity with practical utility.** The combination of Ebbinghaus decay, homeostatic attention, and sleep consolidation produces agent behavior that is naturalistic and predictable. Agents under stress focus on critical failures; agents after recovery periods relax their vigilance. This is not merely aesthetic—it directly impacts retrieval quality and prevents attention dilution.

**Governed exceptions as first-class citizens.** Enterprise environments are rife with policy exceptions. The Governed Exception Pattern prevents the common failure mode where legitimate overrides are discarded by a naive contradiction resolver.

**NLI cache economics.** With an 8-pair benchmark, the cache eliminates 100% of repeat inference cost. In production, where agents may re-evaluate the same rule pairs across thousands of sessions, this represents substantial compute savings.

### 7.2 Limitations

**Embedding quality.** The hash-based embedding fallback (used when no commercial embedding API is available) produces lower retrieval quality than production embedding models. Run 2's lower retention rate (0.67 vs. 1.0) is partially attributable to hash collision artifacts.

**Cache staleness.** The NLI cache currently lacks TTL-based expiration. If rule text is modified but its normalized form is unchanged, stale cache entries may persist. We recommend a 7-day TTL for production deployments.

**Benchmark scale.** Our 8-pair detector benchmark, while diverse in domain coverage, is small by NLI benchmark standards. We plan to extend to 50+ pairs covering additional domains (healthcare, legal, financial compliance).

**Single-tenant.** The current runtime builds a single agent instance. Multi-tenant deployments (e.g., one Bio-Agent OS instance per user in a SaaS setting) require tenant-level database isolation, which is not yet implemented.

### 7.3 Ethical Considerations

Bio-Agent OS's belief management system raises important questions about AI autonomy. The ability for an agent to *learn rules* from experience—including potentially incorrect rules—carries risks. We mitigate these through:

1. **Promotion thresholds**: Rules require 2–3 independent evidence episodes before reaching `stable` state.
2. **ApprovalQueue**: Sensitive rules (containing "production", "auth", "security", "delete") require human approval before promotion.
3. **Fallback action**: Challenged beliefs are explicitly marked as non-authoritative, and destructive actions require explicit approval regardless of belief state.
4. **Core layer immutability**: Human-approved core rules cannot be deprecated by the agent.

---

## 8. Conclusion and Future Work

Bio-Agent OS demonstrates that biologically-inspired memory dynamics—forgetting, stress-responsive attention, belief lifecycle management—are not merely theoretical novelties but produce practical improvements in agent memory systems. The hybrid NLI detector achieves 100% accuracy on semantic conflicts that keyword-based approaches miss, while the NLI cache eliminates redundant inference costs.

**Future directions include:**

1. **LoCoMo benchmark integration** (Wang et al., 2024): Evaluate long-conversation memory retention on standardized benchmarks.
2. **Multi-model NLI**: Compare detector accuracy across Gemma, Llama, Qwen, and GPT-4o backends.
3. **Temporal decay for NLI cache**: 7-day TTL with confidence-weighted invalidation.
4. **Observability dashboard**: Real-time visualization of attention homeostasis, belief lifecycle, and conflict resolution via Streamlit/Gradio.
5. **Plugin ecosystem**: Community-contributed plugins for Cursor, Windsurf, and multi-agent orchestrators.

Bio-Agent OS is available at [github.com/locaith/bio-memory-ai-locaith](https://github.com/locaith/bio-memory-ai-locaith) under the MIT license.

---

## References

- Chhablani, G., et al. (2024). Mem0: The Memory Layer for Personalized AI. *arXiv preprint*.
- Dudai, Y. (2004). The neurobiology of consolidations, or, how stable is the engram? *Annual Review of Psychology*, 55, 51–86.
- Ebbinghaus, H. (1885). *Über das Gedächtnis*. Duncker & Humblot.
- Kumaran, D., Hassabis, D., & McClelland, J. L. (2016). What learning systems do intelligent agents need? *Trends in Cognitive Sciences*, 20(7), 512–534.
- McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). Why there are complementary learning systems in the hippocampus and neocortex. *Psychological Review*, 102(3), 419.
- Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S. G., Stoica, I., & Gonzalez, J. E. (2024). MemGPT: Towards LLMs as operating systems. *ICLR 2024*.
- Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). Generative agents: Interactive simulacra of human behavior. *UIST 2023*.
- Thorne, J., Vlachos, A., Christodoulopoulos, C., & Mittal, A. (2018). FEVER: A large-scale dataset for fact extraction and verification. *NAACL 2018*.
- Turrigiano, G. G. (2008). The self-tuning neuron: Synaptic scaling of excitatory synapses. *Cell*, 135(3), 422–435.
- Wang, S., et al. (2024). LoCoMo: Long context memory benchmark for LLM agents. *arXiv preprint*.
- Wayne, G., et al. (2018). Unsupervised predictive memory in a goal-directed agent. *arXiv preprint*.

---

## Appendix A: Implementation Details

| Component | Lines of Code | Storage |
|:---|:---:|:---|
| L1 Working Memory | 475 | SQLite |
| L2 Semantic Memory | 340 | Qdrant + SQLite |
| Knowledge Graph | 563 | SQLite |
| Episode Store | 260 | SQLite |
| Persona | 492 | SQLite (encrypted) |
| Hippocampus | 683 | — |
| Reconciliation | 576 | SQLite (NLI cache) |
| Retrieval Service | 272 | — |
| DB Adapter | 194 | SQLite / PostgreSQL |
| Approval Queue | 237 | SQLite |
| Garbage Collector | 147 | — |
| Graph Builder | 130 | — |
| **Total** | **~8,200+** | |

**Table A1.** Module breakdown. Total codebase exceeds 8,200 lines of Python.

## Appendix B: Reproducibility

All experiments can be reproduced with:

```bash
git clone https://github.com/locaith/bio-memory-ai-locaith
cd bio-memory-ai-locaith
pip install -e ".[ollama,dev]"
ollama pull gemma4:e2b

# Run benchmark (2 evaluation runs)
REAL_EVAL_RUNS=2 python scripts/run_real_eval.py

# Run unit tests (38 tests)
pytest tests/test_components.py -v
```

Reports are written to `benchmark_reports/`.

## Appendix C: Detector Benchmark Raw Data

Pair: **semantic-deploy-window**
```
Rule A: "Deploy production releases overnight only."
Rule B: "Every production release must happen at 10 AM every business day."
Heuristic: neutral (MISS — no shared polarity keywords)
Hybrid/NLI: contradiction ✓ (recognized temporal incompatibility)
```

Pair: **security-approved-override**
```
Rule A: "Never disable MFA in production."
Rule B: "Allow temporary MFA bypass only with human approval, an incident 
         ticket, and a documented expiry window."
Heuristic: neutral (MISS — insufficient token overlap after polarity strip)
Hybrid/NLI: governed_exception ✓ (recognized conditional override)
```
