"""
LoCoMo evaluation runner: ingest -> answer -> score -> report.

Honesty rules baked in:
- every configuration knob is recorded in the report;
- partial ingests (``max_sessions``) only score questions whose evidence
  lies entirely inside the ingested sessions, so coverage is never faked;
- per-category counts are always reported next to the means.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Callable, Dict, List, Optional

from bio_agent_os.evals.locomo import CATEGORY_NAMES, LocomoConversation, LocomoQA
from bio_agent_os.evals.scoring import score_prediction


@dataclass
class EvalConfig:
    max_conversations: Optional[int] = None
    max_sessions: Optional[int] = None
    max_questions_per_conversation: Optional[int] = None
    categories: Optional[List[int]] = None
    top_k: int = 10
    sleep_every: int = 20
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_conversations": self.max_conversations,
            "max_sessions": self.max_sessions,
            "max_questions_per_conversation": self.max_questions_per_conversation,
            "categories": self.categories,
            "top_k": self.top_k,
            "sleep_every": self.sleep_every,
            **self.extra,
        }


def select_questions(
    conversation: LocomoConversation, config: EvalConfig
) -> List[LocomoQA]:
    ingested_sessions = set(conversation.session_ids)
    if config.max_sessions is not None:
        ingested_sessions = {
            session for session in ingested_sessions if session <= config.max_sessions
        }
    selected: List[LocomoQA] = []
    for qa in conversation.qa:
        if not qa.question:
            continue
        if config.categories and qa.category not in config.categories:
            continue
        evidence_sessions = qa.evidence_sessions
        # Without evidence sessions we cannot prove the answer was ingested.
        if not evidence_sessions or not evidence_sessions.issubset(ingested_sessions):
            continue
        selected.append(qa)
        if (
            config.max_questions_per_conversation
            and len(selected) >= config.max_questions_per_conversation
        ):
            break
    return selected


async def evaluate_conversation(
    conversation: LocomoConversation,
    system,
    config: EvalConfig,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    log = progress or (lambda message: None)

    turns = [
        turn
        for turn in conversation.turns
        if config.max_sessions is None or turn.session_id <= config.max_sessions
    ]
    questions = select_questions(conversation, config)

    ingest_started = time.time()
    for index, turn in enumerate(turns, start=1):
        await system.ingest_turn(turn)
        if index % 25 == 0:
            log(f"  [{system.name}] ingested {index}/{len(turns)} turns")
    await system.finalize_ingest()
    ingest_seconds = time.time() - ingest_started

    results: List[Dict[str, Any]] = []
    answer_started = time.time()
    for index, qa in enumerate(questions, start=1):
        prediction = await system.answer(qa.question)
        scores = score_prediction(prediction, qa)
        results.append(
            {
                "question": qa.question,
                "category": qa.category,
                "category_name": CATEGORY_NAMES.get(qa.category, str(qa.category)),
                "gold": qa.answer,
                "adversarial_answer": qa.adversarial_answer,
                "prediction": str(prediction),
                **scores,
            }
        )
        if index % 10 == 0:
            log(f"  [{system.name}] answered {index}/{len(questions)} questions")
    answer_seconds = time.time() - answer_started

    return {
        "sample_id": conversation.sample_id,
        "turns_ingested": len(turns),
        "questions": results,
        "ingest_seconds": round(ingest_seconds, 2),
        "answer_seconds": round(answer_seconds, 2),
    }


def aggregate(per_conversation: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_questions = [
        question
        for conversation in per_conversation
        for question in conversation["questions"]
    ]
    per_category: Dict[str, Dict[str, Any]] = {}
    for category, name in CATEGORY_NAMES.items():
        bucket = [item for item in all_questions if item["category"] == category]
        if not bucket:
            continue
        entry: Dict[str, Any] = {"count": len(bucket)}
        if category == 5:
            entry["abstention_rate"] = round(
                mean(item["abstained"] for item in bucket), 4
            )
        else:
            entry["f1"] = round(mean(item["f1"] for item in bucket), 4)
            entry["em"] = round(mean(item["em"] for item in bucket), 4)
        per_category[name] = entry

    answerable = [item for item in all_questions if item["category"] != 5]
    summary: Dict[str, Any] = {
        "questions_total": len(all_questions),
        "questions_answerable": len(answerable),
        "per_category": per_category,
    }
    if answerable:
        summary["f1_answerable"] = round(mean(item["f1"] for item in answerable), 4)
        summary["em_answerable"] = round(mean(item["em"] for item in answerable), 4)
    adversarial = [item for item in all_questions if item["category"] == 5]
    if adversarial:
        summary["abstention_rate"] = round(
            mean(item["abstained"] for item in adversarial), 4
        )
    summary["ingest_seconds_total"] = round(
        sum(conversation["ingest_seconds"] for conversation in per_conversation), 2
    )
    summary["answer_seconds_total"] = round(
        sum(conversation["answer_seconds"] for conversation in per_conversation), 2
    )
    return summary


async def run_locomo_eval(
    conversations: List[LocomoConversation],
    system_factories: Dict[str, Callable[[LocomoConversation], Any]],
    config: EvalConfig,
    metadata: Optional[Dict[str, Any]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    log = progress or (lambda message: None)
    if config.max_conversations is not None:
        conversations = conversations[: config.max_conversations]

    systems_report: Dict[str, Any] = {}
    for system_name, factory in system_factories.items():
        log(f"=== system: {system_name} ===")
        per_conversation: List[Dict[str, Any]] = []
        for conversation in conversations:
            log(f"conversation {conversation.sample_id}")
            system = factory(conversation)
            per_conversation.append(
                await evaluate_conversation(conversation, system, config, progress=log)
            )
        systems_report[system_name] = {
            "summary": aggregate(per_conversation),
            "conversations": per_conversation,
        }

    return {
        "benchmark": "locomo",
        "config": config.to_dict(),
        "metadata": metadata or {},
        "conversations_evaluated": [c.sample_id for c in conversations],
        "systems": systems_report,
    }


def write_report(report: Dict[str, Any], out_dir: str, tag: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"locomo_{tag}.json")
    md_path = os.path.join(out_dir, f"locomo_{tag}.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return {"json": json_path, "markdown": md_path}


def render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = ["# LoCoMo Benchmark Report", ""]
    metadata = report.get("metadata", {})
    if metadata:
        for key, value in metadata.items():
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    lines.append("## Configuration")
    for key, value in report.get("config", {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append(
        f"- conversations evaluated: {', '.join(report.get('conversations_evaluated', []))}"
    )
    lines.append("")

    systems = report.get("systems", {})
    lines.append("## Results")
    lines.append("")
    header = (
        "| System | Questions | F1 (answerable) | EM | Abstention (adversarial) "
        "| Ingest s | Answer s |"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|")
    for name, data in systems.items():
        summary = data["summary"]
        lines.append(
            f"| {name} | {summary.get('questions_total', 0)} "
            f"| {summary.get('f1_answerable', '—')} "
            f"| {summary.get('em_answerable', '—')} "
            f"| {summary.get('abstention_rate', '—')} "
            f"| {summary.get('ingest_seconds_total', '—')} "
            f"| {summary.get('answer_seconds_total', '—')} |"
        )
    lines.append("")

    for name, data in systems.items():
        lines.append(f"### {name} — per category")
        lines.append("")
        lines.append("| Category | Count | F1 | EM | Abstention |")
        lines.append("|---|---|---|---|---|")
        for category_name, entry in data["summary"].get("per_category", {}).items():
            lines.append(
                f"| {category_name} | {entry.get('count', 0)} "
                f"| {entry.get('f1', '—')} | {entry.get('em', '—')} "
                f"| {entry.get('abstention_rate', '—')} |"
            )
        lines.append("")
    return "\n".join(lines)
