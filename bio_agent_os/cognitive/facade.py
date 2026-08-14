from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any

logger = logging.getLogger("bio_agent_os.memory_os")

from .causal import CausalMemoryEngine
from .counterfactual import CounterfactualSimulator
from .dream_engine import DreamEngine, DreamReport
from .epistemics import EvidenceLedger
from .event_store import SQLiteEventStore
from .governance import GovernanceEngine
from .immune import ImmuneDecision, MemoryImmuneSystem
from .memory_store import SQLiteMemoryStore
from .models import (
    AccessContext,
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    EventRecord,
    ExecutionOutcome,
    MemoryType,
    Modality,
    ProspectiveTrigger,
    SecurityLabel,
    TrustTier,
    VerificationStatus,
)
from .prospective import ProspectiveMemory
from .reconstruction import CognitiveReconstruction, CognitiveReconstructor
from .retrieval import HybridRetrievalEngine, RetrievalResult
from .projection_capability import enqueueable
from .self_model import SelfModel
from .shadow import (
    COGNITIVE_MEMORY,
    ProjectionMode,
    ShadowMemoryStore,
    current_mode,
)
from .sqlite_utils import resolve_runtime_path
from .world_model import WorldModel
from bio_agent_os.context_fabric import (
    AgentCheckpoint, CheckpointManager, ContextBlockStore, ContextCompiler,
    ContextMetrics, ContextPacket, PredictivePrefetcher, PrefetchPlan, TimedOperation,
)


