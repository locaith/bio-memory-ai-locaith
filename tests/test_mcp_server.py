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


# ==========================================================================
# the packaging contract
#
# CI installed mcp 2.0.0 against `mcp>=1.2.0` and the MCP server stopped
# working: 2.0 renamed FastMCP to MCPServer and removed `mcp.server.fastmcp`
# entirely. The constraint said "any version from 1.2.0 onwards" while the
# code meant "the 1.x API", and nothing caught the difference until a major
# release shipped.
#
# The failure was worse than a broken import. `build_mcp_server` caught the
# ImportError and told the operator to `pip install bio-agent-os[mcp]` — which
# they had already done. An error that instructs you to do the thing you did
# is worse than no error, because it sends you to the wrong place.
# ==========================================================================

import importlib.metadata
import re
import sys
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

#: What `bio_agent_os.mcp_server` actually imports. If this moves, the bound
#: below has to move with it.
REQUIRED_MCP_IMPORT = "mcp.server.fastmcp"


def _mcp_constraints() -> list[str]:
    """Every declared requirement on `mcp`, from the metadata if installed.

    Reads `importlib.metadata` first: that is the packaging as pip actually
    sees it, and it needs no TOML parser — `tomllib` is 3.11+, and this
    package supports 3.10. Falls back to scanning pyproject.toml when the
    distribution is not installed, which is how it runs from a source
    checkout.
    """
    specs: list[str] = []
    try:
        for requirement in importlib.metadata.requires("bio-agent-os") or []:
            head = requirement.split(";", 1)[0].strip()
            if re.match(r"^mcp\b", head):
                specs.append(head)
    except importlib.metadata.PackageNotFoundError:
        pass

    if not specs:
        for line in _PYPROJECT.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            specs.extend(
                match.group(1)
                for match in re.finditer(r'"(mcp[<>=!~ ][^"]*)"', line)
            )

    assert specs, "no mcp requirement found in the packaging metadata"
    return specs


def test_the_mcp_requirement_has_an_upper_bound():
    """An unbounded major is a promise to work with code that does not exist yet.

    mcp 2.0 removed the module this package imports. The constraint has to say
    which API it targets, or the next major breaks it again the day it ships.
    """
    for spec in _mcp_constraints():
        assert "<" in spec, (
            f"{spec!r} admits any future major of mcp. The code imports "
            f"{REQUIRED_MCP_IMPORT}, which mcp 2.0 removed."
        )


def test_every_mcp_requirement_agrees():
    """The `mcp` extra and the `all` extra must not drift apart."""
    assert len(set(_mcp_constraints())) == 1, (
        f"mcp is pinned differently in different extras: {_mcp_constraints()}"
    )


def test_an_incompatible_mcp_is_not_reported_as_a_missing_one(monkeypatch):
    """Tell the truth about which of the two problems it is.

    With mcp installed but too new, the old message sent an operator to
    reinstall a package they already had.
    """
    import bio_agent_os.mcp_server as server_module

    real_import = __import__

    def _no_fastmcp(name, *args, **kwargs):
        if name.startswith("mcp.server.fastmcp"):
            raise ModuleNotFoundError("No module named 'mcp.server.fastmcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "mcp", type(sys)("mcp"))
    monkeypatch.setattr("builtins.__import__", _no_fastmcp)

    with pytest.raises(RuntimeError) as caught:
        server_module.build_mcp_server(MemoryToolset(rest_client=StubRestClient()))

    message = str(caught.value)
    # Whatever version is actually installed, not one this test invents: the
    # first draft faked `mcp.__version__ = "2.0.0"` and asserted on it, which
    # failed the moment a real mcp was present and metadata won.
    installed = server_module._installed_mcp_version()
    assert installed and installed in message, (
        f"the message does not name the installed version ({installed}): {message}"
    )
    assert "incompatible" in message.lower(), (
        "an installed-but-wrong-version mcp is still described as missing"
    )
    assert "pip install bio-agent-os[mcp]" not in message, (
        "tells the operator to install a package that is already installed"
    )


def test_a_genuinely_missing_mcp_still_says_so(monkeypatch):
    """The other half of the same distinction."""
    import bio_agent_os.mcp_server as server_module

    monkeypatch.setattr(server_module, "_installed_mcp_version", lambda: None)
    real_import = __import__

    def _no_mcp(name, *args, **kwargs):
        if name.startswith("mcp"):
            raise ModuleNotFoundError("No module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_mcp)

    with pytest.raises(RuntimeError) as caught:
        server_module.build_mcp_server(MemoryToolset(rest_client=StubRestClient()))
    assert "pip install bio-agent-os[mcp]" in str(caught.value)
