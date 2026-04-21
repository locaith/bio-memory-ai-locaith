"""
Shared runtime builder for API, SDK, and CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.graph_builder import GraphBuilder
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.core.audit_log import AuditLog
from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.dream_journal import DreamJournal
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.memory_health import MemoryHealthMonitor
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.retrieval_service import RetrievalService
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


@dataclass
class BioAgentRuntime:
    agent_name: str
    storage_dir: str
    engine: LLMEngine
    persona: Persona
    router_ai: IntentRouter
    l1: L1WorkingMemory
    l2: L2SemanticMemory
    kg: KnowledgeGraph
    episodes: EpisodeStore
    exact_memory: ExactMemoryStore
    hippo: Hippocampus
    gc: GarbageCollector
    graph_builder: GraphBuilder
    health_monitor: MemoryHealthMonitor
    dream_journal: DreamJournal
    audit_log: AuditLog
    approval_queue: ApprovalQueue
    retrieval_service: RetrievalService


def build_runtime(agent_name: str, storage_dir: str) -> BioAgentRuntime:
    load_dotenv()
    engine = LLMEngine.from_env()
    persona = Persona(name=agent_name, storage_dir=storage_dir)
    router_ai = IntentRouter(engine=engine)
    l1 = L1WorkingMemory(agent_name=agent_name, storage_dir=storage_dir)
    l2 = L2SemanticMemory(agent_name=agent_name, storage_dir=storage_dir)
    kg = KnowledgeGraph(agent_name=agent_name, storage_dir=storage_dir)
    episodes = EpisodeStore(agent_name=agent_name, storage_dir=storage_dir)
    exact_memory = ExactMemoryStore(agent_name=agent_name, storage_dir=storage_dir)
    if exact_memory.count == 0 or os.getenv("BIO_AGENT_REINDEX_EXACT_MEMORY", "0") == "1":
        exact_memory.reindex_from_episodes(episodes)
    dream_journal = DreamJournal(agent_name=agent_name, storage_dir=storage_dir)
    audit_log = AuditLog(agent_name=agent_name, storage_dir=storage_dir)
    approval_queue = ApprovalQueue(agent_name=agent_name, storage_dir=storage_dir)
    retrieval_service = RetrievalService(
        l2=l2,
        graph=kg,
        episodes=episodes,
        exact_memory=exact_memory,
        persona=persona,
        approval_queue=approval_queue,
    )
    hippo = Hippocampus(
        engine=engine,
        l1=l1,
        persona=persona,
        l2=l2,
        episodes=episodes,
        exact_memory=exact_memory,
        graph=kg,
        dream_journal=dream_journal,
        audit_log=audit_log,
        approval_queue=approval_queue,
        detector_mode=os.getenv("BIO_AGENT_CONFLICT_DETECTOR", "hybrid"),
    )
    gc = GarbageCollector(l1=l1, l2=l2)
    graph_builder = GraphBuilder(engine=engine, graph=kg)
    health_monitor = MemoryHealthMonitor(
        l1=l1,
        l2=l2,
        persona=persona,
        episodes=episodes,
        graph=kg,
        exact_memory=exact_memory,
    )
    return BioAgentRuntime(
        agent_name=agent_name,
        storage_dir=storage_dir,
        engine=engine,
        persona=persona,
        router_ai=router_ai,
        l1=l1,
        l2=l2,
        kg=kg,
        episodes=episodes,
        exact_memory=exact_memory,
        hippo=hippo,
        gc=gc,
        graph_builder=graph_builder,
        health_monitor=health_monitor,
        dream_journal=dream_journal,
        audit_log=audit_log,
        approval_queue=approval_queue,
        retrieval_service=retrieval_service,
    )
