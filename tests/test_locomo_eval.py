"""
LoCoMo harness tests: loader, scoring, question selection, and an
end-to-end runner pass with deterministic fake engines.
"""

import json
from pathlib import Path

import pytest

from bio_agent_os.evals.locomo import LocomoQA, load_locomo
from bio_agent_os.evals.runner import EvalConfig, aggregate, run_locomo_eval, select_questions
from bio_agent_os.evals.scoring import (
    exact_match,
    is_abstention,
    normalize_answer,
    score_prediction,
    token_f1,
)
from bio_agent_os.evals.systems import NaiveRagSystem, NoMemorySystem

STORAGE = Path("test_data")

SYNTHETIC_LOCOMO = [
    {
        "sample_id": "conv-test",
        "conversation": {
            "speaker_a": "Ana",
            "speaker_b": "Binh",
            "session_1_date_time": "2 pm on 1 May, 2025",
            "session_1": [
                {"speaker": "Ana", "dia_id": "D1:1", "text": "I adopted a golden retriever named Sunny."},
                {"speaker": "Binh", "dia_id": "D1:2", "text": "Congrats! I started a pottery class last week."},
            ],
            "session_2_date_time": "5 pm on 9 May, 2025",
            "session_2": [
                {"speaker": "Ana", "dia_id": "D2:1", "text": "Sunny chewed my favorite running shoes."},
                {"speaker": "Binh", "dia_id": "D2:2", "text": "My first pottery vase cracked in the kiln."},
            ],
        },
        "qa": [
            {"question": "What kind of dog did Ana adopt?", "answer": "a golden retriever", "evidence": "['D1:1']", "category": 4},
            {"question": "What happened to Binh's vase?", "answer": "it cracked in the kiln", "evidence": "['D2:2']", "category": 4},
            {"question": "When did Ana adopt Sunny?", "answer": "1 May 2025", "evidence": "['D1:1']", "category": 2},
            {"question": "What prize did Ana win at the dog show?", "adversarial_answer": "first place", "evidence": "['D1:1']", "category": 5},
        ],
    }
]


@pytest.fixture()
def locomo_path() -> str:
    STORAGE.mkdir(exist_ok=True)
    path = STORAGE / "locomo_synthetic.json"
    path.write_text(json.dumps(SYNTHETIC_LOCOMO), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def test_loader_parses_sessions_questions_and_evidence(locomo_path):
    conversations = load_locomo(locomo_path)
    assert len(conversations) == 1
    conv = conversations[0]
    assert conv.sample_id == "conv-test"
    assert conv.session_ids == [1, 2]
    assert len(conv.turns) == 4
    assert conv.turns[0].session_datetime == "2 pm on 1 May, 2025"
    assert len(conv.qa) == 4
    assert conv.qa[0].evidence_sessions == {1}
    assert conv.qa[3].category == 5
    assert conv.qa[3].adversarial_answer == "first place"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_token_f1_and_exact_match():
    assert token_f1("7 May 2023", "7 May 2023") == 1.0
    assert exact_match("The Golden Retriever!", "golden retriever") == 1.0
    assert normalize_answer("The Kiln, cracked.") == "kiln cracked"
    assert 0.0 < token_f1("a golden dog", "a golden retriever") < 1.0
    assert token_f1("completely wrong", "golden retriever") == 0.0


def test_adversarial_scoring_rewards_abstention():
    qa = LocomoQA(question="q", category=5, adversarial_answer="first place")
    assert is_abstention("No information available.")
    assert score_prediction("No information available.", qa)["abstained"] == 1.0
    # Taking the bait fails even if phrased with a hedge.
    assert score_prediction("She won first place", qa)["abstained"] == 0.0


def test_answerable_scoring_returns_f1():
    # Articles are stripped during normalization, so this is a full match.
    qa = LocomoQA(question="q", category=4, answer="a golden retriever")
    scores = score_prediction("golden retriever", qa)
    assert scores["f1"] == 1.0
    assert scores["abstained"] is None
    partial = score_prediction("golden dog", qa)
    assert partial["f1"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Question selection honesty
# ---------------------------------------------------------------------------

def test_select_questions_excludes_evidence_outside_ingested_sessions(locomo_path):
    conv = load_locomo(locomo_path)[0]
    config = EvalConfig(max_sessions=1)
    selected = select_questions(conv, config)
    # Question with evidence in session 2 must be dropped on a partial ingest.
    assert all(qa.evidence_sessions == {1} for qa in selected)
    assert len(selected) == 3  # two session-1 answerables + the adversarial one

    full = select_questions(conv, EvalConfig())
    assert len(full) == 4


# ---------------------------------------------------------------------------
# End-to-end runner with deterministic engines
# ---------------------------------------------------------------------------

class CannedEngine:
    """Answers from the provided context with trivial keyword extraction."""

    backend = "fake"
    model_id = "canned"

    async def generate(self, prompt: str, temperature: float = 0.0, **kwargs) -> str:
        lowered = prompt.lower()
        # Conditions key off the question, then require the supporting
        # evidence to actually be present in the provided context.
        question = lowered.split("question:")[-1]
        if "kind of dog" in question and "golden retriever" in lowered:
            return "a golden retriever"
        if "vase" in question and "kiln" in lowered:
            return "it cracked in the kiln"
        if "when did ana adopt" in question and "1 may" in lowered:
            return "1 May 2025"
        return "No information available."


@pytest.mark.asyncio
async def test_runner_end_to_end_scores_naive_rag_above_no_memory(locomo_path):
    from bio_agent_os.core.embedder import Embedder

    conversations = load_locomo(locomo_path)
    engine = CannedEngine()
    embedder = Embedder()
    config = EvalConfig(top_k=4)

    report = await run_locomo_eval(
        conversations,
        {
            "no-memory": lambda conversation: NoMemorySystem(engine),
            "naive-rag": lambda conversation: NaiveRagSystem(engine, embedder, top_k=4),
        },
        config,
        metadata={"model": "canned"},
    )

    assert report["benchmark"] == "locomo"
    no_memory = report["systems"]["no-memory"]["summary"]
    naive_rag = report["systems"]["naive-rag"]["summary"]

    # Without context the canned engine always abstains: F1 floor of 0,
    # but perfect adversarial abstention.
    assert no_memory["f1_answerable"] == 0.0
    assert no_memory["abstention_rate"] == 1.0
    # With retrieved context it answers the answerable questions.
    assert naive_rag["f1_answerable"] > 0.8
    assert naive_rag["per_category"]["single-hop"]["count"] == 2

    summary = aggregate(report["systems"]["naive-rag"]["conversations"])
    assert summary["questions_total"] == 4
