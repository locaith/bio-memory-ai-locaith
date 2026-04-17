import json
from pathlib import Path


def main():
    report_dir = Path("benchmark_reports")
    report_dir.mkdir(exist_ok=True)

    json_report = {
        "suite": "openclaw-phase-5-benchmarks",
        "description": "Benchmark coverage for OpenClaw-style short and long sessions with Bio-Agent OS phase 5 memory features.",
        "scenarios": [
            {
                "name": "openclaw-mini-benchmark",
                "goal": "verify that repeated risky observations become stable rules",
                "checks": [
                    "memory labeling",
                    "micro-sleep consolidation",
                    "rule promotion",
                    "belief graph linkage",
                ],
            },
            {
                "name": "openclaw-long-session-benchmark",
                "goal": "verify compaction, adaptive effort selection, and long-session reinforcement under repeated observations",
                "checks": [
                    "memory compaction metadata",
                    "adaptive effort path",
                    "multi-sleep consolidation",
                    "rule reinforcement over long sessions",
                    "audit and replay compatibility",
                ],
            },
            {
                "name": "openclaw-mode-benchmark",
                "goal": "verify state-dependent retrieval across debug, implement, refactor, and deploy modes",
                "checks": [
                    "exception memory preference in debug",
                    "procedural guidance preference in implement",
                    "semantic guidance preference in refactor",
                    "exception memory preference in deploy",
                ],
            },
            {
                "name": "openclaw-task-chain-benchmark",
                "goal": "verify hybrid retrieval and safety guards across a long multi-stage task chain",
                "checks": [
                    "multi-step memory accumulation",
                    "belief graph retrieval",
                    "exception memory retrieval in release risk states",
                    "safety guard readiness for prompt injection",
                ],
            },
            {
                "name": "openclaw-reconsolidation-chain-benchmark",
                "goal": "verify contradiction handling and reconsolidation across multiple task waves",
                "checks": [
                    "initial stable rule formation",
                    "contradictory evidence ingestion",
                    "rule deprecation and replacement",
                    "graph retrieval reflects the newer belief",
                ],
            },
        ],
        "status": "generated",
    }

    markdown_report = """# OpenClaw Phase 5 Benchmark Report

- Scenario group: OpenClaw short and long sessions
- Goal: verify that Bio-Agent OS converts repeated observations into stable rules without context bloat
- Coverage:
  - memory labeling
  - compaction for long observations
  - adaptive effort selection
  - micro-sleep consolidation
  - rule promotion and reinforcement
  - belief graph linkage
  - audit and replay compatibility
  - state-dependent retrieval across agent modes
  - multi-stage task-chain memory behavior
  - contradiction and reconsolidation across task waves

## Included scenarios

1. openclaw-mini-benchmark
2. openclaw-long-session-benchmark
3. openclaw-mode-benchmark
4. openclaw-task-chain-benchmark
5. openclaw-reconsolidation-chain-benchmark
"""

    (report_dir / "openclaw-phase5-benchmark.json").write_text(
        json.dumps(json_report, indent=2),
        encoding="utf-8",
    )
    (report_dir / "openclaw-phase5-benchmark.md").write_text(
        markdown_report,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
