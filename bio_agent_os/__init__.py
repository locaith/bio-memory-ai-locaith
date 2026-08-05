"""
Bio-Agent OS.

Portable bio-inspired memory infrastructure for coding agents, ERP agents,
and long-running autonomous systems.
"""

__version__ = "0.8.2rc1"
__author__ = "Locaith Solution Tech"

from bio_agent_os.core.async_sqlite_store import AsyncSQLiteStore
from bio_agent_os.core.audit_log import AuditLog
from bio_agent_os.core.approval_queue import ApprovalQueue
from bio_agent_os.core.compaction import MemoryCompactor
from bio_agent_os.core.db_adapter import PostgresAdapter, SQLiteAdapter
from bio_agent_os.core.dream_journal import DreamJournal
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.memory_health import MemoryHealthMonitor
from bio_agent_os.core.metrics_store import MetricsStore
from bio_agent_os.core.migration import MigrationSummary, SQLiteToPostgresMigrator
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.reconciliation import ContradictionResolver
from bio_agent_os.core.retrieval_service import RetrievalService
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.core.runtime import BioAgentRuntime, build_runtime
from bio_agent_os.core.sqlite_store import SQLiteStore
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.graph_builder import GraphBuilder
from bio_agent_os.rest_client import BioAgentRESTClient
from bio_agent_os.sdk import BioAgentSDK
from bio_agent_os.plugins import (
    OpenClawMemoryPlugin,
    SWEAgentMemoryPlugin,
    build_openclaw_plugin,
    build_swe_agent_plugin,
)

__all__ = [
    "AsyncSQLiteStore",
    "AuditLog",
    "BioAgentRuntime",
    "BioAgentSDK",
    "ApprovalQueue",
    "DreamJournal",
    "ExactMemoryStore",
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
    "MetricsStore",
    "MigrationSummary",
    "Persona",
    "PostgresAdapter",
    "BioAgentRESTClient",
    "SQLiteToPostgresMigrator",
    "ContradictionResolver",
    "RetrievalService",
    "OpenClawMemoryPlugin",
    "SWEAgentMemoryPlugin",
    "SQLiteAdapter",
    "SQLiteStore",
    "build_openclaw_plugin",
    "build_swe_agent_plugin",
    "build_runtime",
    "MemoryOS",
    "AccessContext",
    "BeliefState",
    "CognitiveMemory",
    "EventRecord",
    "MemoryType",
    "SecurityLabel",
    "TrustTier",
]

# --------------------------------------------------------------------------
# Bio-AGI Memory OS v0.8.1 — additive cognitive kernel
#
# Merged, not replaced. The upstream v0.8.1 bundle ships only the cognitive
# and context_fabric packages, and its own __init__ drops every legacy export
# above. Adopting that file verbatim would have removed L1/L2 memory, the
# hippocampus, the knowledge graph, the LOCOMO evals, the REST API, the MCP
# server and the SDK from the public surface. Everything below is added
# alongside those, so both generations import from one package.
# --------------------------------------------------------------------------

from bio_agent_os.cognitive import MemoryOS
from bio_agent_os.cognitive.hooks import ClaudeCodeHookAdapter
from bio_agent_os.cognitive.models import (
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
    SimulationTrace,
    TrustTier,
    VerificationStatus,
)
from bio_agent_os.context_fabric import (
    AgentCheckpoint,
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    StorageTier,
)

__all__ += [
    "AgentCheckpoint",
    "ClaudeCodeHookAdapter",
    "ContextBlock",
    "ContextBlockKind",
    "ContextPacket",
    "EpistemicStatus",
    "ExecutionOutcome",
    "Modality",
    "ProspectiveTrigger",
    "SimulationTrace",
    "StorageTier",
    "VerificationStatus",
]
