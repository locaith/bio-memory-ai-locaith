import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import aiohttp

from bio_agent_os import EpisodeStore, Hippocampus, KnowledgeGraph, L1WorkingMemory, L2SemanticMemory, MetricsStore, Persona
from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.retrieval_service import RetrievalService


class InstrumentedOllamaEngine(LLMEngine):
    def __init__(self, model_id: str = "gemma4:e2b"):
        super().__init__(backend="ollama", model_id=model_id, temperature=0.1)
        self.usage = []

    async def _generate_ollama(self, prompt: str, temp: float) -> str:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": self.model_id,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temp},
            }
            started = time.perf_counter()
            async with session.post(f"{self.base_url}/api/generate", json=payload) as response:
                data = await response.json()
            elapsed = time.perf_counter() - started
            self.usage.append(
                {
                    "prompt_eval_count": int(data.get("prompt_eval_count") or 0),
                    "eval_count": int(data.get("eval_count") or 0),
                    "latency_seconds": elapsed,
                    "total_duration_ns": int(data.get("total_duration") or 0),
                    "model": data.get("model", self.model_id),
                }
            )
            return data.get("response", "")


async def run_eval():
    os.environ.setdefault("BIO_AGENT_SECRET_KEY", "real_eval_secret_key_123456")
    storage_dir = Path("real_eval_data")
    if storage_dir.exists():
        shutil.rmtree(storage_dir)
    storage_dir.mkdir(exist_ok=True)

    engine = InstrumentedOllamaEngine(os.getenv("REAL_EVAL_MODEL", "gemma4:e2b"))
    l1 = L1WorkingMemory(agent_name="real-eval-agent", storage_dir=str(storage_dir))
    l2 = L2SemanticMemory(agent_name="real-eval-agent", storage_dir=str(storage_dir))
    persona = Persona(name="real-eval-agent", storage_dir=str(storage_dir))
    episodes = EpisodeStore(agent_name="real-eval-agent", storage_dir=str(storage_dir))
    graph = KnowledgeGraph(agent_name="real-eval-agent", storage_dir=str(storage_dir))
    hippo = Hippocampus(engine=engine, l1=l1, l2=l2, persona=persona, episodes=episodes, graph=graph)
    retrieval = RetrievalService(l2=l2, graph=graph, episodes=episodes, persona=persona)

    tasks = [
        {
            "task_id": "debug-1",
            "mode": "debug",
            "text": "build failed with peer dependency mismatch after vite upgrade on frontend workspace",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "debug-2",
            "mode": "debug",
            "text": "npm install failed again because plugin major version does not match vite major version",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "policy-1",
            "mode": "deploy",
            "text": "team policy says never use git push -f on the frontend branch in production",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "deploy-1",
            "mode": "deploy",
            "text": "deploy release candidate to production and avoid risky branch operations during the rollout",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "hotfix-1",
            "mode": "deploy",
            "text": "approved hotfix runbook says allow force push on hotfix branches only with explicit approval and audit logging",
            "workspace_id": "frontend",
            "project_version": "v3.0.1",
        },
        {
            "task_id": "hotfix-2",
            "mode": "deploy",
            "text": "incident response validated hotfix branch exception with approval and audit logging during production recovery",
            "workspace_id": "frontend",
            "project_version": "v3.0.1",
        },
    ]

    task_results = []
    for task in tasks:
        started = time.perf_counter()
        await hippo.label_and_store(
            task["text"],
            source="openclaw",
            task_id=task["task_id"],
            workspace_id=task["workspace_id"],
            project_version=task["project_version"],
            source_refs=[{"kind": "terminal", "ref": task["task_id"]}],
        )
        l1.increment_nights()
        l1.increment_nights()
        l1.increment_nights()
        consolidate_result = await hippo.consolidate()
        elapsed = time.perf_counter() - started
        task_results.append(
            {
                "task_id": task["task_id"],
                "mode": task["mode"],
                "elapsed_seconds": round(elapsed, 3),
                "consolidate": consolidate_result,
            }
        )

    probes = [
        {
            "name": "dependency-retention",
            "query": "vite plugin dependency mismatch procedure",
            "state": retrieval.build_retrieval_state(
                {
                    "mode": "debug",
                    "stress_state": "failure",
                    "risk_level": "high",
                    "workspace_id": "frontend",
                    "project_version": "v3.0.0",
                }
            ),
            "expect": lambda line: any(
                "dependency" in item.get("content", "").lower() or "dependency" in item.get("text", "").lower()
                for item in line["l2_results"] + line["graph_results"]
            ),
        },
        {
            "name": "policy-retention",
            "query": "frontend force push policy",
            "state": retrieval.build_retrieval_state(
                {
                    "mode": "deploy",
                    "stress_state": "failure",
                    "risk_level": "high",
                    "workspace_id": "frontend",
                    "project_version": "v3.0.1",
                }
            ),
            "expect": lambda line: any(
                "push -f" in item.get("text", "").lower() or "force push" in item.get("content", "").lower()
                for item in line["l2_results"] + line["graph_results"]
            ),
        },
        {
            "name": "hotfix-exception-retention",
            "query": "hotfix branch exception with approval",
            "state": retrieval.build_retrieval_state(
                {
                    "mode": "deploy",
                    "stress_state": "failure",
                    "risk_level": "high",
                    "workspace_id": "frontend",
                    "project_version": "v3.0.1",
                }
            ),
            "expect": lambda line: any(
                item.get("memory_type") == "exception" and "hotfix" in item.get("content", "").lower()
                for item in line["l2_results"]
            ),
        },
    ]

    probe_results = []
    for probe in probes:
        line = retrieval.build_lineage(probe["query"], probe["state"], top_k=5)
        probe_results.append(
            {
                "name": probe["name"],
                "passed": bool(probe["expect"](line)),
                "safety_guard": line["safety_guard"],
                "top_l2": line["l2_results"][:2],
                "top_graph": line["graph_results"][:2],
            }
        )

    rules = persona.get_rule_records()
    deprecated_rules = [rule for rule in rules.values() if rule["state"] == "deprecated"]
    challenged_rules = [rule for rule in rules.values() if rule["state"] == "challenged"]
    stable_rules = [rule for rule in rules.values() if rule["state"] in {"reinforced", "stable"}]
    contradiction_success = bool(deprecated_rules or challenged_rules)
    task_success_checks = [
        any(
            "dependency" in item["content"].lower()
            for item in l2.search(
                "dependency mismatch",
                top_k=3,
                retrieval_state={
                    "mode": "debug",
                    "stress_state": "failure",
                    "risk_level": "high",
                    "workspace_id": "frontend",
                    "project_version": "v3.0.0",
                },
            )
        ),
        any("push -f" in rule["text"].lower() or "force push" in rule["text"].lower() for rule in rules.values()),
        any(
            "hotfix" in item["content"].lower()
            for item in l2.search(
                "hotfix approval exception",
                top_k=3,
                retrieval_state={
                    "mode": "deploy",
                    "stress_state": "failure",
                    "risk_level": "high",
                    "workspace_id": "frontend",
                    "project_version": "v3.0.1",
                    "prefer_exception": True,
                },
            )
        ),
    ]

    metrics = {
        "model": engine.model_id,
        "task_set_size": len(tasks),
        "calls": len(engine.usage),
        "token": {
            "prompt_tokens": sum(item["prompt_eval_count"] for item in engine.usage),
            "completion_tokens": sum(item["eval_count"] for item in engine.usage),
            "total_tokens": sum(item["prompt_eval_count"] + item["eval_count"] for item in engine.usage),
        },
        "latency": {
            "total_seconds": round(sum(item["latency_seconds"] for item in engine.usage), 3),
            "avg_seconds_per_call": round(sum(item["latency_seconds"] for item in engine.usage) / max(len(engine.usage), 1), 3),
            "avg_seconds_per_task": round(sum(task["elapsed_seconds"] for task in task_results) / max(len(task_results), 1), 3),
        },
        "retention": {
            "passed": sum(1 for item in probe_results if item["passed"]),
            "total": len(probe_results),
            "rate": round(sum(1 for item in probe_results if item["passed"]) / max(len(probe_results), 1), 3),
        },
        "contradiction": {
            "deprecated_rules": len(deprecated_rules),
            "challenged_rules": len(challenged_rules),
            "stable_rules": len(stable_rules),
            "resolved": contradiction_success,
        },
        "task_success": {
            "passed": sum(1 for item in task_success_checks if item),
            "total": len(task_success_checks),
            "rate": round(sum(1 for item in task_success_checks if item) / max(len(task_success_checks), 1), 3),
        },
        "task_results": task_results,
        "probe_results": probe_results,
        "rules": list(rules.values()),
        "attention_state": l1.get_attention_state(),
    }

    return metrics


