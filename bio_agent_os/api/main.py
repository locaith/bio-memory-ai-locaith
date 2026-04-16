"""
api/main.py — FastAPI Application (Entry Point).

Expose endpoints cho Frontend/Client:
  POST /api/chat       — Gửi tin nhắn cho AI (Router → L1 + L2 → LLM → Response)
  POST /api/ingest     — Nạp dữ liệu thô vào Pipeline (Chunking → Label → Store)
  POST /api/sleep      — Kích hoạt vòng lặp đêm (Prune + Encode)
  GET  /api/state      — Xem trạng thái bộ nhớ (L1, L2, Graph, Persona)
  GET  /api/graph      — Xem Knowledge Graph
  GET  /                — Dashboard UI
"""

import os
import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.persona import Persona
from bio_agent_os.core.router import IntentRouter
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory
from bio_agent_os.memory.knowledge_graph import KnowledgeGraph
from bio_agent_os.background_jobs.hippocampus import Hippocampus
from bio_agent_os.background_jobs.garbage_collector import GarbageCollector
from bio_agent_os.background_jobs.graph_builder import GraphBuilder


# ─── Global Components ────────────────────────────────────

AGENT_NAME = os.getenv("AGENT_NAME", "Bio-AI")
STORAGE_DIR = os.getenv("STORAGE_DIR", "data")
LLM_BACKEND = os.getenv("LLM_BACKEND", "gemini")
MODEL_ID = os.getenv("MODEL_ID", "gemini-3-flash-preview")

engine: Optional[LLMEngine] = None
persona: Optional[Persona] = None
router_ai: Optional[IntentRouter] = None
l1: Optional[L1WorkingMemory] = None
l2: Optional[L2SemanticMemory] = None
kg: Optional[KnowledgeGraph] = None
hippo: Optional[Hippocampus] = None
gc: Optional[GarbageCollector] = None
graph_builder: Optional[GraphBuilder] = None


def init_components():
    """Initialize all Bio-Agent OS components."""
    global engine, persona, router_ai, l1, l2, kg, hippo, gc, graph_builder

    from dotenv import load_dotenv
    load_dotenv()

    engine = LLMEngine(backend=LLM_BACKEND, model_id=MODEL_ID)
    persona = Persona(name=AGENT_NAME, storage_dir=STORAGE_DIR)
    router_ai = IntentRouter(engine=engine)
    l1 = L1WorkingMemory(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    l2 = L2SemanticMemory(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    kg = KnowledgeGraph(agent_name=AGENT_NAME, storage_dir=STORAGE_DIR)
    hippo = Hippocampus(engine=engine, l1=l1, persona=persona, l2=l2)
    gc = GarbageCollector(l1=l1, l2=l2)
    graph_builder = GraphBuilder(engine=engine, graph=kg)

    print(f"[Bio-Agent OS] Initialized: {AGENT_NAME} | Backend: {LLM_BACKEND} | Model: {MODEL_ID}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_components()
    yield


# ─── FastAPI App ──────────────────────────────────────────

app = FastAPI(
    title="Bio-Agent OS",
    description="Open-source Bio-Inspired Memory Framework for AI Agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the dashboard UI."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "..", "index.html")
    if not os.path.exists(html_path):
        html_path = "index.html"
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Bio-Agent OS</h1><p>Dashboard not found. Place index.html in project root.</p>")


@app.post("/api/chat")
async def chat(request: Request):
    """
    Main chat endpoint.
    
    Flow: User message → Router → Build context (L1 + L2 + Graph) → LLM → Response
    """
    data = await request.json()
    message = data.get("message", "").strip()
    if not message:
        return {"error": "Empty message"}

    # 1. Route intent
    intent = router_ai.quick_classify(message)

    # 2. Build context from memory layers
    l1_context = l1.build_context_string(n=5)
    identity_prompt = persona.get_identity_prompt()

    l2_context = ""
    graph_context = ""

    if intent.value in ("knowledge", "recall"):
        l2_results = l2.search(message, top_k=3)
        if l2_results:
            l2_context = "\nKiến thức dài hạn liên quan:\n" + "\n".join(
                f"  - {r['content']} (score={r['score']})" for r in l2_results
            )

    # 3. Generate response
    full_prompt = f"""{identity_prompt}

Sự kiện gần đây (L1 Working Memory):
{l1_context}
{l2_context}
{graph_context}

Tin nhắn từ User: {message}

Hãy phản hồi ngắn gọn, hữu ích, dựa trên kiến thức cốt lõi và ngữ cảnh ở trên:"""

    response = await engine.generate(full_prompt)

    # 4. Store interaction in L1
    await hippo.label_and_store(message, source="user")
    await hippo.label_and_store(response, source=AGENT_NAME)

    return {
        "response": response,
        "intent": intent.value,
        "l1_count": l1.count,
        "core_rules": persona.rule_count,
    }


@app.post("/api/ingest")
async def ingest(request: Request):
    """
    Ingest raw data into the Bio-Memory pipeline.
    
    For bulk data: chunks the text and processes each chunk
    through Hippocampus labeling + GraphBuilder extraction.
    """
    data = await request.json()
    text = data.get("text", "")
    chunk_size = data.get("chunk_size", 2000)
    source = data.get("source", "ingest")

    if not text:
        return {"error": "Empty text"}

    # Chunk the text
    chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

    stats = {"chunks": len(chunks), "labeled": 0, "graph_entities": 0, "graph_relations": 0}

    for chunk in chunks:
        # Label and store
        await hippo.label_and_store(chunk, source=source)
        stats["labeled"] += 1

        # Extract graph triples
        g_stats = await graph_builder.process(chunk)
        stats["graph_entities"] += g_stats["entities_added"]
        stats["graph_relations"] += g_stats["relations_added"]

    return {"status": "ok", "stats": stats}


@app.post("/api/sleep")
async def trigger_sleep():
    """
    Trigger a full sleep cycle:
      1. Garbage Collector prunes L1 + L2
      2. Hippocampus consolidates survivors into Core Logic
      3. GraphBuilder processes remaining raw entries
    """
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


@app.get("/api/state")
def get_state():
    """Get the current state of all memory layers."""
    return {
        "agent_name": AGENT_NAME,
        "l1": {
            "count": l1.count,
            "entries": l1.get_recent(10),
        },
        "l2": {
            "count": l2.count,
        },
        "knowledge_graph": {
            "nodes": kg.node_count,
            "edges": kg.edge_count,
        },
        "persona": {
            "rule_count": persona.rule_count,
            "rules": list(persona.get_rules().values()),
        },
        "hippo_logs": hippo.logs[-10:] if hippo else [],
        "gc_logs": gc.logs[-10:] if gc else [],
    }


@app.get("/api/graph")
def get_graph():
    """Get the full Knowledge Graph data."""
    return {
        "nodes": kg._nodes,
        "edges": kg._edges,
        "stats": {
            "node_count": kg.node_count,
            "edge_count": kg.edge_count,
        },
    }


@app.post("/api/reset")
def reset():
    """Hard reset all memory (use with caution)."""
    l1.clear()
    return {"status": "reset", "warning": "L1 cleared. Persona and L2 preserved."}


# ─── Run ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8055"))
    uvicorn.run(app, host="0.0.0.0", port=port)
