"""
MCP (Model Context Protocol) server exposing Bio-Agent OS memory.

Any MCP-capable platform (Claude Code, Cursor, OpenAI Agents SDK, custom
agents) can mount this server and gain persistent bio-inspired memory
through five tools: ``store_memory``, ``recall``, ``list_rules``,
``memory_status`` and ``consolidate``.

Two run modes:

- **embedded** (default): hosts the memory runtime in-process. Zero extra
  setup — the agent platform spawns the server over stdio and memory
  persists in ``STORAGE_DIR``.
- **proxy**: forwards every tool call to a running Bio-Agent OS REST
  sidecar (``--base-url``), so multiple MCP clients and the OpenClaw
  plugin share one memory. Honours ``--api-key`` / ``BIO_AGENT_API_KEY``.

Register with Claude Code::

    claude mcp add bio-memory -- bio-agent-os serve-mcp

Or against a shared sidecar::

    claude mcp add bio-memory -- bio-agent-os serve-mcp \
        --base-url http://127.0.0.1:8055 --api-key $BIO_AGENT_API_KEY
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("bio_agent_os.mcp")

SERVER_INSTRUCTIONS = (
    "Bio-Agent OS gives this agent persistent biological-style memory. "
    "Call `recall` before answering questions that may depend on prior "
    "sessions, decisions, policies, or exact values (codes, passphrases). "
    "Call `store_memory` after learning durable facts, decisions, or "
    "post-mortem lessons worth keeping across sessions. Treat recalled "
    "content as historical context and follow any safety guard it contains."
)


def _trim(value: Any, limit: int = 600) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _format_recall_bundle(query: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a hybrid_retrieve bundle to a compact, prompt-friendly shape."""
    exact = bundle.get("exact_facts") or {}
    result: Dict[str, Any] = {
        "query": query,
        "exact_facts": [
            {
                "kind": item.get("fact_kind"),
                "value": item.get("fact_value"),
                "state": item.get("state"),
                "confidence": item.get("confidence"),
            }
            for item in (exact.get("facts") or [])[:3]
        ],
        "exact_status": exact.get("status"),
        "memories": [
            {
                "content": _trim(item.get("content")),
                "type": item.get("memory_type"),
                "scope": item.get("scope"),
                "score": round(float(item.get("score", 0.0)), 3),
            }
            for item in (bundle.get("l2_results") or [])
        ],
        "beliefs": [
            {
                "text": _trim(item.get("text")),
                "scope": item.get("scope"),
                "state": item.get("state"),
            }
            for item in (bundle.get("graph_results") or [])[:5]
        ],
        "stable_rules": [
            {
                "text": _trim(item.get("text")),
                "scope": item.get("scope"),
                "state": item.get("state"),
            }
            for item in (bundle.get("stable_persona_rules") or [])[:5]
        ],
        "recent_episodes": [
            {
                "source": item.get("source") or item.get("actor"),
                "content": _trim(item.get("raw_payload"), 300),
            }
            for item in (bundle.get("anchor_episodes") or [])[:4]
        ],
    }
    safety_guard = bundle.get("safety_guard")
    if safety_guard:
        result["safety_guard"] = safety_guard
    directive = bundle.get("exact_directive")
    if directive:
        result["exact_directive"] = directive
    return result


