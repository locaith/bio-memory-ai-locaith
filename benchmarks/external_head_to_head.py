"""External competitor harness preflight.

This file intentionally does not fabricate competitor scores. It reports which
real systems are installed and produces exact commands/requirements for Claude
Opus 5 or CI to run AgentMemory, Mem0, Letta, Graphiti and Cognee under the same
dataset/model/token/hardware envelope.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path


def run() -> dict:
    systems = {
        "agentmemory": {"available": bool(shutil.which("agentmemory")), "npx_available": bool(shutil.which("npx")), "required": "Node >=20 and @agentmemory/agentmemory"},
        "mem0": {"available": importlib.util.find_spec("mem0") is not None, "required": "pip install mem0ai"},
        "letta": {"available": importlib.util.find_spec("letta") is not None, "required": "pip install letta"},
        "graphiti": {"available": importlib.util.find_spec("graphiti_core") is not None, "required": "pip install graphiti-core"},
        "cognee": {"available": importlib.util.find_spec("cognee") is not None, "required": "pip install cognee"},
    }
    return {
        "benchmark": "external-head-to-head-preflight-v1",
        "systems": systems,
        "fairness_contract": {
            "same_dataset": True,
            "same_reader_model": True,
            "same_embedding_model": True,
            "same_reranker": True,
            "same_token_budget": True,
            "same_hardware": True,
            "report_accuracy_latency_cost_security": True,
        },
        "status": "READY_BUT_NOT_RUN" if not all(v["available"] for v in systems.values()) else "READY_TO_RUN",
        "statement": "No competitor score is claimed until its actual package is installed and executed.",
    }


def main() -> None:
    result = run()
    path = Path("reports/external_head_to_head_preflight.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
