"""
LoCoMo dataset loader (Maharana et al., 2024 — snap-research/locomo).

LoCoMo measures very-long-term conversational memory: 10 multi-session
dialogues (~400 turns each) with ~2k QA pairs in five categories:

1. multi-hop, 2. temporal, 3. open-domain, 4. single-hop,
5. adversarial (the correct behaviour is to abstain).
"""

from __future__ import annotations

import ast
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}

_DIA_ID_PATTERN = re.compile(r"D(\d+):(\d+)")


@dataclass
class LocomoTurn:
    speaker: str
    dia_id: str
    text: str
    session_id: int
    session_datetime: str


@dataclass
class LocomoQA:
    question: str
    category: int
    answer: Optional[str] = None
    adversarial_answer: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    @property
    def evidence_sessions(self) -> Set[int]:
        sessions: Set[int] = set()
        for ref in self.evidence:
            match = _DIA_ID_PATTERN.search(str(ref))
            if match:
                sessions.add(int(match.group(1)))
        return sessions


@dataclass
class LocomoConversation:
    sample_id: str
    speaker_a: str
    speaker_b: str
    turns: List[LocomoTurn]
    qa: List[LocomoQA]

    @property
    def session_ids(self) -> List[int]:
        return sorted({turn.session_id for turn in self.turns})


def download_locomo(dest_path: str, url: str = LOCOMO_URL) -> str:
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    if not os.path.exists(dest_path):
        urllib.request.urlretrieve(url, dest_path)
    return dest_path


def _parse_evidence(raw) -> List[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (ValueError, SyntaxError):
            pass
        return [raw]
    return []


def load_locomo(path: str) -> List[LocomoConversation]:
    with open(path, "r", encoding="utf-8") as handle:
        raw_samples = json.load(handle)

    conversations: List[LocomoConversation] = []
    for raw in raw_samples:
        conv: Dict = raw.get("conversation", {})
        turns: List[LocomoTurn] = []
        session_id = 0
        while True:
            session_id += 1
            session_key = f"session_{session_id}"
            if session_key not in conv:
                break
            session_datetime = str(conv.get(f"{session_key}_date_time", ""))
            for raw_turn in conv.get(session_key) or []:
                text = str(raw_turn.get("text", "")).strip()
                if not text:
                    continue
                turns.append(
                    LocomoTurn(
                        speaker=str(raw_turn.get("speaker", "unknown")),
                        dia_id=str(raw_turn.get("dia_id", f"D{session_id}:?")),
                        text=text,
                        session_id=session_id,
                        session_datetime=session_datetime,
                    )
                )

        qa_items: List[LocomoQA] = []
        for raw_qa in raw.get("qa") or []:
            try:
                category = int(raw_qa.get("category"))
            except (TypeError, ValueError):
                continue
            answer = raw_qa.get("answer")
            qa_items.append(
                LocomoQA(
                    question=str(raw_qa.get("question", "")).strip(),
                    category=category,
                    answer=str(answer) if answer is not None else None,
                    adversarial_answer=raw_qa.get("adversarial_answer"),
                    evidence=_parse_evidence(raw_qa.get("evidence")),
                )
            )

        conversations.append(
            LocomoConversation(
                sample_id=str(raw.get("sample_id", f"sample-{len(conversations)}")),
                speaker_a=str(conv.get("speaker_a", "speaker_a")),
                speaker_b=str(conv.get("speaker_b", "speaker_b")),
                turns=turns,
                qa=qa_items,
            )
        )
    return conversations
