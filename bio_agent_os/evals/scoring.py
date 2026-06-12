"""
Deterministic QA scoring.

Categories 1-4 use SQuAD-style normalized token F1 and exact match —
the metric LoCoMo and the Mem0/Zep papers report — so numbers are
reproducible without an LLM judge. Category 5 (adversarial) measures
abstention: the system is correct when it declines to answer instead
of taking the bait.
"""

from __future__ import annotations

import re
import string
from collections import Counter
from typing import Dict, Optional

from bio_agent_os.evals.locomo import LocomoQA

_ARTICLES = re.compile(r"\b(a|an|the)\b", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

ABSTENTION_MARKERS = (
    "no information",
    "not mentioned",
    "not specified",
    "not stated",
    "no answer",
    "i don't know",
    "i do not know",
    "cannot answer",
    "can't answer",
    "not available",
    "unknown",
    "not discussed",
    "does not mention",
    "doesn't mention",
    "no mention",
)


def normalize_answer(text: str) -> str:
    text = str(text).lower()
    text = "".join(char for char in text if char not in string.punctuation)
    text = _ARTICLES.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def token_f1(prediction: str, gold: str) -> float:
    pred_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(gold).split()
    if not pred_tokens or not gold_tokens:
        return float(pred_tokens == gold_tokens)
    common = Counter(pred_tokens) & Counter(gold_tokens)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def exact_match(prediction: str, gold: str) -> float:
    return float(normalize_answer(prediction) == normalize_answer(gold))


def is_abstention(prediction: str) -> bool:
    lowered = str(prediction).lower()
    return any(marker in lowered for marker in ABSTENTION_MARKERS)


def score_prediction(prediction: str, qa: LocomoQA) -> Dict[str, Optional[float]]:
    """
    Score one prediction.

    Returns ``f1``/``em`` for answerable questions, and ``abstained``
    (1.0 = correctly refused) for adversarial ones.
    """
    if qa.category == 5:
        abstained = is_abstention(prediction)
        took_bait = False
        if qa.adversarial_answer:
            took_bait = token_f1(prediction, qa.adversarial_answer) > 0.5
        return {
            "f1": None,
            "em": None,
            "abstained": float(abstained and not took_bait),
        }
    gold = qa.answer or ""
    return {
        "f1": token_f1(prediction, gold),
        "em": exact_match(prediction, gold),
        "abstained": None,
    }
