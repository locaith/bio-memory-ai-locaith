from __future__ import annotations

import argparse
import shutil
from pathlib import Path

EXPORT_BLOCK = '''
# Bio-AGI Memory OS v0.8 alpha overlay
from bio_agent_os.cognitive import MemoryOS, ClaudeCodeHookAdapter
from bio_agent_os.context_fabric import (
    AgentCheckpoint, ContextBlock, ContextBlockKind, ContextPacket, StorageTier,
)
from bio_agent_os.cognitive.models import (
    AccessContext, BeliefState, CognitiveMemory, EpistemicStatus, EventRecord,
    ExecutionOutcome, MemoryType, Modality, ProspectiveTrigger, SecurityLabel,
    TrustTier, VerificationStatus,
)
'''


def copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def backup_and_replace(source: Path, destination: Path, backup_suffix: str) -> None:
    if destination.exists():
        backup = destination.with_name(destination.name + backup_suffix)
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(destination, backup)
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Bio-AGI Memory OS v0.8 alpha to an existing Bio-Agent OS checkout")
    parser.add_argument("--target", required=True, help="Path to existing repository root")
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parents[1]
    target_root = Path(args.target).resolve()
    package = target_root / "bio_agent_os"
    if not package.is_dir():
        raise SystemExit(f"Not a Bio-Agent OS checkout: missing {package}")

    backup_and_replace(source_root / "bio_agent_os" / "cognitive", package / "cognitive", ".pre-v080-backup")
    backup_and_replace(source_root / "bio_agent_os" / "context_fabric", package / "context_fabric", ".pre-v080-backup")

    for directory in ("integrations",):
        copy_tree(source_root / directory, target_root / directory)

    for filename in (
        "ARCHITECTURE.md",
        "KNOWN_GAPS.md",
        "CLAUDE_REVIEW_BRIEF.md",
        "HEAD_TO_HEAD_HARNESS_PLAN.md",
        "IMPLEMENTATION_REPORT.md",
        "CLAUDE_CONTINUATION_STATE.json",
    ):
        source = source_root / filename
        if source.exists():
            shutil.copy2(source, target_root / filename)

    upgrade_tests = target_root / "tests" / "v080_cognitive_context"
    copy_tree(source_root / "tests", upgrade_tests)
    upgrade_bench = target_root / "benchmarks" / "v080_cognitive_context"
    copy_tree(source_root / "benchmarks", upgrade_bench)

    init_file = package / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    if "Bio-AGI Memory OS v0.8 alpha overlay" not in text:
        init_file.with_suffix(".py.pre-v080-backup").write_text(text, encoding="utf-8")
        init_file.write_text(text.rstrip() + "\n" + EXPORT_BLOCK, encoding="utf-8")

    print(f"Applied v0.8 alpha overlay to {target_root}")
    print("No commit was created. Review CLAUDE_REVIEW_BRIEF.md first.")
    print("Run upstream tests plus tests/v080_cognitive_context and the benchmark preflight.")


if __name__ == "__main__":
    main()