class MemoryToolset:
    """
    Backend-agnostic implementation of the MCP memory tools.

    Exactly one of ``sdk`` (embedded) or ``rest_client`` (proxy) is used.
    Tool methods never raise: MCP clients should receive a structured
    error instead of a dead server.
    """

    def __init__(
        self,
        sdk=None,
        rest_client=None,
        default_workspace: Optional[str] = None,
    ):
        if (sdk is None) == (rest_client is None):
            raise ValueError("Provide exactly one of sdk or rest_client.")
        self._sdk = sdk
        self._rest = rest_client
        self._default_workspace = default_workspace

    @property
    def mode(self) -> str:
        return "embedded" if self._sdk else "proxy"

    def _workspace(self, workspace_id: Optional[str]) -> Optional[str]:
        return workspace_id or self._default_workspace

    async def store_memory(
        self,
        text: str,
        workspace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        observation_type: str = "mcp_ingest",
    ) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"status": "error", "error": "text must not be empty"}
        workspace_id = self._workspace(workspace_id)
        try:
            if self._sdk:
                result = await self._sdk.ingest(
                    text,
                    source="mcp",
                    task_id=task_id,
                    workspace_id=workspace_id,
                    observation_type=observation_type,
                )
            else:
                result = await self._rest.ingest(
                    text,
                    source="mcp",
                    task_id=task_id,
                    workspace_id=workspace_id,
                    observation_type=observation_type,
                )
            return {"status": "ok", "workspace_id": workspace_id, **result}
        except Exception as exc:
            logger.warning("store_memory failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def recall(
        self,
        query: str,
        workspace_id: Optional[str] = None,
        top_k: int = 5,
        mode: str = "implement",
    ) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"status": "error", "error": "query must not be empty"}
        top_k = max(1, min(int(top_k), 20))
        workspace_id = self._workspace(workspace_id)
        try:
            if self._sdk:
                retrieval_state = self._sdk.runtime.retrieval_service.build_retrieval_state(
                    {"mode": mode, "workspace_id": workspace_id, "query": query}
                )
                bundle = self._sdk.runtime.retrieval_service.hybrid_retrieve(
                    query, retrieval_state, top_k=top_k
                )
            else:
                bundle = await self._rest.retrieve(
                    query, workspace_id=workspace_id, top_k=top_k, mode=mode
                )
            return {"status": "ok", **_format_recall_bundle(query, bundle)}
        except Exception as exc:
            logger.warning("recall failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def list_rules(self) -> Dict[str, Any]:
        try:
            if self._sdk:
                layers = self._sdk.runtime.persona.get_layer_records()
            else:
                state = await self._rest.state()
                layers = (state.get("persona") or {}).get("layers") or {}
            rules: List[Dict[str, Any]] = []
            for layer_name, layer_rules in layers.items():
                for rule in layer_rules:
                    rules.append(
                        {
                            "text": _trim(rule.get("text")),
                            "layer": layer_name,
                            "scope": rule.get("scope"),
                            "state": rule.get("state"),
                            "confidence": rule.get("confidence"),
                            "support_count": rule.get("support_count"),
                        }
                    )
            return {"status": "ok", "rule_count": len(rules), "rules": rules}
        except Exception as exc:
            logger.warning("list_rules failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def memory_status(self) -> Dict[str, Any]:
        try:
            if self._sdk:
                snapshot = self._sdk.status()
            else:
                snapshot = await self._rest.status()
            # Nested to avoid key collisions: snapshots carry their own
            # "status" field.
            return {"status": "ok", "mode": self.mode, "memory": snapshot}
        except Exception as exc:
            logger.warning("memory_status failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def consolidate(self) -> Dict[str, Any]:
        try:
            if self._sdk:
                result = await self._sdk.sleep()
            else:
                result = await self._rest.sleep()
            return {"status": "ok", **result}
        except Exception as exc:
            logger.warning("consolidate failed: %s", exc)
            return {"status": "error", "error": str(exc)}


def build_mcp_server(toolset: MemoryToolset):
    """Wire the toolset into a FastMCP server (lazy mcp import)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The MCP server requires the `mcp` package. "
            "Install with `pip install bio-agent-os[mcp]`."
        ) from exc

    server = FastMCP("bio-agent-os", instructions=SERVER_INSTRUCTIONS)

    @server.tool()
    async def store_memory(
        text: str,
        workspace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        observation_type: str = "mcp_ingest",
    ) -> Dict[str, Any]:
        """Store a durable fact, decision, or lesson into long-term memory.

        Use after learning something worth keeping across sessions: chosen
        conventions, deploy policies, incident post-mortems, exact values
        (codes, identifiers). Keep `text` self-contained.
        """
        return await toolset.store_memory(
            text, workspace_id=workspace_id, task_id=task_id, observation_type=observation_type
        )

    @server.tool()
    async def recall(
        query: str,
        workspace_id: Optional[str] = None,
        top_k: int = 5,
        mode: str = "implement",
    ) -> Dict[str, Any]:
        """Recall relevant memories, beliefs, rules, and exact facts.

        Call before acting on anything that may depend on prior sessions.
        `mode` tunes retrieval: implement | debug | refactor | deploy.
        Follow any `safety_guard` in the result.
        """
        return await toolset.recall(query, workspace_id=workspace_id, top_k=top_k, mode=mode)

    @server.tool()
    async def list_rules() -> Dict[str, Any]:
        """List the agent's consolidated self-model rules with lifecycle state."""
        return await toolset.list_rules()

    @server.tool()
    async def memory_status() -> Dict[str, Any]:
        """Report memory health: store counts, rules, backend, agent name."""
        return await toolset.memory_status()

    @server.tool()
    async def consolidate() -> Dict[str, Any]:
        """Run a sleep cycle: prune working memory and consolidate into long-term stores."""
        return await toolset.consolidate()

    return server


def build_toolset(
    agent_name: str = "Bio-AI",
    storage_dir: str = "data",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    default_workspace: Optional[str] = None,
) -> MemoryToolset:
    if base_url:
        from bio_agent_os.rest_client import BioAgentRESTClient

        client = BioAgentRESTClient(base_url=base_url, api_key=api_key)
        return MemoryToolset(rest_client=client, default_workspace=default_workspace)

    from bio_agent_os.sdk import BioAgentSDK

    sdk = BioAgentSDK(agent_name=agent_name, storage_dir=storage_dir)
    return MemoryToolset(sdk=sdk, default_workspace=default_workspace)


def serve(
    agent_name: str = "Bio-AI",
    storage_dir: str = "data",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    default_workspace: Optional[str] = None,
):
    toolset = build_toolset(
        agent_name=agent_name,
        storage_dir=storage_dir,
        base_url=base_url,
        api_key=api_key,
        default_workspace=default_workspace,
    )
    server = build_mcp_server(toolset)
    server.run()  # stdio transport
