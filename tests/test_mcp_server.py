"""
MCP server tests: embedded toolset, proxy routing, and FastMCP wiring.
"""

import pytest

from bio_agent_os.mcp_server import MemoryToolset, build_mcp_server, build_toolset

STORAGE = "test_data"


class FakeStructuredEngine:
    """Deterministic engine so embedded ingest never touches a real LLM."""

    backend = "fake"
    model_id = "fake-model"

    async def generate_structured(self, prompt, schema=None, temperature=0.0, **kwargs):
        return {
            "topic": "policy",
            "importance_score": 7,
            "is_junk_or_transient": False,
            "user_state": "focused",
        }

    async def generate(self, prompt, temperature=0.3, **kwargs):
        return "ok"


def _embedded_toolset() -> MemoryToolset:
    toolset = build_toolset(agent_name="mcp-test", storage_dir=STORAGE)
    toolset._sdk.runtime.hippo.engine = FakeStructuredEngine()
    return toolset


@pytest.mark.asyncio
async def test_embedded_store_recall_roundtrip():
    toolset = _embedded_toolset()

    stored = await toolset.store_memory(
        "Deploy policy: always run a canary release before full rollout.",
        workspace_id="ws-mcp",
    )
    assert stored["status"] == "ok"
    assert stored["workspace_id"] == "ws-mcp"

    # Long-term memories surface through recall once they are in L2.
    toolset._sdk.runtime.l2.store(
        content="Canary releases are mandatory for production deploys.",
        importance=8.0,
        workspace_id="ws-mcp",
    )
    recalled = await toolset.recall("canary release deploy policy", workspace_id="ws-mcp")
    assert recalled["status"] == "ok"
    assert any("Canary releases" in item["content"] for item in recalled["memories"])
    # Compact shape: no raw vectors or unbounded payloads.
    for item in recalled["memories"]:
        assert set(item.keys()) == {"content", "type", "scope", "score"}


@pytest.mark.asyncio
async def test_embedded_list_rules_and_status():
    toolset = _embedded_toolset()
    toolset._sdk.runtime.persona.add_rule(
        "Never bypass code review on release branches.", scope="project"
    )

    rules = await toolset.list_rules()
    assert rules["status"] == "ok"
    assert any("code review" in rule["text"] for rule in rules["rules"])

    status = await toolset.memory_status()
    assert status["status"] == "ok"
    assert status["mode"] == "embedded"
    assert status["memory"]["agent_name"] == "mcp-test"


@pytest.mark.asyncio
async def test_tool_errors_are_structured_not_raised():
    toolset = _embedded_toolset()
    assert (await toolset.store_memory("   "))["status"] == "error"
    assert (await toolset.recall(""))["status"] == "error"


class StubRestClient:
    def __init__(self):
        self.calls = []

    async def ingest(self, text, **context):
        self.calls.append(("ingest", text, context))
        return {"stats": {"chunks": 1}}

    async def retrieve(self, query, **context):
        self.calls.append(("retrieve", query, context))
        return {"l2_results": [], "exact_facts": {}, "graph_results": []}

    async def status(self):
        self.calls.append(("status", None, {}))
        return {"agent_name": "remote"}

    async def sleep(self):
        self.calls.append(("sleep", None, {}))
        return {"pruning": {}}

    async def state(self):
        self.calls.append(("state", None, {}))
        return {"persona": {"layers": {"core": [{"text": "remote rule", "scope": "core", "state": "stable"}]}}}


@pytest.mark.asyncio
async def test_proxy_mode_routes_to_rest_client_with_default_workspace():
    stub = StubRestClient()
    toolset = MemoryToolset(rest_client=stub, default_workspace="ws-proxy")
    assert toolset.mode == "proxy"

    await toolset.store_memory("remember this")
    await toolset.recall("what do we remember")
    rules = await toolset.list_rules()
    status = await toolset.memory_status()

    ingest_call = next(call for call in stub.calls if call[0] == "ingest")
    assert ingest_call[2]["workspace_id"] == "ws-proxy"
    retrieve_call = next(call for call in stub.calls if call[0] == "retrieve")
    assert retrieve_call[2]["workspace_id"] == "ws-proxy"
    assert rules["rules"][0]["text"] == "remote rule"
    assert status["mode"] == "proxy"


def test_toolset_requires_exactly_one_backend():
    with pytest.raises(ValueError):
        MemoryToolset()


@pytest.mark.asyncio
async def test_fastmcp_server_exposes_the_five_tools():
    pytest.importorskip("mcp")
    toolset = MemoryToolset(rest_client=StubRestClient())
    server = build_mcp_server(toolset)
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "store_memory",
        "recall",
        "list_rules",
        "memory_status",
        "consolidate",
    }
    descriptions = {tool.name: tool.description for tool in tools}
    assert "before answering" not in descriptions["store_memory"]
    assert "Recall" in descriptions["recall"]
