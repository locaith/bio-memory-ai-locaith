"""
FastAPI entry point for Bio-Agent OS.
"""

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from bio_agent_os.api.schemas import (
    ApprovalDecisionRequest,
    ChatRequest,
    IngestRequest,
    RetrieveRequest,
    RevalidationResolveRequest,
)
from bio_agent_os.api.security import (
    APISecurityConfig,
    AuthMiddleware,
    RateLimitMiddleware,
)

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
from bio_agent_os.core.runtime import build_runtime
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.memory.episodes import EpisodeStore
from bio_agent_os.memory.exact_memory import ExactMemoryStore
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory
from bio_agent_os.memory.coverage_index import CoverageIndex


AGENT_NAME = os.getenv("AGENT_NAME", "Bio-AI")
STORAGE_DIR = os.getenv("STORAGE_DIR", "data")

engine: Optional[LLMEngine] = None
persona: Optional[Persona] = None
router_ai: Optional[IntentRouter] = None
l1: Optional[L1WorkingMemory] = None
l2: Optional[L2SemanticMemory] = None
kg: Optional[KnowledgeGraph] = None
episodes: Optional[EpisodeStore] = None
exact_memory: Optional[ExactMemoryStore] = None
hippo: Optional[Hippocampus] = None
gc: Optional[GarbageCollector] = None
graph_builder: Optional[GraphBuilder] = None
health_monitor: Optional[MemoryHealthMonitor] = None
dream_journal: Optional[DreamJournal] = None
audit_log: Optional[AuditLog] = None
approval_queue: Optional[ApprovalQueue] = None
retrieval_service: Optional[RetrievalService] = None
coverage_index: Optional[CoverageIndex] = None


