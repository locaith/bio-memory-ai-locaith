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


async def run_eval(run_name: str = "run-1"):
    os.environ.setdefault("BIO_AGENT_SECRET_KEY", "real_eval_secret_key_123456")
    storage_dir = Path("real_eval_runs") / run_name
    if storage_dir.exists():
        shutil.rmtree(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)

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

    contradiction_tasks = [
        {
            "task_id": "policy-neg-1",
            "mode": "deploy",
            "text": "team policy says never use git push -f on the frontend branch in production",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "policy-neg-2",
            "mode": "deploy",
            "text": "frontend production policy forbids git push -f and requires normal reviewed pushes",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "policy-neg-3",
            "mode": "deploy",
            "text": "never use git push -f on the frontend branch during production deployment",
            "workspace_id": "frontend",
            "project_version": "v3.0.0",
        },
        {
            "task_id": "hotfix-pos-1",
            "mode": "deploy",
            "text": "approved hotfix runbook says allow force push on hotfix branches only with explicit approval and audit logging",
            "workspace_id": "frontend",
            "project_version": "v3.0.1",
        },
        {
            "task_id": "hotfix-pos-2",
            "mode": "deploy",
            "text": "incident response allows git push -f on approved hotfix branches with audit logging",
            "workspace_id": "frontend",
            "project_version": "v3.0.1",
        },
        {
            "task_id": "hotfix-pos-3",
            "mode": "deploy",
            "text": "allow force push on approved hotfix branches in production only when explicit approval and audit logging are present",
            "workspace_id": "frontend",
            "project_version": "v3.0.1",
        },
    ]
    approved_override_tasks = [
        {
            "task_id": "tenant-default-1",
            "mode": "implement",
            "text": "default tenant policy says never rename ERP customer codes after onboarding",
            "workspace_id": "erp-core",
            "project_version": "v5.2.0",
        },
        {
            "task_id": "tenant-exception-1",
            "mode": "implement",
            "text": "tenant A exception allows customer code rename only with finance approval and audit trail",
            "workspace_id": "erp-core",
            "project_version": "v5.2.1",
        },
        {
            "task_id": "tenant-exception-2",
            "mode": "implement",
            "text": "for tenant A, approved code rename override requires finance signoff and audit logging",
            "workspace_id": "erp-core",
            "project_version": "v5.2.1",
        },
        {
            "task_id": "migration-default-1",
            "mode": "deploy",
            "text": "default migration policy says never run destructive schema migration during business hours",
            "workspace_id": "erp-db",
            "project_version": "v8.0.0",
        },
        {
            "task_id": "migration-exception-1",
            "mode": "deploy",
            "text": "approved migration exception allows destructive schema change during recovery window only with DBA approval and rollback plan",
            "workspace_id": "erp-db",
            "project_version": "v8.0.1",
        },
        {
            "task_id": "migration-exception-2",
            "mode": "deploy",
            "text": "during recovery window, migration override is allowed only if DBA approval and rollback checklist are recorded",
            "workspace_id": "erp-db",
            "project_version": "v8.0.1",
        },
        {
            "task_id": "security-default-1",
            "mode": "deploy",
            "text": "security policy says never disable MFA in production",
            "workspace_id": "auth-core",
            "project_version": "v4.4.0",
        },
        {
            "task_id": "security-exception-1",
            "mode": "deploy",
            "text": "security override allows temporary MFA bypass only with human approval, incident ticket, and expiry window",
            "workspace_id": "auth-core",
            "project_version": "v4.4.1",
        },
        {
            "task_id": "security-exception-2",
            "mode": "deploy",
            "text": "temporary MFA bypass exception is approved only when human approval, incident ticket, and timed rollback are documented",
            "workspace_id": "auth-core",
            "project_version": "v4.4.1",
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
    reinforced_rules = [rule for rule in rules.values() if rule["state"] == "reinforced"]
    contradiction_success = bool(deprecated_rules or challenged_rules or reinforced_rules)
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
        "run_name": run_name,
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

    contradiction_storage = Path("real_eval_runs") / f"{run_name}-contradiction"
    if contradiction_storage.exists():
        shutil.rmtree(contradiction_storage)
    contradiction_storage.mkdir(parents=True, exist_ok=True)

    contradiction_engine = InstrumentedOllamaEngine(os.getenv("REAL_EVAL_MODEL", "gemma4:e2b"))
    c_l1 = L1WorkingMemory(agent_name="contradiction-agent", storage_dir=str(contradiction_storage))
    c_l2 = L2SemanticMemory(agent_name="contradiction-agent", storage_dir=str(contradiction_storage))
    c_persona = Persona(name="contradiction-agent", storage_dir=str(contradiction_storage))
    c_episodes = EpisodeStore(agent_name="contradiction-agent", storage_dir=str(contradiction_storage))
    c_graph = KnowledgeGraph(agent_name="contradiction-agent", storage_dir=str(contradiction_storage))
    c_hippo = Hippocampus(engine=contradiction_engine, l1=c_l1, l2=c_l2, persona=c_persona, episodes=c_episodes, graph=c_graph)

    contradiction_task_results = []
    for task in contradiction_tasks:
        started = time.perf_counter()
        await c_hippo.label_and_store(
            task["text"],
            source="openclaw",
            task_id=task["task_id"],
            workspace_id=task["workspace_id"],
            project_version=task["project_version"],
            source_refs=[{"kind": "terminal", "ref": task["task_id"]}],
        )
        c_l1.increment_nights()
        c_l1.increment_nights()
        c_l1.increment_nights()
        consolidate_result = await c_hippo.consolidate()
        contradiction_task_results.append(
            {
                "task_id": task["task_id"],
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "consolidate": consolidate_result,
            }
        )

    contradiction_rules = c_persona.get_rule_records()
    contradiction_metrics = {
        "calls": len(contradiction_engine.usage),
        "token_total": sum(item["prompt_eval_count"] + item["eval_count"] for item in contradiction_engine.usage),
        "stable_rules": len([rule for rule in contradiction_rules.values() if rule["state"] == "stable"]),
        "reinforced_rules": len([rule for rule in contradiction_rules.values() if rule["state"] == "reinforced"]),
        "challenged_rules": len([rule for rule in contradiction_rules.values() if rule["state"] == "challenged"]),
        "deprecated_rules": len([rule for rule in contradiction_rules.values() if rule["state"] == "deprecated"]),
            "resolved": any(rule["state"] in {"reinforced", "challenged", "deprecated"} for rule in contradiction_rules.values()),
        "task_results": contradiction_task_results,
        "rules": list(contradiction_rules.values()),
    }
    metrics["contradiction_suite"] = contradiction_metrics

    override_storage = Path("real_eval_runs") / f"{run_name}-approved-overrides"
    if override_storage.exists():
        shutil.rmtree(override_storage)
    override_storage.mkdir(parents=True, exist_ok=True)

    o_engine = InstrumentedOllamaEngine(os.getenv("REAL_EVAL_MODEL", "gemma4:e2b"))
    o_l1 = L1WorkingMemory(agent_name="override-agent", storage_dir=str(override_storage))
    o_l2 = L2SemanticMemory(agent_name="override-agent", storage_dir=str(override_storage))
    o_persona = Persona(name="override-agent", storage_dir=str(override_storage))
    o_episodes = EpisodeStore(agent_name="override-agent", storage_dir=str(override_storage))
    o_graph = KnowledgeGraph(agent_name="override-agent", storage_dir=str(override_storage))
    o_hippo = Hippocampus(engine=o_engine, l1=o_l1, l2=o_l2, persona=o_persona, episodes=o_episodes, graph=o_graph)

    override_task_results = []
    for task in approved_override_tasks:
        started = time.perf_counter()
        await o_hippo.label_and_store(
            task["text"],
            source="openclaw",
            task_id=task["task_id"],
            workspace_id=task["workspace_id"],
            project_version=task["project_version"],
            source_refs=[{"kind": "terminal", "ref": task["task_id"]}],
        )
        o_l1.increment_nights()
        o_l1.increment_nights()
        o_l1.increment_nights()
        consolidate_result = await o_hippo.consolidate()
        override_task_results.append(
            {
                "task_id": task["task_id"],
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "consolidate": consolidate_result,
            }
        )

    override_rules = o_persona.get_rule_records()
    governed_edges = [edge for edge in o_graph.query_relations(next(iter(override_rules.keys()), "")) if edge["relation"] == "governed_exception_for"] if override_rules else []
    override_metrics = {
        "calls": len(o_engine.usage),
        "token_total": sum(item["prompt_eval_count"] + item["eval_count"] for item in o_engine.usage),
        "stable_rules": len([rule for rule in override_rules.values() if rule["state"] == "stable"]),
        "reinforced_rules": len([rule for rule in override_rules.values() if rule["state"] == "reinforced"]),
        "governed_exception_edges": len([edge for edge in o_graph._edges if edge["relation"] == "governed_exception_for"]),
        "resolved": any(rule["state"] == "reinforced" for rule in override_rules.values()),
        "task_results": override_task_results,
        "rules": list(override_rules.values()),
        "sample_relations": governed_edges[:5],
    }
    metrics["approved_override_suite"] = override_metrics

    return metrics


def _aggregate_runs(all_metrics):
    def avg(path):
        values = all_metrics
        for key in path:
            values = [item[key] if isinstance(item, dict) else None for item in values]
        filtered = [value for value in values if isinstance(value, (int, float))]
        return round(sum(filtered) / max(len(filtered), 1), 3)

    return {
        "runs": len(all_metrics),
        "model": all_metrics[0]["model"] if all_metrics else "",
        "retention_rates": [item["retention"]["rate"] for item in all_metrics],
        "task_success_rates": [item["task_success"]["rate"] for item in all_metrics],
        "contradiction_resolved_runs": sum(1 for item in all_metrics if item["contradiction"]["resolved"]),
        "contradiction_suite_resolved_runs": sum(1 for item in all_metrics if item["contradiction_suite"]["resolved"]),
        "approved_override_resolved_runs": sum(1 for item in all_metrics if item["approved_override_suite"]["resolved"]),
        "stable_rule_counts": [item["contradiction_suite"]["stable_rules"] for item in all_metrics],
        "reinforced_rule_counts": [item["contradiction_suite"]["reinforced_rules"] for item in all_metrics],
        "avg_total_tokens": avg(["token", "total_tokens"]),
        "avg_total_latency_seconds": avg(["latency", "total_seconds"]),
        "avg_retention_rate": avg(["retention", "rate"]),
        "avg_task_success_rate": avg(["task_success", "rate"]),
    }


def write_reports(all_metrics):
    report_dir = Path("benchmark_reports")
    report_dir.mkdir(exist_ok=True)
    aggregate = _aggregate_runs(all_metrics)
    latest_metrics = all_metrics[-1]
    json_path = report_dir / "real_eval_gemma4_e2b.json"
    markdown_path = report_dir / "real_eval_gemma4_e2b.md"
    comparison_json_path = report_dir / "real_eval_gemma4_e2b_comparison.json"
    comparison_md_path = report_dir / "real_eval_gemma4_e2b_comparison.md"
    json_path.write_text(json.dumps(latest_metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    comparison_json_path.write_text(
        json.dumps({"aggregate": aggregate, "runs": all_metrics}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        "\n".join(
            [
                "# Real Evaluation Report: gemma4:e2b",
                "",
                f"- Run: {latest_metrics['run_name']}",
                f"- Calls: {latest_metrics['calls']}",
                f"- Total tokens: {latest_metrics['token']['total_tokens']}",
                f"- Prompt tokens: {latest_metrics['token']['prompt_tokens']}",
                f"- Completion tokens: {latest_metrics['token']['completion_tokens']}",
                f"- Total latency (s): {latest_metrics['latency']['total_seconds']}",
                f"- Avg latency per call (s): {latest_metrics['latency']['avg_seconds_per_call']}",
                f"- Retention rate: {latest_metrics['retention']['passed']}/{latest_metrics['retention']['total']} = {latest_metrics['retention']['rate']}",
                f"- Contradiction resolved: {latest_metrics['contradiction']['resolved']}",
                f"- Task success rate: {latest_metrics['task_success']['passed']}/{latest_metrics['task_success']['total']} = {latest_metrics['task_success']['rate']}",
                "",
                "## Attention homeostasis",
                "",
                f"- Stress level: {latest_metrics['attention_state']['stress_level']:.3f}",
                f"- Global gain: {latest_metrics['attention_state']['global_gain']:.3f}",
                f"- Failure streak: {latest_metrics['attention_state']['failure_streak']:.0f}",
            ]
        ),
        encoding="utf-8",
    )
    comparison_md_path.write_text(
        "\n".join(
            [
                "# Real Evaluation Comparison: gemma4:e2b",
                "",
                f"- Runs: {aggregate['runs']}",
                f"- Avg total tokens: {aggregate['avg_total_tokens']}",
                f"- Avg total latency (s): {aggregate['avg_total_latency_seconds']}",
                f"- Avg retention rate: {aggregate['avg_retention_rate']}",
                f"- Avg task success rate: {aggregate['avg_task_success_rate']}",
                f"- Runs with contradiction resolved: {aggregate['contradiction_resolved_runs']}/{aggregate['runs']}",
                f"- Contradiction-suite resolved runs: {aggregate['contradiction_suite_resolved_runs']}/{aggregate['runs']}",
                f"- Approved-override-suite resolved runs: {aggregate['approved_override_resolved_runs']}/{aggregate['runs']}",
                "",
                "## Per-run trend",
                "",
            ]
            + [
                (
                    f"- {item['run_name']}: tokens={item['token']['total_tokens']}, "
                    f"latency={item['latency']['total_seconds']}s, "
                    f"retention={item['retention']['rate']}, "
                    f"task_success={item['task_success']['rate']}, "
                    f"contradiction_resolved={item['contradiction']['resolved']}, "
                    f"contradiction_suite_stable={item['contradiction_suite']['stable_rules']}, "
                    f"contradiction_suite_reinforced={item['contradiction_suite']['reinforced_rules']}, "
                    f"contradiction_suite_resolved={item['contradiction_suite']['resolved']}, "
                    f"approved_override_reinforced={item['approved_override_suite']['reinforced_rules']}, "
                    f"approved_override_edges={item['approved_override_suite']['governed_exception_edges']}, "
                    f"approved_override_resolved={item['approved_override_suite']['resolved']}"
                )
                for item in all_metrics
            ]
        ),
        encoding="utf-8",
    )


def main():
    run_count = int(os.getenv("REAL_EVAL_RUNS", "3"))
    all_metrics = []
    metrics_store = MetricsStore(agent_name="real-eval-agent", storage_dir="real_eval_metrics")
    for index in range(1, run_count + 1):
        metrics = asyncio.run(run_eval(run_name=f"run-{index}"))
        metrics_store.append("openclaw-real-eval", metrics["model"], metrics)
        all_metrics.append(metrics)
    write_reports(all_metrics)
    print(json.dumps({"aggregate": _aggregate_runs(all_metrics), "runs": all_metrics}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
