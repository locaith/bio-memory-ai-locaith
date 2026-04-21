"""
Python SDK facade for Bio-Agent OS.
"""

from __future__ import annotations

from typing import Any, Optional

from bio_agent_os.core.runtime import BioAgentRuntime, build_runtime


class BioAgentSDK:
    def __init__(self, agent_name: str = "Bio-AI", storage_dir: str = "data"):
        self.runtime: BioAgentRuntime = build_runtime(agent_name=agent_name, storage_dir=storage_dir)

    async def chat(self, message: str, **context: Any) -> dict[str, Any]:
        if not message.strip():
            return {"error": "Empty message"}

        intent = self.runtime.router_ai.quick_classify(message)
        retrieval_state = self.runtime.retrieval_service.build_retrieval_state(context)
        retrieval_state["query"] = message
        l1_context = self.runtime.l1.build_context_string(n=5)
        identity_prompt = self.runtime.persona.get_identity_prompt()

        l2_context = ""
        graph_context = ""
        safety_guard = ""
        if intent.value in {"knowledge", "recall", "task"}:
            bundle = self.runtime.retrieval_service.hybrid_retrieve(message, retrieval_state, top_k=int(context.get("top_k", 5)))
            safety_guard = bundle["safety_guard"]
            if bundle["l2_results"]:
                l2_context = "\nRelevant long-term memory:\n" + "\n".join(
                    f"- [{item['memory_type']}/{item['scope']}] {item['content']} (score={item['score']:.3f})"
                    for item in bundle["l2_results"]
                )
            if bundle["graph_results"]:
                graph_context = "\nRelevant belief graph:\n" + "\n".join(
                    f"- [{item['scope']}/{item['state']}] {item['text']} (score={item['score']:.3f})"
                    for item in bundle["graph_results"]
                )

        prompt = (
            f"{identity_prompt}\n\n"
            f"{safety_guard}\n\n"
            f"Recent working memory:\n{l1_context}\n"
            f"{l2_context}\n\n"
            f"{graph_context}\n\n"
            f"User message: {message}\n\n"
            "If a safety guard conflicts with a general plan, follow the safety guard. "
            "Reply concisely and practically."
        )
        response = await self.runtime.engine.generate(prompt)
        await self.runtime.hippo.label_and_store(
            message,
            source="user",
            task_id=context.get("task_id"),
            workspace_id=context.get("workspace_id"),
            project_version=context.get("project_version"),
            source_refs=context.get("source_refs"),
            observation_type="chat_input",
        )
        await self.runtime.hippo.label_and_store(
            response,
            source=self.runtime.agent_name,
            task_id=context.get("task_id"),
            workspace_id=context.get("workspace_id"),
            project_version=context.get("project_version"),
            source_refs=context.get("source_refs"),
            observation_type="chat_output",
        )
        return {
            "response": response,
            "intent": intent.value,
            "safety_guard": safety_guard,
            "l1_count": self.runtime.l1.count,
            "core_rules": self.runtime.persona.rule_count,
        }

    async def ingest(self, text: str, source: str = "sdk", **context: Any) -> dict[str, Any]:
        if not text:
            return {"error": "Empty text"}
        await self.runtime.hippo.label_and_store(
            text,
            source=source,
            task_id=context.get("task_id"),
            workspace_id=context.get("workspace_id"),
            project_version=context.get("project_version"),
            source_refs=context.get("source_refs"),
            observation_type=context.get("observation_type", "sdk_ingest"),
        )
        return {"status": "ok", "l1_count": self.runtime.l1.count}

    async def sleep(self) -> dict[str, Any]:
        gc_stats = self.runtime.gc.run()
        consolidate = await self.runtime.hippo.consolidate()
        return {"gc": gc_stats, "consolidate": consolidate}

    async def dream(self) -> dict[str, Any]:
        return await self.runtime.hippo.dream()

    async def reflect(self, query: Optional[str] = None, **context: Any) -> dict[str, Any]:
        prompt = query or "Summarize the current memory health, active rules, risks, and missing evidence."
        retrieval_state = self.runtime.retrieval_service.build_retrieval_state(context)
        lineage = self.runtime.retrieval_service.build_lineage(prompt, retrieval_state, top_k=5)
        llm_prompt = (
            "You are reflecting on the internal memory state of an AI agent.\n"
            "Summarize health, stable beliefs, risky overrides, and uncertainty.\n\n"
            f"Lineage snapshot:\n{lineage}\n"
        )
        try:
            summary = await self.runtime.engine.generate(llm_prompt)
        except Exception:
            snapshot = self.runtime.health_monitor.snapshot()
            summary = (
                f"Memory health snapshot: rules={snapshot['rules_total']}, "
                f"l1={snapshot['l1_working_memory']['count']}, "
                f"beliefs={snapshot['belief_graph']['belief_rules']}."
            )
        return {"summary": summary, "lineage": lineage}

    def status(self) -> dict[str, Any]:
        snapshot = self.runtime.health_monitor.snapshot()
        return {
            "agent_name": self.runtime.agent_name,
            "storage_dir": self.runtime.storage_dir,
            "backend": self.runtime.engine.backend,
            "model": self.runtime.engine.model_id,
            "status": snapshot,
        }
