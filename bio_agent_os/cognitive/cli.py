from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .facade import MemoryOS
from .models import AccessContext, MemoryType, TrustTier, VerificationStatus


def doctor() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "doctor.db"
        memory = MemoryOS(db)
        event = memory.observe(
            tenant_id="doctor",
            actor="cli",
            source="doctor",
            content="Bio-AGI Memory OS doctor event",
            trust_tier=TrustTier.TRUSTED_SYSTEM,
        )
        stored = memory.remember(
            event=event,
            memory_type=MemoryType.SEMANTIC,
            content="Doctor memory is healthy",
            verification_status=VerificationStatus.MACHINE_CHECKED,
        )
        context = AccessContext(tenant_id="doctor", agent_id="doctor-agent")
        recalled = memory.recall("doctor healthy", context=context)
        packet = memory.compile_context("doctor healthy", context=context, goal="Verify memory runtime")
        cached = memory.compile_context("doctor healthy", context=context, goal="Verify memory runtime")
        checkpoint = memory.create_checkpoint(
            tenant_id="doctor", agent_id="doctor-agent", workspace_id=None,
            goal="Verify memory runtime", completed_steps=("write", "recall"),
            pending_steps=("finish",), active_memory_ids=packet.provenance_memory_ids,
        )
        restored = memory.restore_checkpoint("doctor", checkpoint_id=checkpoint.checkpoint_id)
        checks = {
            "event_integrity": memory.events.verify_chain("doctor"),
            "recall": bool(recalled and recalled[0].memory.memory_id == stored.memory_id),
            "context_compile": bool(packet.blocks and stored.memory_id in packet.provenance_memory_ids),
            "context_cache": bool(cached.metrics.get("cache_hit")),
            "checkpoint_restore": bool(restored and restored.checkpoint_id == checkpoint.checkpoint_id),
            "fts_first_stage": bool(memory.memories.fts_available),
            "tenant_scope": not memory.recall("doctor healthy", context=AccessContext(tenant_id="other")),
        }
        ok = all(checks.values())
        print(json.dumps({"status": "ok" if ok else "failed", "version": "0.8.0a1", "checks": checks}, indent=2))
        memory.close()
        return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="bio-agi-memory")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor")
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    parser.print_help()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
