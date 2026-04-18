"""
FastAPI entry point for Bio-Agent OS.
"""

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

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
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


AGENT_NAME = os.getenv("AGENT_NAME", "Bio-AI")
STORAGE_DIR = os.getenv("STORAGE_DIR", "data")

engine: Optional[LLMEngine] = None
persona: Optional[Persona] = None
router_ai: Optional[IntentRouter] = None
l1: Optional[L1WorkingMemory] = None
l2: Optional[L2SemanticMemory] = None
kg: Optional[KnowledgeGraph] = None
episodes: Optional[EpisodeStore] = None
hippo: Optional[Hippocampus] = None
gc: Optional[GarbageCollector] = None
graph_builder: Optional[GraphBuilder] = None
health_monitor: Optional[MemoryHealthMonitor] = None
dream_journal: Optional[DreamJournal] = None
audit_log: Optional[AuditLog] = None
approval_queue: Optional[ApprovalQueue] = None
retrieval_service: Optional[RetrievalService] = None


def init_components():
    global engine, persona, router_ai, l1, l2, kg, episodes, hippo, gc, graph_builder, health_monitor, dream_journal, audit_log, approval_queue, retrieval_service

    from dotenv import load_dotenv

    load_dotenv()

    engine = LLMEngine.from_env()
    persona = Persona(name=AGENT_NAME, storage_dir=STORAGE_DIR)
    router_ai = IntentRouter(engine=engine)
    l1 = L1WorkingMemory(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    l2 = L2SemanticMemory(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    kg = KnowledgeGraph(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    episodes = EpisodeStore(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    dream_journal = DreamJournal(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    audit_log = AuditLog(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    approval_queue = ApprovalQueue(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    retrieval_service = RetrievalService(l2=l2, graph=kg, episodes=episodes, persona=persona)
    hippo = Hippocampus(
        engine=engine,
        l1=l1,
        persona=persona,
        l2=l2,
        episodes=episodes,
        graph=kg,
        dream_journal=dream_journal,
        audit_log=audit_log,
        approval_queue=approval_queue,
    )
    gc = GarbageCollector(l1=l1, l2=l2)
    graph_builder = GraphBuilder(engine=engine, graph=kg)
    health_monitor = MemoryHealthMonitor(l1=l1, l2=l2, persona=persona, episodes=episodes, graph=kg)

    print(
        "[Bio-Agent OS] Initialized "
        f"{AGENT_NAME} | backend={engine.backend} | model={engine.model_id}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_components()
    yield


app = FastAPI(
    title="Bio-Agent OS",
    description="Portable bio-inspired memory infrastructure for AI agents",
    version="0.6.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "index.html")
    if not os.path.exists(html_path):
        html_path = "index.html"
    try:
        with open(html_path, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Bio-Agent OS</h1><p>Dashboard not found.</p>")


@app.post("/api/chat")
async def chat(request: Request):
    data = await request.json()
    message = data.get("message", "").strip()
    task_id = data.get("task_id")
    workspace_id = data.get("workspace_id")
    project_version = data.get("project_version")
    source_refs = data.get("source_refs")
    mode = data.get("mode", "implement")
    stress_state = data.get("stress_state", "normal")
    risk_level = data.get("risk_level", "medium")
    if not message:
        return {"error": "Empty message"}

    intent = router_ai.quick_classify(message)
    l1_context = l1.build_context_string(n=5)
    identity_prompt = persona.get_identity_prompt()
    retrieval_state = retrieval_service.build_retrieval_state(data)

    l2_context = ""
    safety_guard = ""
    graph_context = ""
    if intent.value in ("knowledge", "recall", "task"):
        retrieval_bundle = retrieval_service.hybrid_retrieve(message, retrieval_state, top_k=5)
        l2_results = retrieval_bundle["l2_results"]
        graph_results = retrieval_bundle["graph_results"]
        safety_guard = retrieval_bundle["safety_guard"]
        if l2_results:
            l2_context = "\nRelevant long-term memory:\n" + "\n".join(
                f"- [{item['memory_type']}/{item['scope']}] {item['content']} (score={item['score']:.3f})"
                for item in l2_results
            )
        if graph_results:
            graph_context = "\nRelevant belief graph:\n" + "\n".join(
                f"- [{item['scope']}/{item['state']}] {item['text']} (score={item['score']:.3f})"
                for item in graph_results
            )

    full_prompt = (
        f"{identity_prompt}\n\n"
        f"{safety_guard}\n\n"
        f"Recent working memory:\n{l1_context}\n"
        f"{l2_context}\n\n"
        f"{graph_context}\n\n"
        f"User message: {message}\n\n"
        "If a safety guard conflicts with a general plan, follow the safety guard. "
        "Reply concisely and practically."
    )

    response = await engine.generate(full_prompt)
    await hippo.label_and_store(
        message,
        source="user",
        task_id=task_id,
        workspace_id=workspace_id,
        project_version=project_version,
        source_refs=source_refs,
        observation_type="chat_input",
    )
    await hippo.label_and_store(
        response,
        source=AGENT_NAME,
        task_id=task_id,
        workspace_id=workspace_id,
        project_version=project_version,
        source_refs=source_refs,
        observation_type="chat_output",
    )
    audit_log.append(
        "chat_turn",
        "Processed chat turn",
        {
            "intent": intent.value,
            "message_length": len(message),
            "task_id": task_id,
            "workspace_id": workspace_id,
            "project_version": project_version,
            "mode": mode,
            "stress_state": stress_state,
            "risk_level": risk_level,
        },
    )

    return {
        "response": response,
        "intent": intent.value,
        "l1_count": l1.count,
        "core_rules": persona.rule_count,
        "safety_guard": safety_guard,
    }


@app.post("/api/ingest")
async def ingest(request: Request):
    data = await request.json()
    text = data.get("text", "")
    chunk_size = data.get("chunk_size", 2000)
    source = data.get("source", "ingest")
    task_id = data.get("task_id")
    workspace_id = data.get("workspace_id")
    project_version = data.get("project_version")
    source_refs = data.get("source_refs")
    observation_type = data.get("observation_type", "ingest")

    if not text:
        return {"error": "Empty text"}

    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    stats = {"chunks": len(chunks), "labeled": 0, "graph_entities": 0, "graph_relations": 0}

    for chunk in chunks:
        await hippo.label_and_store(
            chunk,
            source=source,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            source_refs=source_refs,
            observation_type=observation_type,
        )
        stats["labeled"] += 1
        graph_stats = await graph_builder.process(chunk)
        stats["graph_entities"] += graph_stats["entities_added"]
        stats["graph_relations"] += graph_stats["relations_added"]
    audit_log.append(
        "bulk_ingest",
        "Processed ingest request",
        {
            **stats,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "project_version": project_version,
        },
    )

    return {"status": "ok", "stats": stats}


@app.post("/api/sleep")
async def trigger_sleep():
    gc_result = gc.run()
    encode_result = await hippo.consolidate()
    return {
        "status": "ok",
        "pruning": gc_result,
        "encoding": encode_result,
        "persona_rules": persona.rule_count,
        "hippo_logs": hippo.logs[-10:],
        "gc_logs": gc.logs[-10:],
    }


@app.post("/api/dream")
async def trigger_dream():
    gc_result = gc.run()
    dream_result = await hippo.dream()
    return {
        "status": "ok",
        "pruning": gc_result,
        "dream": dream_result,
        "persona_rules": persona.rule_count,
        "episodes": episodes.count,
    }


@app.get("/api/reflect")
async def reflect():
    deterministic = health_monitor.reflect()
    try:
        reflection_text = await engine.generate(health_monitor.reflection_prompt(), temperature=0.2)
    except Exception:
        reflection_text = deterministic["agent_reflection"]
    result = {
        **deterministic,
        "llm_reflection": reflection_text,
        "reflection_mode": "llm" if reflection_text != deterministic["agent_reflection"] else "deterministic",
    }
    audit_log.append("reflect", "Generated reflection", {"mode": result["reflection_mode"]})
    return result


@app.get("/api/state")
def get_state():
    return {
        "agent_name": AGENT_NAME,
        "backend": engine.backend,
        "model": engine.model_id,
        "l1": {"count": l1.count, "entries": l1.get_recent(10), "focus_set": l1.get_focus_set(10)},
        "l2": {"count": l2.count},
        "episodes": {"count": episodes.count, "recent": episodes.get_recent(10)},
        "knowledge_graph": {"nodes": kg.node_count, "edges": kg.edge_count},
        "belief_graph": kg.belief_summary(),
        "approvals": {"pending": approval_queue.pending_count, "recent": approval_queue.list(limit=10)},
        "persona": {
            "rule_count": persona.rule_count,
            "rules": list(persona.get_rule_records().values()),
            "layers": persona.get_layer_records(),
        },
        "hippo_logs": hippo.logs[-10:],
        "gc_logs": gc.logs[-10:],
    }


@app.get("/api/health")
def get_health():
    return health_monitor.status()


@app.get("/api/status")
def get_status():
    return {
        "agent_name": AGENT_NAME,
        "backend": engine.backend,
        "model": engine.model_id,
        "health": health_monitor.status(),
    }


@app.get("/api/graph")
def get_graph():
    return {
        "nodes": kg._nodes,
        "edges": kg._edges,
        "stats": {"node_count": kg.node_count, "edge_count": kg.edge_count},
    }


@app.get("/api/beliefs")
def get_beliefs(active_only: bool = False):
    return kg.belief_query(active_only=active_only)


@app.get("/api/beliefs/{rule_id}")
def get_belief(rule_id: str):
    return kg.belief_query(rule_id=rule_id)


@app.get("/api/beliefs/timeline")
def get_belief_timeline(active_only: bool = False):
    result = kg.belief_query(active_only=active_only)
    rules = sorted(
        result.get("rules", []),
        key=lambda item: item["properties"].get("valid_from") or 0,
    )
    edges = sorted(
        [
            edge for edge in kg._edges
            if edge["relation"] in {
                "supports",
                "conflicts_with",
                "supersedes",
                "governed_exception_for",
                "approved_by_policy",
                "requires_human_approval",
                "expires_override_at",
            }
        ],
        key=lambda item: item["properties"].get("valid_from") or item.get("created_at") or 0,
    )
    return {"rules": rules, "edges": edges}


@app.get("/api/dreams")
def get_dream_reports(limit: int = 10):
    return {"count": dream_journal.count, "reports": dream_journal.recent(limit)}


@app.get("/api/audit")
def get_audit(limit: int = 50, event_type: Optional[str] = None):
    return {"count": audit_log.count, "events": audit_log.recent(limit=limit, event_type=event_type)}


@app.get("/api/replay")
def replay_memory(since: float = 0.0, until: Optional[float] = None):
    return {"events": audit_log.replay(since=since, until=until)}


@app.post("/api/retrieve")
async def retrieve(request: Request):
    data = await request.json()
    query = data.get("query", "").strip()
    if not query:
        return {"error": "Empty query"}
    retrieval_state = retrieval_service.build_retrieval_state(data)
    if "prefer_exception" in data:
        retrieval_state["prefer_exception"] = bool(data.get("prefer_exception"))
    bundle = retrieval_service.hybrid_retrieve(query, retrieval_state, top_k=int(data.get("top_k", 5)))
    return {"query": query, "retrieval_state": retrieval_state, **bundle}


@app.post("/api/lineage")
async def lineage(request: Request):
    data = await request.json()
    query = data.get("query", "").strip()
    if not query:
        return {"error": "Empty query"}
    retrieval_state = retrieval_service.build_retrieval_state(data)
    return {"query": query, "retrieval_state": retrieval_state, **retrieval_service.build_lineage(query, retrieval_state, top_k=int(data.get("top_k", 5)))}


@app.get("/api/approvals")
def get_approvals(status: Optional[str] = None):
    return {"pending": approval_queue.pending_count, "requests": approval_queue.list(status=status, limit=200)}


@app.post("/api/approvals/{request_id}/approve")
async def approve_request(request_id: str, request: Request):
    payload = await request.json()
    reviewer = payload.get("reviewer", "human")
    resolved = approval_queue.resolve(request_id, "approved", reviewer=reviewer)
    if not resolved:
        return {"error": "Approval request not found"}

    if resolved["action_type"] == "promote_sensitive_rule":
        metadata = resolved.get("metadata", {})
        rule_id = persona.add_rule(
            resolved["rule_text"],
            scope=resolved.get("scope", "project"),
            confidence=float(resolved.get("confidence", 0.7)),
            evidence_episode_ids=metadata.get("evidence_episode_ids", []),
            source="human-approved",
        )
        return {"status": "approved", "request": resolved, "rule_id": rule_id}

    if resolved["action_type"] == "deprecate_sensitive_rule":
        metadata = resolved.get("metadata", {})
        persona.deprecate_rule(resolved["target_rule_id"], superseded_by=metadata.get("superseded_by"))
        return {"status": "approved", "request": resolved, "deprecated_rule_id": resolved["target_rule_id"]}

    return {"status": "approved", "request": resolved}


@app.post("/api/approvals/{request_id}/reject")
async def reject_request(request_id: str, request: Request):
    payload = await request.json()
    reviewer = payload.get("reviewer", "human")
    resolved = approval_queue.resolve(request_id, "rejected", reviewer=reviewer)
    if not resolved:
        return {"error": "Approval request not found"}
    return {"status": "rejected", "request": resolved}


@app.post("/api/reset")
def reset():
    l1.clear()
    return {"status": "reset", "warning": "L1 cleared. Persona, L2, and episodes preserved."}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8055"))
    uvicorn.run(app, host="0.0.0.0", port=port)
