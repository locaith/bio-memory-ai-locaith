"""
Bio-Agent OS.

Portable bio-inspired memory infrastructure for coding agents, ERP agents,
and long-running autonomous systems.
"""

__version__ = "0.4.0"
__author__ = "Locaith Solution Tech"

from bio_agent_os.core.audit_log import AuditLog
from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.compaction import MemoryCompactor
from bio_agent_os.core.dream_journal import DreamJournal
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.memory_health import MemoryHealthMonitor
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.reconciliation import ContradictionResolver
from bio_agent_os.core.retrieval_service import RetrievalService
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.graph_builder import GraphBuilder

__all__ = [
    "AuditLog",
    "ApprovalQueue",
    "DreamJournal",
    "EpisodeStore",
    "GarbageCollector",
    "GraphBuilder",
    "Hippocampus",
    "IntentRouter",
    "KnowledgeGraph",
    "L1WorkingMemory",
    "L2SemanticMemory",
    "LLMEngine",
    "MemoryCompactor",
    "MemoryHealthMonitor",
    "Persona",
    "ContradictionResolver",
    "RetrievalService",
]