def init_components():
    global engine, persona, router_ai, l1, l2, kg, episodes, exact_memory, hippo, gc, graph_builder, health_monitor, dream_journal, audit_log, approval_queue, retrieval_service, coverage_index

    runtime = build_runtime(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    engine = runtime.engine
    persona = runtime.persona
    router_ai = runtime.router_ai
    l1 = runtime.l1
    l2 = runtime.l2
    kg = runtime.kg
    episodes = runtime.episodes
    exact_memory = runtime.exact_memory
    hippo = runtime.hippo
    gc = runtime.gc
    graph_builder = runtime.graph_builder
    health_monitor = runtime.health_monitor
    dream_journal = runtime.dream_journal
    audit_log = runtime.audit_log
    approval_queue = runtime.approval_queue
    retrieval_service = runtime.retrieval_service
    coverage_index = runtime.coverage_index

    print(
        "[Bio-Agent OS] Initialized "
        f"{AGENT_NAME} | backend={engine.backend} | model={engine.model_id}"
    )


SECURITY_CONFIG = APISecurityConfig.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if SECURITY_CONFIG.api_key is None:
        print(
            "[Bio-Agent OS] WARNING: BIO_AGENT_API_KEY is not set — the API is "
            "unauthenticated. Safe for loopback-only development; set a key "
            "before exposing the server to a network."
        )
    init_components()
    yield


app = FastAPI(
    title="Bio-Agent OS",
    description="Portable bio-inspired memory infrastructure for AI agents",
    version="0.6.1",
    lifespan=lifespan,
)

# Middleware execution order (outermost first): CORS -> rate limit -> auth,
# so CORS preflights are answered before auth and throttled clients are
# rejected before hitting auth comparisons.
app.add_middleware(AuthMiddleware, config=SECURITY_CONFIG)
app.add_middleware(RateLimitMiddleware, config=SECURITY_CONFIG)
app.add_middleware(
    CORSMiddleware,
    allow_origins=SECURITY_CONFIG.cors_origins,
    allow_credentials=SECURITY_CONFIG.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _workspace_scope(request: Request):
    """None means unrestricted (admin key or auth disabled)."""
    return getattr(request.state, "workspace_scope", None)


def _require_admin(request: Request):
    if _workspace_scope(request) is not None:
        raise HTTPException(status_code=403, detail="This endpoint requires the admin API key.")


def _enforce_workspace(request: Request, workspace_id: Optional[str]) -> Optional[str]:
    """Resolve the effective workspace for a request, honouring tenant scope."""
    scope = _workspace_scope(request)
    if scope is None:
        return workspace_id
    if workspace_id:
        if workspace_id in scope:
            return workspace_id
        raise HTTPException(status_code=403, detail="Workspace not permitted for this API key.")
    if len(scope) == 1:
        return next(iter(scope))
    raise HTTPException(
        status_code=403,
        detail="This API key spans multiple workspaces; set workspace_id explicitly.",
    )


def _apply_scope_to_retrieval_state(request: Request, retrieval_state: dict) -> dict:
    scope = _workspace_scope(request)
    if scope is not None:
        retrieval_state["allowed_workspaces"] = sorted(scope)
    return retrieval_state


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
async def chat(payload: ChatRequest, request: Request):
    message = payload.message
    task_id = payload.task_id
    workspace_id = _enforce_workspace(request, payload.workspace_id)
    project_version = payload.project_version
    source_refs = payload.source_refs

    intent = router_ai.quick_classify(message)
    l1_context = l1.build_context_string(n=5)
    identity_prompt = persona.get_identity_prompt()
    retrieval_state = retrieval_service.build_retrieval_state(payload.model_dump())
    retrieval_state["workspace_id"] = workspace_id
    _apply_scope_to_retrieval_state(request, retrieval_state)
    retrieval_state["query"] = message
    exact_query_kind = exact_memory.infer_query_kind(message) if exact_memory else None

    l2_context = ""
    safety_guard = ""
    graph_context = ""
    if intent.value in ("knowledge", "recall", "task") or exact_query_kind:
        retrieval_bundle = retrieval_service.hybrid_retrieve(message, retrieval_state, top_k=5)
        exact_facts = retrieval_bundle.get("exact_facts", {})
        revalidation = retrieval_bundle.get("revalidation", {})
        l2_results = retrieval_bundle["l2_results"]
        graph_results = retrieval_bundle["graph_results"]
        safety_guard = retrieval_bundle["safety_guard"]
        context_sections = []
        if exact_facts.get("facts"):
            exact_context = "Exact memory:\n" + "\n".join(
                f"- [{item['fact_kind']}/{item['state']}] {item['fact_value']} "
                f"(reinforced={item['reinforcement_count']}, confidence={item['confidence']:.2f})"
                for item in exact_facts["facts"][:3]
            )
            if exact_facts.get("status") == "conflicting":
                exact_context += "\n- Exact memory is conflicting. Do not guess; cite the recent evidence or ask for confirmation."
            context_sections.append(exact_context)
        if l2_results:
            l2_context = "Relevant long-term memory:\n" + "\n".join(
                f"- [{item['memory_type']}/{item['scope']}] {item['content']} (score={item['score']:.3f})"
                for item in l2_results
            )
            context_sections.append(l2_context)
        if graph_results:
            graph_context = "\nRelevant belief graph:\n" + "\n".join(
                f"- [{item['scope']}/{item['state']}] {item['text']} (score={item['score']:.3f})"
                for item in graph_results
            )
        if context_sections:
            l2_context = "\n" + "\n\n".join(context_sections)
        direct_exact_response = retrieval_service.direct_exact_response(message, exact_facts, revalidation)
    else:
        direct_exact_response = None

    if direct_exact_response:
        response = direct_exact_response
    else:
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
            "mode": payload.mode,
            "stress_state": payload.stress_state,
            "risk_level": payload.risk_level,
        },
    )

    return {
        "response": response,
        "intent": intent.value,
        "l1_count": l1.count,
        "core_rules": persona.rule_count,
        "safety_guard": safety_guard,
        "direct_memory_response": bool(direct_exact_response),
    }


@app.post("/api/ingest")
async def ingest(payload: IngestRequest, request: Request, background: BackgroundTasks):
    text = payload.text
    chunk_size = payload.chunk_size
    source = payload.source
    task_id = payload.task_id
    workspace_id = _enforce_workspace(request, payload.workspace_id)
    project_version = payload.project_version
    source_refs = payload.source_refs
    observation_type = payload.observation_type

    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    stats = {"chunks": len(chunks), "labeled": 0, "graph_deferred": len(chunks)}

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

    # Everything that needs the network runs AFTER the response, so an ingest
    # costs the caller a SQLite insert and nothing else. Entity/relation
    # extraction is an LLM call per chunk and embeddings are a round trip each;
    # inline, they made ingest ~2.8s. Deferring them to the background keeps
    # recall fresh within seconds without ever blocking a write.
    background.add_task(episodes.backfill_vectors, 200)
    background.add_task(_enrich_graph, chunks)

    return {"status": "ok", "stats": stats}