def write_reports(metrics):
    report_dir = Path("benchmark_reports")
    report_dir.mkdir(exist_ok=True)
    json_path = report_dir / "real_eval_gemma4_e2b.json"
    markdown_path = report_dir / "real_eval_gemma4_e2b.md"
    json_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(
        "\n".join(
            [
                "# Real Evaluation Report: gemma4:e2b",
                "",
                f"- Calls: {metrics['calls']}",
                f"- Total tokens: {metrics['token']['total_tokens']}",
                f"- Prompt tokens: {metrics['token']['prompt_tokens']}",
                f"- Completion tokens: {metrics['token']['completion_tokens']}",
                f"- Total latency (s): {metrics['latency']['total_seconds']}",
                f"- Avg latency per call (s): {metrics['latency']['avg_seconds_per_call']}",
                f"- Retention rate: {metrics['retention']['passed']}/{metrics['retention']['total']} = {metrics['retention']['rate']}",
                f"- Contradiction resolved: {metrics['contradiction']['resolved']}",
                f"- Task success rate: {metrics['task_success']['passed']}/{metrics['task_success']['total']} = {metrics['task_success']['rate']}",
                "",
                "## Attention homeostasis",
                "",
                f"- Stress level: {metrics['attention_state']['stress_level']:.3f}",
                f"- Global gain: {metrics['attention_state']['global_gain']:.3f}",
                f"- Failure streak: {metrics['attention_state']['failure_streak']:.0f}",
            ]
        ),
        encoding="utf-8",
    )


def main():
    metrics = asyncio.run(run_eval())
    metrics_store = MetricsStore(agent_name="real-eval-agent", storage_dir="real_eval_data")
    metrics_store.append("openclaw-real-eval", metrics["model"], metrics)
    write_reports(metrics)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
