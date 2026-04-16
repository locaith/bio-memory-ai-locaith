"""
Bio-Agent OS — Open-source Bio-Inspired Memory Framework for AI Agents.

An AI memory architecture that mimics human cognitive processes:
  • Hippocampus (Labeling & Consolidation)
  • Synaptic Pruning (Forgetting irrelevant data)
  • Encoding Shift (Compressing experiences into Core Logic)
  • Knowledge Graph (Mapping entities & relationships)

Designed by Locaith Solution Tech | "Make in Vietnam"
License: MIT
"""

__version__ = "0.2.0"
__author__ = "Locaith Solution Tech"

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.graph_builder import GraphBuilder