async def _enrich_graph(chunks: list[str]) -> None:
    """Build knowledge-graph entities/relations off the request path."""
    for chunk in chunks:
        try:
            await graph_builder.process(chunk)
        except Exception as exc:  # noqa: BLE001 — enrichment must never break ingest
            print(f"[Bio-Agent OS] graph enrichment skipped: {exc}")


@app.post("/api/sleep")
async def trigger_sleep(request: Request):
    _require_admin(request)
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
async def trigger_dream(request: Request):
    _require_admin(request)
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
async def reflect(request: Request):
    _require_admin(request)
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
def get_state(request: Request):
    _require_admin(request)
    return {
        "agent_name": AGENT_NAME,
        "backend": engine.backend,
        "model": engine.model_id,
        "l1": {"count": l1.count, "entries": l1.get_recent(10), "focus_set": l1.get_focus_set(10)},
        "l2": {"count": l2.count},
        "episodes": {"count": episodes.count, "recent": episodes.get_recent(10)},
        "exact_memory": {"count": exact_memory.count, "recent": exact_memory.recent(10)},
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


@app.get("/api/confidence-dashboard")
def get_confidence_dashboard(request: Request):
    _require_admin(request)
    return health_monitor.confidence_dashboard()


@app.get("/api/episodes")
def get_episodes(
    request: Request,
    limit: int = Query(10, ge=1, le=200),
    task_id: Optional[str] = Query(None, max_length=128),
    workspace_id: Optional[str] = Query(None, max_length=128),
    project_version: Optional[str] = Query(None, max_length=128),
    query: Optional[str] = Query(None, max_length=20_000),
):
    workspace_id = _enforce_workspace(request, workspace_id)
    if query:
        items = episodes.search_text(
            query=query,
            limit=limit,
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
        )
    else:
        items = episodes.query(
            task_id=task_id,
            workspace_id=workspace_id,
            project_version=project_version,
            limit=limit,
        )
    return {"count": len(items), "episodes": items}


@app.get("/api/status")
def get_status():
    return {
        "agent_name": AGENT_NAME,
        "backend": engine.backend,
        "model": engine.model_id,
        "health": health_monitor.status(),
    }


@app.get("/api/exact-memories")
def get_exact_memories(
    request: Request,
    limit: int = Query(10, ge=1, le=200),
    query: Optional[str] = Query(None, max_length=20_000),
    workspace_id: Optional[str] = Query(None, max_length=128),
    project_version: Optional[str] = Query(None, max_length=128),
    task_id: Optional[str] = Query(None, max_length=128),
):
    workspace_id = _enforce_workspace(request, workspace_id)
    if query:
        return exact_memory.recall(
            query=query,
            workspace_id=workspace_id,
            project_version=project_version,
            task_id=task_id,
            limit=limit,
        )
    # The unfiltered listing spans every workspace, so it stays admin-only.
    _require_admin(request)
    return {"count": exact_memory.count, "records": exact_memory.recent(limit)}


@app.post("/api/exact-memories/reindex")
def reindex_exact_memories(request: Request, limit: int = Query(1200, ge=1, le=10_000)):
    _require_admin(request)
    imported = exact_memory.reindex_from_episodes(episodes, limit=limit)
    return {"status": "ok", "imported": imported, "count": exact_memory.count}


@app.get("/api/revalidation")
def get_revalidation(request: Request, limit: int = Query(12, ge=1, le=200)):
    _require_admin(request)
    summary = health_monitor.revalidation_summary()
    return {**summary, "clusters": summary.get("clusters", [])[:limit]}


@app.post("/api/revalidation/resolve")
async def resolve_revalidation(payload: RevalidationResolveRequest, request: Request):
    workspace_id = _enforce_workspace(request, payload.workspace_id)
    result = exact_memory.resolve_conflict(
        fact_kind=payload.fact_kind,
        fact_value=payload.fact_value,
        workspace_id=workspace_id,
        project_version=payload.project_version,
        reviewer=payload.reviewer,
    )
    return {"status": "ok", **result}


@app.get("/api/graph")
def get_graph(request: Request):
    _require_admin(request)
    return {
        "nodes": kg._nodes,
        "edges": kg._edges,
        "stats": {"node_count": kg.node_count, "edge_count": kg.edge_count},
    }


@app.get("/api/beliefs")
def get_beliefs(request: Request, active_only: bool = False):
    _require_admin(request)
    return kg.belief_query(active_only=active_only)


# NOTE: declared before /api/beliefs/{rule_id} so "timeline" is not
# swallowed by the path parameter.
@app.get("/api/beliefs/timeline")
def get_belief_timeline(request: Request, active_only: bool = False):
    _require_admin(request)
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


@app.get("/api/beliefs/{rule_id}")
def get_belief(rule_id: str, request: Request):
    _require_admin(request)
    return kg.belief_query(rule_id=rule_id)


@app.get("/api/dreams")
def get_dream_reports(request: Request, limit: int = Query(10, ge=1, le=200)):
    _require_admin(request)
    return {"count": dream_journal.count, "reports": dream_journal.recent(limit)}


@app.get("/api/audit")
def get_audit(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    event_type: Optional[str] = Query(None, max_length=64),
):
    _require_admin(request)
    return {"count": audit_log.count, "events": audit_log.recent(limit=limit, event_type=event_type)}


@app.get("/api/replay")
def replay_memory(
    request: Request,
    since: float = Query(0.0, ge=0.0),
    until: Optional[float] = Query(None, ge=0.0),
):
    _require_admin(request)
    return {"events": audit_log.replay(since=since, until=until)}


@app.post("/api/retrieve")
async def retrieve(payload: RetrieveRequest, request: Request):
    query = payload.query
    retrieval_state = retrieval_service.build_retrieval_state(payload.model_dump())
    retrieval_state["workspace_id"] = _enforce_workspace(request, payload.workspace_id)
    _apply_scope_to_retrieval_state(request, retrieval_state)
    if payload.prefer_exception is not None:
        retrieval_state["prefer_exception"] = payload.prefer_exception
    bundle = retrieval_service.hybrid_retrieve(query, retrieval_state, top_k=payload.top_k)
    return {"query": query, "retrieval_state": retrieval_state, **bundle}


@app.post("/api/lineage")
async def lineage(payload: RetrieveRequest, request: Request):
    query = payload.query
    retrieval_state = retrieval_service.build_retrieval_state(payload.model_dump())
    retrieval_state["workspace_id"] = _enforce_workspace(request, payload.workspace_id)
    _apply_scope_to_retrieval_state(request, retrieval_state)
    return {
        "query": query,
        "retrieval_state": retrieval_state,
        **retrieval_service.build_lineage(query, retrieval_state, top_k=payload.top_k),
    }


@app.get("/api/lineage")
def lineage_get(
    request: Request,
    query: str = Query(min_length=1, max_length=20_000),
    mode: str = Query("implement", max_length=32),
    stress_state: str = Query("normal", max_length=32),
    risk_level: str = Query("medium", max_length=32),
    task_id: Optional[str] = Query(None, max_length=128),
    workspace_id: Optional[str] = Query(None, max_length=128),
    project_version: Optional[str] = Query(None, max_length=128),
    top_k: int = Query(5, ge=1, le=50),
):
    data = {
        "query": query,
        "mode": mode,
        "stress_state": stress_state,
        "risk_level": risk_level,
        "task_id": task_id,
        "workspace_id": _enforce_workspace(request, workspace_id),
        "project_version": project_version,
    }
    retrieval_state = retrieval_service.build_retrieval_state(data)
    _apply_scope_to_retrieval_state(request, retrieval_state)
    return {
        "query": query,
        "retrieval_state": retrieval_state,
        **retrieval_service.build_lineage(query, retrieval_state, top_k=top_k),
    }


@app.get("/api/approvals")
def get_approvals(request: Request, status: Optional[str] = Query(None, max_length=32)):
    _require_admin(request)
    return {"pending": approval_queue.pending_count, "requests": approval_queue.list(status=status, limit=200)}


@app.post("/api/approvals/{request_id}/approve")
async def approve_request(
    request_id: str, request: Request, payload: Optional[ApprovalDecisionRequest] = None
):
    _require_admin(request)
    reviewer = payload.reviewer if payload else "human"
    resolved = approval_queue.resolve(request_id, "approved", reviewer=reviewer)
    if not resolved:
        raise HTTPException(status_code=404, detail="Approval request not found")

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
async def reject_request(
    request_id: str, request: Request, payload: Optional[ApprovalDecisionRequest] = None
):
    _require_admin(request)
    reviewer = payload.reviewer if payload else "human"
    resolved = approval_queue.resolve(request_id, "rejected", reviewer=reviewer)
    if not resolved:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return {"status": "rejected", "request": resolved}


@app.post("/api/reset")
def reset(request: Request):
    _require_admin(request)
    l1.clear()
    return {"status": "reset", "warning": "L1 cleared. Persona, L2, and episodes preserved."}


# ── Verified-Coverage Multi-Resolution Index (VCMI) ──────────────────────────
@app.post("/api/coverage/refresh")
def coverage_refresh(request: Request, workspace_id: Optional[str] = Query(None, max_length=128)):
    """Dựng/cập nhật cây chỉ mục nhiều tầng phủ lên kho thô bất tử (episodes)."""
    workspace_id = _enforce_workspace(request, workspace_id)
    return coverage_index.refresh(workspace_id=workspace_id)


@app.get("/api/coverage")
def coverage_report(request: Request, workspace_id: Optional[str] = Query(None, max_length=128)):
    """Bằng chứng ĐO ĐƯỢC 'không sót thông tin': % phủ + danh sách mồ côi."""
    workspace_id = _enforce_workspace(request, workspace_id)
    return coverage_index.coverage_report(workspace_id=workspace_id)


@app.post("/api/coverage/retrieve")
def coverage_retrieve(payload: RetrieveRequest, request: Request):
    """Truy hồi node tóm tắt cấp cao (đã áp suy giảm Ebbinghaus lên thứ hạng)."""
    workspace_id = _enforce_workspace(request, payload.workspace_id)
    return {
        "query": payload.query,
        "nodes": coverage_index.retrieve(payload.query, workspace_id=workspace_id, top_k=payload.top_k),
    }


@app.get("/api/zoom")
def coverage_zoom(node_id: str = Query(..., max_length=128)):
    """Zoom về nguồn: giải một node tóm tắt ra ĐÚNG episode thô nguyên văn."""
    episodes_raw = coverage_index.zoom(node_id)
    return {"node_id": node_id, "count": len(episodes_raw), "episodes": episodes_raw}


@app.delete("/api/workspace/{workspace_id}")
def purge_workspace(workspace_id: str, request: Request):
    """Xoá VĨNH VIỄN toàn bộ trí nhớ của một workspace, có kiểm chứng.

    "Kho thô bất tử" nghĩa là thuật toán quên không bao giờ xoá dữ liệu — KHÔNG
    có nghĩa là chủ dữ liệu không xoá được. Quyền được lãng quên (GDPR) cần một
    đường xoá dứt khoát, và vận hành cần nó để nạp lại sạch. Trả về số bản đã
    xoá ở từng tầng cùng số còn lại (phải bằng 0) để việc xoá là kiểm chứng
    được, không phải chỉ tin.
    """
    workspace_id = _enforce_workspace(request, workspace_id)
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")
    deleted = {
        "episodes": episodes.purge_workspace(workspace_id) if episodes else 0,
        "l1": l1.purge_workspace(workspace_id) if l1 else 0,
        "l2": l2.purge_workspace(workspace_id) if l2 else 0,
        "coverage_nodes": coverage_index.purge_workspace(workspace_id) if coverage_index else 0,
    }
    remaining = episodes.workspace_count(workspace_id) if episodes else 0
    audit_log.append(
        "workspace_purged",
        f"Purged all memory for workspace {workspace_id}",
        {**deleted, "remaining_episodes": remaining},
    )
    return {
        "status": "purged",
        "workspace_id": workspace_id,
        "deleted": deleted,
        "remaining_episodes": remaining,
        "verified": remaining == 0,
    }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8055"))
    uvicorn.run(app, host=host, port=port)
