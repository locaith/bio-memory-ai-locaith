import json
from pathlib import Path


def main():
    report_dir = Path("benchmark_reports")
    report_dir.mkdir(exist_ok=True)

    json_report = {
        "name": "openclaw-mini-benchmark",
        "description": "Mini benchmark for OpenClaw-style micro-sleep and rule formation",
        "checks": [
            "memory labeling",
            "micro-sleep consolidation",
            "rule promotion",
            "belief graph linkage",
        ],
        "status": "generated",
    }

    markdown_report = """# OpenClaw Mini Benchmark

- Scenario: repeated risky terminal observations
- Goal: verify that Bio-Agent OS converts repeated observations into stable rules
- Coverage:
  - memory labeling
  - micro-sleep consolidation
  - rule promotion
  - belief graph linkage
"""

    (report_dir / "openclaw-mini-benchmark.json").write_text(
        json.dumps(json_report, indent=2),
        encoding="utf-8",
    )
    (report_dir / "openclaw-mini-benchmark.md").write_text(
        markdown_report,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
