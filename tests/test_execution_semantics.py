"""What the system *did*, not what it answered.

A suite of 1055 tests stayed green through `376c4ce`, which put a cosine pass
back on 37 of 38 temporal queries and cost eight correct answers. Every test
asked what came out. None asked which path ran. These do.

The rule the counters enforce:

    Fallback is an exception path justified by missing decision-relevant
    evidence, not a parallel retrieval strategy. **Fallback requires a reason.**

Three states that must never merge, because collapsing any two of them is a
different bug:

    MISMATCH      this row is about another slot. Ordinary. Not evidence of
                  anything, and not a reason to search.
    STRUCTURED_GAP this person has memories and none in the slot asked about.
                  The one condition that justifies a rescue.
    NO_SUBJECT_HISTORY nothing is known about this person. UNKNOWN, and
                  nothing to bound a search by.

`376c4ce` merged the first into the second and paid eight questions for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive import temporal_operator as T
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import AccessContext, MemoryType

CTX = AccessContext(tenant_id="t1", workspace_id="w1")


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "exec.db")
    T.reset_execution()
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str,
              at: str = "2024-01-01T00:00:00+00:00") -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1",
                              observed_at=at)
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


def _full_record(memory_os: MemoryOS) -> None:
    _remember(memory_os, "Phạm Vy làm việc tại công ty An Phát.")
    _remember(memory_os, "Số điện thoại của Phạm Vy là 0912345678.")
    _remember(memory_os, "Phạm Vy đang giữ chức trưởng nhóm.")
    _remember(memory_os, "Phạm Vy sinh ngày 12/03/1990.")


# --------------------------------------------------------------------------
# the three shapes
# --------------------------------------------------------------------------

def test_case_a_structured_complete_never_falls_back(os_):
    """A record full of other slots is not a reason to search.

    Asking about an employer when the person also has a phone, a role and a
    birthday: all three "mismatch", and none of that is evidence.
    """
    _full_record(os_)
    T.reset_execution()
    T.claim_history(os_, subject="Phạm Vy", aspect="employer",
                    predicate="employer", context=CTX)

    report = T.execution_report()
    assert report["structured_branch_taken"] == 1
    assert report["fallback_attempted"] == 0
    assert report["unnecessary_fallback"] == 0
    assert report["by_aspect_calls"] == 0, (
        "gọi cosine dù bằng chứng structured đã đủ — đúng lỗi 376c4ce")


def test_case_b_a_structured_gap_may_open_the_rescue_lane(os_):
    """An employer statement mis-tagged as a phone. The slot asked about is
    empty, and *that* is the reason a search is allowed."""
    _remember(os_, "Số điện thoại của Phạm Vy là 0912345678.")
    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    import json

    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"entity": "Phạm Vy", "attribute": "phone",
                     "predicate_epistemic_status": "untrusted"}), memory_id))
    os_.memories.conn.commit()

    T.reset_execution()
    spans = T.claim_history(os_, subject="Phạm Vy", aspect="employer",
                            predicate="employer", context=CTX)
    # No employer slot exists, so the structured branch does not fire and the
    # legacy path carries the query — a gap, handled, not a silent loss.
    assert T.execution_report()["structured_branch_taken"] == 0
    assert any("An Phát" in s.content for s in spans), (
        "bằng chứng gắn sai slot biến mất — F2 quay lại")


def test_case_c_no_subject_history_searches_nothing(os_):
    """Nothing is known about this person, so there is nothing for a bounded
    search to be bounded by."""
    _full_record(os_)
    T.reset_execution()
    spans = T.claim_history(os_, subject="Trần Không Tồn Tại",
                            aspect="employer", predicate="employer",
                            context=CTX)
    assert spans == []
    assert T.execution_report()["fallback_attempted"] == 0


# --------------------------------------------------------------------------
# mutants
# --------------------------------------------------------------------------

def test_eager_rescue_mutant_is_caught_by_unnecessary_fallback(os_):
    """`376c4ce`, reproduced deliberately.

    Rescuing on the existence of a mismatched row rather than on a gap. The
    answer may still look fine; the execution is wrong, and only an execution
    counter sees it.
    """
    _full_record(os_)
    T.reset_execution()
    T.claim_history(os_, subject="Phạm Vy", aspect="employer",
                    predicate="employer", context=CTX, rescue=True)

    report = T.execution_report()
    assert report["unnecessary_fallback"] == 1
    assert report["unnecessary_fallback_rate"] > 0, (
        "mutant eager-rescue không bị số đo nào bắt — suite lại mù như cũ")


def test_no_rescue_mutant_is_caught_by_losing_evidence(os_, monkeypatch):
    """The opposite mutant: never recover a mis-slotted row.

    It must fail the F2 reproduction, or "exclude everything" would look like
    a valid design.
    """
    import json

    _remember(os_, "Phạm Vy đang giữ chức trưởng nhóm.")
    memory_id = _remember(os_, "Phạm Vy làm việc tại công ty An Phát.")
    os_.memories.conn.execute(
        "UPDATE cognitive_memories SET structured_json = ? WHERE memory_id = ?",
        (json.dumps({"entity": "Phạm Vy", "attribute": "job_title",
                     "predicate_epistemic_status": "untrusted"}), memory_id))
    os_.memories.conn.commit()

    # NO_RESCUE: the aspect filter never rescues anything.
    monkeypatch.setattr(T, "_positively_selected", lambda *a, **k: [])
    monkeypatch.setattr(T, "_by_aspect", lambda os_, candidates, aspect: [])

    spans = T.claim_history(os_, subject="Phạm Vy", aspect="employer",
                            predicate="employer", context=CTX, rescue=True)
    assert not any("An Phát" in s.content for s in spans), (
        "mutant no-rescue vẫn tìm thấy bằng chứng — repro F2 chưa chứng minh "
        "được gì")


def test_the_counters_carry_their_denominator():
    """A rate without one is a number somebody will quote in isolation."""
    T.reset_execution()
    report = T.execution_report()
    assert "structured_queries" in report
    assert report["unnecessary_fallback_rate"] == 0.0