class MemoryOS:
    """High-level facade for Bio-AGI Memory OS v0.8 Alpha."""

    def __init__(self, db_path: str | Path = ":memory:", *, projection_mode: str | None = None,
                 embedder: Any | None = None):
        """`embedder` is optional and injected, never imported.

        With one, retrieval can tell a paraphrase from a coincidence and can
        refuse to answer; without one it behaves exactly as it did before, which
        is what nine canary runs hardened. Anything with
        `.embed(text) -> list[float]` works. See `cognitive/semantic_index.py`.
        """
        # Six stores below open six connections. With plain ":memory:" SQLite
        # gives each of them a *private* database, so they cannot see one
        # another and any consistency test passes while proving nothing. The
        # path is resolved once here to a shared in-memory URI, and the anchor
        # held inside sqlite_utils keeps that database alive for the runtime.
        path = resolve_runtime_path(db_path)
        self.db_path = path
        self.events = SQLiteEventStore(path)
        self.memories = SQLiteMemoryStore(path)
        self.governance = GovernanceEngine()
        self.immune = MemoryImmuneSystem()
        self.retrieval = HybridRetrievalEngine(self.memories, self.governance, embedder=embedder)
        self.evidence = EvidenceLedger(self.events)
        self.world_model = WorldModel()
        self.self_model = SelfModel(path)
        self.prospective = ProspectiveMemory(path)
        self.causal = CausalMemoryEngine()
        self.counterfactual = CounterfactualSimulator(self.causal)
        self.dream_engine = DreamEngine()
        self.reconstructor = CognitiveReconstructor(self.events, self.memories)
        self.context_blocks = ContextBlockStore(path)
        self.context_compiler = ContextCompiler(self.context_blocks)
        self.checkpoints = CheckpointManager(path)
        self.prefetcher = PredictivePrefetcher(self.retrieval)
        self.context_metrics = ContextMetrics()
        self.quarantine: list[dict[str, Any]] = []

        # Projection mode. Default is legacy and stays legacy unless the
        # environment says otherwise, so existing behaviour is unchanged for
        # everyone who does nothing.
        self.projection_mode = current_mode(projection_mode)
        # Shadow output lives on the memory connection but in its own table,
        # so production recall cannot reach it by construction.
        self.shadow_memories = ShadowMemoryStore(self.memories.conn)

    def observe(
        self,
        *,
        tenant_id: str,
        actor: str,
        source: str,
        content: str,
        workspace_id: str | None = None,
        trust_tier: TrustTier = TrustTier.AGENT_OBSERVATION,
        security_label: SecurityLabel = SecurityLabel.INTERNAL,
        valid_from: str | None = None,
        valid_to: str | None = None,
        observed_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        modality: Modality = Modality.TEXT,
        epistemic_status: EpistemicStatus = EpistemicStatus.OBSERVED,
    ) -> EventRecord:
        """Record an observation.

        `observed_at` states when the observation happened. It is the one
        temporal field that previously had no route in, while `valid_from` and
        `valid_to` did — so a caller replaying a history could say when a fact
        was *true* but not when it was *learned*, and every memory in a
        simulated two-year history landed with an age of zero. `None` keeps the
        previous behaviour: the moment of the call.
        """
        decision = self.immune.inspect(
            content,
            memory_type=MemoryType.EPISODIC,
            trust_tier=trust_tier,
            epistemic_status=epistemic_status,
            source=source,
            persistent=False,
        )
        event = EventRecord(
            tenant_id=tenant_id,
            actor=actor,
            source=source,
            payload={"content": decision.redacted_content or content},
            workspace_id=workspace_id,
            trust_tier=trust_tier,
            security_label=security_label,
            valid_from=valid_from,
            valid_to=valid_to,
            **({"observed_at": observed_at} if observed_at else {}),
            metadata={**(metadata or {}), "immune_reasons": list(decision.reasons), "risk_score": decision.risk_score},
            modality=modality,
            epistemic_status=epistemic_status,
        )
        return self.events.append(event, projection_types=self._projection_types())

    def _projection_types(self) -> tuple[str, ...]:
        """What this observation owes, given the mode and what can be built.

        Legacy owes nothing, which is exactly the behaviour that shipped.
        Shadow owes a cognitive_memory job and nothing else. The other types
        have no builder, and enqueueing work that can only dead-letter would
        turn a missing capability into noise.

        **The hippocampus label is deliberately not here**, and the 36-hour A/B
        soak of 2026-08-11 is why. Enqueueing a second outbox row per
        observation cost +0.196 ms at p95 and took the share of cycles breaching
        the 1.0 ms SLO from 0.21% to 2.31%. Nothing degraded over 36 hours and
        no job was ever lost — the write path handled it — but it was a real
        cost paid on every single write, for ever.

        The outbox exists so a projection cannot be lost: leases, retries,
        dead-letter, exactly-once. A memory needs that, because a memory that
        goes missing is gone. A label does not: it is derived data, recomputable
        from an immutable event at any later time. "Unlabelled" is a property of
        the data, discoverable by a join, not a fact that has to be remembered
        in a queue at the moment of writing.

        So labels are enqueued by `backfill_labels()`, off this path, in
        batches. `observe()` pays nothing, and stays byte-identical to the path
        nine canary runs hardened. See `reports/join_soak_verdict.md`.

        Wrapped because deciding must never be able to fail an observe() that
        would otherwise have succeeded.
        """
        try:
            if self.projection_mode is ProjectionMode.LEGACY:
                return ()
            supported, skipped = enqueueable((COGNITIVE_MEMORY,))
            if skipped:
                logger.warning("skipping unsupported projection types: %s", skipped)
            return supported
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to resolve projection types; falling back to legacy")
            return ()

    def remember(
        self,
        *,
        event: EventRecord,
        memory_type: MemoryType,
        content: str,
        confidence: float = 0.6,
        importance: float = 0.5,
        salience: float = 0.5,
        utility: float = 0.5,
        lifecycle_state: BeliefState = BeliefState.PROPOSED,
        approved_by: str | None = None,
        governed_exception_for: str | None = None,
        approval_expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        allowed_agents: list[str] | None = None,
        allowed_roles: list[str] | None = None,
        purpose_allowlist: list[str] | None = None,
        structured_content: dict[str, Any] | None = None,
        epistemic_status: EpistemicStatus | None = None,
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
        counterevidence_event_ids: list[str] | None = None,
        applicable_context: dict[str, Any] | None = None,
        simulation_id: str | None = None,
    ) -> CognitiveMemory | ImmuneDecision:
        persistent = memory_type in {
            MemoryType.PROCEDURAL,
            MemoryType.BELIEF,
            MemoryType.IDENTITY,
            MemoryType.POLICY,
            MemoryType.EXCEPTION,
            MemoryType.SELF_MODEL,
        }
        effective_epistemic = epistemic_status or event.epistemic_status
        decision = self.immune.inspect(
            content,
            memory_type=memory_type,
            trust_tier=event.trust_tier,
            epistemic_status=effective_epistemic,
            source=event.source,
            persistent=persistent,
        )
        if decision.quarantined:
            self.quarantine.append(
                {
                    "event_id": event.event_id,
                    "content": content,
                    "reasons": list(decision.reasons),
                    "risk_score": decision.risk_score,
                }
            )
            return decision
        memory = CognitiveMemory(
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            memory_type=memory_type,
            content=decision.redacted_content or content,
            source_event_ids=[event.event_id],
            structured_content=structured_content or {},
            confidence=max(0.0, min(confidence, 1.0)),
            importance=max(0.0, min(importance, 1.0)),
            salience=max(0.0, min(salience, 1.0)),
            utility=max(0.0, min(utility, 1.0)),
            trust_tier=event.trust_tier,
            security_label=event.security_label,
            valid_from=event.valid_from,
            valid_to=event.valid_to,
            # Inherited for the same reason as the two above: the claim was
            # learned when its event was observed, not when the row was written.
            # Without this a replayed history has every memory aged zero, and
            # `staleness.annotate` stamps the run date onto a two-year-old price.
            observed_at=event.observed_at,
            lifecycle_state=lifecycle_state,
            approved_by=approved_by,
            governed_exception_for=governed_exception_for,
            approval_expires_at=approval_expires_at,
            metadata=metadata or {},
            allowed_agents=allowed_agents or [],
            allowed_roles=allowed_roles or [],
            purpose_allowlist=purpose_allowlist or [],
            epistemic_status=effective_epistemic,
            verification_status=verification_status,
            counterevidence_event_ids=counterevidence_event_ids or [],
            applicable_context=applicable_context or {},
            modality=event.modality,
            simulation_id=simulation_id,
        )
        valid, reasons = self.governance.validate_promotion(memory)
        if not valid and lifecycle_state == BeliefState.STABLE:
            memory.lifecycle_state = BeliefState.PROPOSED
            memory.metadata["promotion_blocked"] = reasons
        stored = self.memories.put(memory)
        self.world_model.ingest(stored)
        self.context_blocks.invalidate_scope(stored.tenant_id, stored.workspace_id)
        return stored


    def bulk_ingest(
        self,
        *,
        tenant_id: str,
        actor: str,
        source: str,
        items: list[dict[str, Any]],
        workspace_id: str | None = None,
        trust_tier: TrustTier = TrustTier.AGENT_OBSERVATION,
        security_label: SecurityLabel = SecurityLabel.INTERNAL,
    ) -> tuple[list[CognitiveMemory], list[ImmuneDecision]]:
        """High-throughput atomic ingestion with the same immune/governance gates.

        Each item accepts ``content`` and optional ``memory_type``, confidence,
        importance, salience, utility, metadata, structured_content,
        epistemic_status and verification_status. Quarantined items are not
        written to either canonical event or memory stores.
        """
        events: list[EventRecord] = []
        memories: list[CognitiveMemory] = []
        rejected: list[ImmuneDecision] = []
        for item in items:
            content = str(item["content"])
            memory_type = item.get("memory_type", MemoryType.SEMANTIC)
            if isinstance(memory_type, str):
                memory_type = MemoryType(memory_type)
            epistemic = item.get("epistemic_status", EpistemicStatus.OBSERVED)
            if isinstance(epistemic, str):
                epistemic = EpistemicStatus(epistemic)
            verification = item.get("verification_status", VerificationStatus.UNVERIFIED)
            if isinstance(verification, str):
                verification = VerificationStatus(verification)
            effective_trust = item.get("trust_tier", trust_tier)
            if not isinstance(effective_trust, TrustTier):
                effective_trust = TrustTier(int(effective_trust))
            persistent = memory_type in {
                MemoryType.PROCEDURAL, MemoryType.BELIEF, MemoryType.IDENTITY,
                MemoryType.POLICY, MemoryType.EXCEPTION, MemoryType.SELF_MODEL,
            }
            decision = self.immune.inspect(
                content, memory_type=memory_type, trust_tier=effective_trust,
                epistemic_status=epistemic, source=source, persistent=persistent,
            )
            if decision.quarantined:
                rejected.append(decision)
                self.quarantine.append({
                    "content": content, "reasons": list(decision.reasons),
                    "risk_score": decision.risk_score, "source": source,
                })
                continue
            event = EventRecord(
                tenant_id=tenant_id, actor=actor, source=source,
                payload={"content": decision.redacted_content or content},
                workspace_id=item.get("workspace_id", workspace_id), trust_tier=effective_trust,
                security_label=item.get("security_label", security_label),
                valid_from=item.get("valid_from"), valid_to=item.get("valid_to"),
                metadata=item.get("event_metadata", {}), modality=item.get("modality", Modality.TEXT),
                epistemic_status=epistemic,
            )
            memory = CognitiveMemory(
                tenant_id=tenant_id, workspace_id=event.workspace_id, memory_type=memory_type,
                content=decision.redacted_content or content, source_event_ids=[event.event_id],
                structured_content=item.get("structured_content", {}),
                confidence=max(0.0, min(float(item.get("confidence", 0.6)), 1.0)),
                importance=max(0.0, min(float(item.get("importance", 0.5)), 1.0)),
                salience=max(0.0, min(float(item.get("salience", 0.5)), 1.0)),
                utility=max(0.0, min(float(item.get("utility", 0.5)), 1.0)),
                trust_tier=effective_trust, security_label=event.security_label,
                valid_from=event.valid_from, valid_to=event.valid_to,
                lifecycle_state=item.get("lifecycle_state", BeliefState.PROPOSED),
                governed_exception_for=item.get("governed_exception_for"),
                approved_by=item.get("approved_by"), approval_expires_at=item.get("approval_expires_at"),
                allowed_agents=item.get("allowed_agents", []), allowed_roles=item.get("allowed_roles", []),
                purpose_allowlist=item.get("purpose_allowlist", []), metadata=item.get("metadata", {}),
                epistemic_status=epistemic, verification_status=verification,
                counterevidence_event_ids=item.get("counterevidence_event_ids", []),
                applicable_context=item.get("applicable_context", {}), modality=event.modality,
                simulation_id=item.get("simulation_id"),
            )
            valid, reasons = self.governance.validate_promotion(memory)
            if not valid and memory.lifecycle_state == BeliefState.STABLE:
                memory.lifecycle_state = BeliefState.PROPOSED
                memory.metadata["promotion_blocked"] = reasons
            events.append(event)
            memories.append(memory)
        persisted_events = self.events.append_many(events) if events else []
        checksum_by_id = {event.event_id: event.checksum for event in persisted_events}
        # Event IDs stay stable; the memory projection only needs lineage IDs.
        if memories:
            self.memories.put_many(memories)
            for memory in memories:
                self.world_model.ingest(memory)
            scopes = {(memory.tenant_id, memory.workspace_id) for memory in memories}
            for scope_tenant, scope_workspace in scopes:
                self.context_blocks.invalidate_scope(scope_tenant, scope_workspace)
        return memories, rejected

    def recall(
        self,
        query: str,
        *,
        context: AccessContext,
        state: dict[str, Any] | None = None,
        as_of: str | None = None,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        return self.retrieval.recall(query, context, state=state, as_of=as_of, limit=limit)

    def reconstruct(
        self,
        query: str,
        *,
        context: AccessContext,
        state: dict[str, Any] | None = None,
        as_of: str | None = None,
        limit: int = 8,
    ) -> CognitiveReconstruction:
        selected = [item.memory for item in self.recall(query, context=context, state=state, as_of=as_of, limit=limit)]
        return self.reconstructor.reconstruct(query, context, selected)

    def add_prospective_trigger(self, tenant_id: str, trigger: ProspectiveTrigger) -> None:
        self.prospective.add(tenant_id, trigger)

    def due_actions(self, tenant_id: str, state: dict[str, Any]) -> list[ProspectiveTrigger]:
        return self.prospective.evaluate(tenant_id, state)

    def learn_capability(self, tenant_id: str, agent_id: str, capability: str, outcome: ExecutionOutcome) -> bool:
        return self.self_model.record(tenant_id, agent_id, capability, outcome)

    def dream(self, tenant_id: str, outcomes: list[ExecutionOutcome], source_event_ids: list[str]) -> DreamReport:
        return self.dream_engine.consolidate_procedures(tenant_id, outcomes, source_event_ids)

    def compile_context(
        self,
        query: str,
        *,
        context: AccessContext,
        goal: str | None = None,
        state: dict[str, Any] | None = None,
        token_budget: int = 4096,
        recall_limit: int = 40,
        include_simulations: bool = False,
        use_cache: bool = True,
    ) -> ContextPacket:
        started = perf_counter()
        effective_state = state or {}
        if use_cache:
            cached = self.context_compiler.cached_packet(
                query=query, context=context, goal=goal, state=effective_state, token_budget=token_budget
            )
            if cached is not None:
                self.context_metrics.record_compile(cached.metrics, (perf_counter() - started) * 1000.0)
                return cached
        results = self.recall(query, context=context, state=effective_state, limit=recall_limit)
        packet = self.context_compiler.compile(
            query=query, context=context, retrieval_results=results, goal=goal, state=effective_state,
            token_budget=token_budget, include_simulations=include_simulations, use_cache=use_cache,
        )
        self.context_metrics.record_compile(packet.metrics, (perf_counter() - started) * 1000.0)
        return packet

    def create_checkpoint(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        workspace_id: str | None,
        goal: str,
        completed_steps: tuple[str, ...] = (),
        pending_steps: tuple[str, ...] = (),
        open_hypotheses: tuple[str, ...] = (),
        uncertainties: tuple[str, ...] = (),
        tool_state: dict[str, Any] | None = None,
        self_state: dict[str, Any] | None = None,
        active_memory_ids: tuple[str, ...] = (),
        parent_checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentCheckpoint:
        checkpoint = AgentCheckpoint(
            tenant_id=tenant_id, agent_id=agent_id, workspace_id=workspace_id, goal=goal,
            completed_steps=completed_steps, pending_steps=pending_steps,
            open_hypotheses=open_hypotheses, uncertainties=uncertainties,
            tool_state=tool_state or {}, world_state=self.world_model.snapshot().entities,
            self_state=self_state or {}, active_memory_ids=active_memory_ids,
            parent_checkpoint_id=parent_checkpoint_id, metadata=metadata or {},
        )
        return self.checkpoints.save(checkpoint)

    def restore_checkpoint(
        self,
        tenant_id: str,
        *,
        checkpoint_id: str | None = None,
        agent_id: str | None = None,
        workspace_id: str | None = None,
    ) -> AgentCheckpoint | None:
        self.context_metrics.restore_calls += 1
        if checkpoint_id:
            return self.checkpoints.get(checkpoint_id, tenant_id)
        if not agent_id:
            raise ValueError("agent_id is required when checkpoint_id is omitted")
        return self.checkpoints.latest(tenant_id, agent_id, workspace_id)

    def prefetch(
        self,
        action: str,
        *,
        context: AccessContext,
        state: dict[str, Any] | None = None,
        limit: int = 8,
    ) -> tuple[PrefetchPlan, list[RetrievalResult]]:
        self.context_metrics.prefetch_calls += 1
        return self.prefetcher.execute(action, context, state, limit)


    def share_context_packet(
        self,
        packet: ContextPacket,
        *,
        source_agent_id: str,
        target_agent_id: str,
    ) -> list[str]:
        return self.context_blocks.share_blocks(
            [block.block_id for block in packet.blocks], tenant_id=packet.tenant_id,
            source_agent_id=source_agent_id, target_agent_id=target_agent_id,
        )

    def shared_context(self, tenant_id: str, target_agent_id: str, limit: int = 100):
        return self.context_blocks.shared_with(tenant_id, target_agent_id, limit)

    def context_metrics_snapshot(self) -> dict[str, Any]:
        return self.context_metrics.snapshot()

    def close(self) -> None:
        """Close all SQLite handles owned by the facade."""
        seen: set[int] = set()
        for component in (
            self.events, self.memories, self.self_model, self.prospective,
            self.context_blocks, self.checkpoints,
        ):
            conn = getattr(component, "conn", None)
            if conn is not None and id(conn) not in seen:
                try:
                    conn.close()
                except Exception:
                    pass
                seen.add(id(conn))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

