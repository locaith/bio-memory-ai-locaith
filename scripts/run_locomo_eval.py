"""
Run the LoCoMo benchmark against Bio-Agent OS and fair baselines.

Examples
--------
Quick smoke run (1 conversation, first 3 sessions, local Ollama):

    python scripts/run_locomo_eval.py --backend ollama --model gemma4:e2b \
        --max-conversations 1 --max-sessions 3 --max-questions 12 \
        --systems no-memory,naive-rag,bio-memory --tag smoke

Full run (all 10 conversations, every question):

    python scripts/run_locomo_eval.py --backend ollama --model qwen2.5:14b-instruct \
        --systems no-memory,naive-rag,bio-memory --tag full
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bio_agent_os.evals.locomo import LOCOMO_URL, download_locomo, load_locomo
from bio_agent_os.evals.runner import EvalConfig, run_locomo_eval, write_report
from bio_agent_os.evals.systems import BioMemorySystem, NaiveRagSystem, NoMemorySystem

DEFAULT_DATASET_PATH = os.path.join("data", "evals", "locomo10.json")
SYSTEM_CHOICES = ("no-memory", "naive-rag", "bio-memory")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LoCoMo benchmark for Bio-Agent OS")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH)
    parser.add_argument("--backend", default=os.getenv("LLM_BACKEND", "ollama"))
    parser.add_argument("--model", default=os.getenv("MODEL_ID", "gemma4:e2b"))
    parser.add_argument(
        "--systems",
        default="no-memory,naive-rag,bio-memory",
        help=f"Comma-separated subset of: {', '.join(SYSTEM_CHOICES)}",
    )
    parser.add_argument("--max-conversations", type=int, default=None)
    parser.add_argument("--max-sessions", type=int, default=None)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument(
        "--categories",
        default="",
        help="Comma-separated category ids (1-5). Empty = all.",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--sleep-every",
        type=int,
        default=20,
        help="Consolidate bio-memory every N ingested turns (0 disables).",
    )
    parser.add_argument("--storage-dir", default=os.path.join("data", "evals", "runs"))
    parser.add_argument("--out-dir", default="benchmark_reports")
    parser.add_argument("--tag", default=None)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    os.environ["LLM_BACKEND"] = args.backend
    os.environ["MODEL_ID"] = args.model

    dataset_path = args.dataset
    if not os.path.exists(dataset_path):
        print(f"Downloading LoCoMo dataset from {LOCOMO_URL} ...")
        download_locomo(dataset_path)
    conversations = load_locomo(dataset_path)
    print(f"Loaded {len(conversations)} conversations.")

    categories = [int(item) for item in args.categories.split(",") if item.strip()]
    config = EvalConfig(
        max_conversations=args.max_conversations,
        max_sessions=args.max_sessions,
        max_questions_per_conversation=args.max_questions,
        categories=categories or None,
        top_k=args.top_k,
        sleep_every=args.sleep_every,
    )

    requested = [name.strip() for name in args.systems.split(",") if name.strip()]
    unknown = set(requested) - set(SYSTEM_CHOICES)
    if unknown:
        raise SystemExit(f"Unknown systems: {sorted(unknown)}")

    from bio_agent_os.core.embedder import Embedder
    from bio_agent_os.core.llm_engine import LLMEngine

    engine = LLMEngine.from_env()
    print(f"Engine: backend={engine.backend} model={engine.model_id}")

    factories = {}
    if "no-memory" in requested:
        factories["no-memory"] = lambda conversation: NoMemorySystem(engine)
    if "naive-rag" in requested:
        embedder = Embedder()
        factories["naive-rag"] = lambda conversation: NaiveRagSystem(
            engine, embedder, top_k=config.top_k
        )
    if "bio-memory" in requested:
        from bio_agent_os.core.runtime import build_runtime

        run_stamp = time.strftime("%Y%m%d-%H%M%S")

        def bio_factory(conversation):
            # A fresh agent per conversation keeps L1/L2 state independent;
            # the workspace filter additionally isolates retrieval.
            storage = os.path.join(args.storage_dir, run_stamp)
            runtime = build_runtime(
                agent_name=f"locomo-{conversation.sample_id}",
                storage_dir=storage,
            )
            return BioMemorySystem(
                runtime,
                workspace_id=conversation.sample_id,
                sleep_every=config.sleep_every,
                top_k=config.top_k,
            )

        factories["bio-memory"] = bio_factory

    # The embedding backend belongs in the record as much as the LLM does.
    #
    # Without it, a report cannot be read. Started a re-run of the June numbers
    # on 2026-08-09 and the first line of output was "No real embedding backend
    # active — semantic search is DEGRADED (hash mode, near-random recall)". The
    # run would have produced a low score for bio-memory that had nothing to do
    # with bio-memory — and the June reports, which record only `model`, cannot
    # be checked for the same fault. A benchmark that does not say how it was
    # configured is a number without a claim attached.
    embedding = {
        "backend": os.environ.get("EMBEDDING_BACKEND") or "unset",
        "model": os.environ.get("EMBEDDING_MODEL") or "unset",
        "dimensions": os.environ.get("EMBEDDING_DIMENSIONS") or "unset",
        "base_url_host": (os.environ.get("EMBEDDING_BASE_URL") or "unset").split("/")[2]
                         if "//" in (os.environ.get("EMBEDDING_BASE_URL") or "") else "unset",
    }
    embedding["degraded_hash_mode"] = embedding["backend"] == "unset"

    metadata = {
        "backend": engine.backend,
        "model": engine.model_id,
        "embedding": embedding,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": dataset_path,
        "dataset_url": LOCOMO_URL,
    }
    if embedding["degraded_hash_mode"]:
        print("  WARNING: no embedding backend configured. Semantic recall is in "
              "hash fallback and any bio-memory score below is meaningless.",
              flush=True)

    report = await run_locomo_eval(
        conversations,
        factories,
        config,
        metadata=metadata,
        progress=print,
    )

    tag = args.tag or f"{engine.backend}_{engine.model_id}".replace(":", "-").replace(
        "/", "-"
    )
    paths = write_report(report, args.out_dir, tag)
    print(f"Report written: {paths['json']}")
    print(f"Report written: {paths['markdown']}")

    for name, data in report["systems"].items():
        summary = data["summary"]
        print(
            f"{name}: questions={summary.get('questions_total')} "
            f"F1={summary.get('f1_answerable')} EM={summary.get('em_answerable')} "
            f"abstention={summary.get('abstention_rate')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
