"""The two stores a deletion could not reach, and what happens now that it can.

`privacy_registry` closed the discovery problem: every persistent table is
declared, and an undeclared one fails the suite. It did not close the two
findings it recorded. `rejected_inputs` and `agent_checkpoints` were declared,
described, and still outside `CONTENT_COLUMNS`, so every deletion verified
itself against a store list that did not include them.

Measured on a real MemoryOS on 17/08, before any of this:

    remember(POLICY, "An Phat api_key: sk-live-…")   -> quarantined
    forget_derived(subject="An Phat", needle="An Phat")
                                              -> verified_clean = True
    erase_history(needle="An Phat", confirm=True)
                                              -> verified_clean = True,
                                                 2 events redacted
    rejected_inputs.content                   -> "An Phat api_key: sk-live-…"
    agent_checkpoints.payload_json            -> the same key, verbatim

Both levels did everything they knew how to do, reported an unqualified
success, and left the credential on disk in the same database file.

The store's own docstring argues its content must be verbatim so a rejection
stays replayable — "a rejection you cannot replay is a deletion with extra
steps". That argument is right and nothing here changes how a rejection is
written. What it does not support is the conclusion that was drawn from it:
verbatim and unerasable are different properties. `cognitive_events` is
verbatim, append-only, replay-critical, and redactable, in this same codebase.

So: scanned at every level, so a deletion reports the copy, and rewritten by
`erase_history`, so a deletion can finish. The row, the reasons, the risk score
and the runtime fingerprint survive either way — "which build refused what" is
what the table is for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.forgetting import (
    REDACTION_MARKER, erase_history, forget_derived)
from bio_agent_os.cognitive.models import MemoryType

SUBJECT = "An Phat"
SECRET = f"{SUBJECT} api_key: sk-live-QUARANTINE-9f3a2b7c1d"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "quarantine.db")
    memory_os.attach_runtime()
    yield memory_os
    memory_os.close()


def _reject(memory_os: MemoryOS, text: str = SECRET, entity: str = SUBJECT):
    """Offer an input the immune system will refuse, the way a caller would."""
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(
        event=event, memory_type=MemoryType.POLICY, content=text,
        confidence=0.9,
        structured_content={"entity": entity, "attribute": "api_key",
                            "value": text})


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    stored = memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                                content=text, confidence=0.9)
    return getattr(stored, "memory_id", None) or ""


def _column(memory_os: MemoryOS, table: str, column: str) -> list[str]:
    return [str(v) for (v,) in
            memory_os.memories.conn.execute(f"SELECT {column} FROM {table}")]


# --------------------------------------------------------------------------
# the facts the decision rests on
# --------------------------------------------------------------------------

def test_a_quarantine_happens_because_a_credential_was_detected(os_):
    """Which is what makes this table's population what it is.

    `MemorySecurityScanner.assess` sets `quarantined` only when a secret or an
    injection pattern matched a persistent write. The backlog is therefore not
    arbitrary refused text — it is, by construction, the material the immune
    system exists to keep out of memory.
    """
    decision = _reject(os_)
    assert decision.quarantined is True
    assert "secret_or_credential_pattern" in decision.reasons


def test_the_refused_copy_is_verbatim_and_the_accepted_one_would_not_be(os_):
    """The store's replay argument, kept — and the cost of keeping it, stated.

    `facade._record_rejection` passes `content`, not
    `decision.redacted_content`, which is computed on the same call and
    discarded. Nothing here asks for that to change: a redacted rejection
    cannot be replayed. It does mean the raw credential is on disk, which is
    why the two operations below have to be able to reach it.
    """
    decision = _reject(os_)
    assert decision.redacted_content is not None
    assert "sk-live-QUARANTINE-9f3a2b7c1d" not in decision.redacted_content

    stored = _column(os_, "rejected_inputs", "content")
    assert stored == [SECRET], "bản ghi từ chối phải giữ nguyên văn để replay"
    payload = _column(os_, "rejected_inputs", "payload_json")[0]
    assert SECRET in payload, "structured_content của caller cũng nguyên văn"


def test_a_checkpoint_stores_whatever_the_caller_handed_it(os_):
    """Three columns hold content, not one: `goal` is free text and
    `metadata_json` is an open dict."""
    os_.create_checkpoint(tenant_id="t1", agent_id="ag1", workspace_id="w1",
                          goal=f"xu ly {SUBJECT}",
                          tool_state={"env": {"API_KEY": SECRET}},
                          metadata={"ticket": f"{SUBJECT}-114"})
    assert SUBJECT in _column(os_, "agent_checkpoints", "goal")[0]
    assert SECRET in _column(os_, "agent_checkpoints", "payload_json")[0]
    assert SUBJECT in _column(os_, "agent_checkpoints", "metadata_json")[0]


# --------------------------------------------------------------------------
# the decision, and the test that fails if it is silently reverted
# --------------------------------------------------------------------------

def test_both_stores_are_scanned_by_the_deletion_verifier():
    """Remove any of these and a deletion goes back to reporting clean over a
    credential it never looked at.

    That is the revert this test exists to catch, and it is not hypothetical:
    the registry declared both stores on 16/08 and described exactly what was
    wrong with them, and the verifier's list was unchanged the next day. A
    finding written down is not a finding fixed.
    """
    from bio_agent_os.cognitive.forgetting import CONTENT_COLUMNS

    for entry in (("rejected_inputs", "content"),
                  ("rejected_inputs", "payload_json"),
                  ("agent_checkpoints", "goal"),
                  ("agent_checkpoints", "payload_json"),
                  ("agent_checkpoints", "metadata_json")):
        assert entry in CONTENT_COLUMNS, entry


def test_the_registry_no_longer_records_either_as_unscanned():
    """The registry is where the policy lives, so a revert has to fail here
    too and not only in `forgetting.py`."""
    from bio_agent_os.cognitive import privacy_registry as registry

    for name in ("rejected_inputs", "agent_checkpoints"):
        policy = registry.declared(name)
        assert policy is not None
        assert "NOT scanned" not in policy.verification_strategy, name
        assert policy.erase_history_policy == registry.REDACT, name
        assert policy.note, name
    assert registry.unscanned_content_stores() == []


# --------------------------------------------------------------------------
# before / after on a real MemoryOS
# --------------------------------------------------------------------------

def test_a_reversible_deletion_now_reports_the_copy_it_cannot_remove(os_):
    """`forget_derived` does not touch either store — and now says so.

    Before: `verified_clean = True` with the credential in `rejected_inputs`.
    A caller who needs it actually gone has to be told, or they will not
    escalate.
    """
    _remember(os_, f"Khach hang {SUBJECT} dung dich vu dinh ky.")
    _reject(os_)
    os_.create_checkpoint(tenant_id="t1", agent_id="ag1", workspace_id="w1",
                          goal=f"xu ly {SUBJECT}")

    report = forget_derived(os_, subject=SUBJECT, needle=SUBJECT,
                            actor="tester")
    assert report.memories_deleted == 1
    assert report.checks_run > 0
    assert report.verified_clean is False, "báo sạch trong khi bản sao còn đó"
    where = {hit["where"] for hit in report.residue}
    assert "rejected_inputs.content" in where
    assert "agent_checkpoints.goal" in where
    # Untouched, deliberately: this level is the reversible one.
    assert _column(os_, "rejected_inputs", "content") == [SECRET]


def test_erase_history_rewrites_the_copy_and_keeps_the_record(os_):
    """The other half. A report that can never come back clean trains people
    to ignore it, so the escalation has to actually resolve.

    Scoped by the credential rather than by the subject, because the subject
    also survives — partially redacted — inside the event the refused input
    produced, and that event belongs to no memory so nothing resolves it here.
    Reported as residue, not as clean, so the existing contract holds; it is a
    separate gap from this one and not fixed here.
    """
    _remember(os_, f"Khach hang {SUBJECT} dung dich vu dinh ky.")
    _reject(os_)
    os_.create_checkpoint(tenant_id="t1", agent_id="ag1", workspace_id="w1",
                          goal=f"xu ly {SUBJECT}",
                          tool_state={"env": {"API_KEY": SECRET}})

    report = erase_history(os_, subject=SUBJECT, needle=SECRET,
                           reason="yêu cầu xoá của khách", actor="tuananh",
                           confirm=True)
    assert report.verified_clean is True, report.residue
    assert report.stores_redacted == {"rejected_inputs": 1,
                                      "agent_checkpoints": 1}

    # Every column of a matched row goes, not just the one that matched: the
    # checkpoint matched on `payload_json` and its `goal` named the customer.
    for table, column in (("rejected_inputs", "content"),
                          ("rejected_inputs", "payload_json"),
                          ("agent_checkpoints", "goal"),
                          ("agent_checkpoints", "payload_json")):
        for value in _column(os_, table, column):
            assert SUBJECT not in value, f"{table}.{column}"
            assert "sk-live-QUARANTINE" not in value, f"{table}.{column}"

    # The row survives, and so does everything that makes it a record.
    row = os_.rejected.all()[0]
    assert row["content"] == REDACTION_MARKER
    assert row["reasons"] == ["secret_or_credential_pattern"]
    assert row["risk_score"] is not None
    assert row["runtime_fingerprint"], "ai từ chối, ở build nào — vẫn phải trả lời được"
    assert row["rejected_at"]
    assert os_.rejected.by_runtime()[0]["count"] == 1


def test_an_erased_rejection_leaves_the_replay_queue(os_):
    """`pending()` is the replay queue and a tombstone cannot be replayed.

    Closing it out as `discarded`, with the erasure's actor and reason, is the
    store's own vocabulary for a rejection a person ended deliberately.
    """
    _reject(os_)
    assert os_.rejected.counts() == {"pending": 1}

    erase_history(os_, needle=SECRET, reason="lộ khoá", actor="tuananh",
                  confirm=True)

    assert os_.rejected.pending() == []
    assert os_.rejected.counts() == {"discarded": 1}
    row = os_.rejected.all()[0]
    assert row["resolved_by"] == "tuananh"
    assert "lộ khoá" in row["resolution"]


def test_erase_history_reaches_an_input_that_never_became_a_memory(os_):
    """The early return that hid this whole class.

    A quarantined input has no memory row — that is what quarantine means —
    and no event holding it verbatim, because `observe` stores
    `decision.redacted_content`. So every resolution path `erase_history` had
    came back empty and it returned "không tìm thấy" without looking at the
    one table that did hold the credential.
    """
    _reject(os_)
    assert os_.memories.conn.execute(
        "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0] == 0

    report = erase_history(os_, needle=SECRET, reason="lộ khoá",
                           actor="tuananh", confirm=True)
    assert report.events_redacted == 0, "sự kiện vốn đã được che, không phải ở đây"
    assert report.stores_redacted == {"rejected_inputs": 1}
    assert report.verified_clean is True
    assert "sk-live-QUARANTINE" not in _column(
        os_, "rejected_inputs", "content")[0]


def test_erase_history_still_says_when_it_found_nothing(os_):
    """The early return is narrowed, not removed."""
    _reject(os_)
    report = erase_history(os_, needle="không có chuỗi nào như thế",
                           reason="thử", actor="tuananh", confirm=True)
    assert report.stores_redacted == {}
    assert report.events_redacted == 0
    assert "không tìm thấy" in report.note


# --------------------------------------------------------------------------
# what redaction must not break
# --------------------------------------------------------------------------

def test_a_redacted_checkpoint_restores_empty_rather_than_raising(os_):
    """`_row` indexed the payload keys `save` wrote. After a redaction they are
    not there, and an ordinary read path raising KeyError is something nobody
    can act on. Empty state plus `metadata["redacted"]` is something they can.
    """
    saved = os_.create_checkpoint(tenant_id="t1", agent_id="ag1",
                                  workspace_id="w1", goal=f"xu ly {SUBJECT}",
                                  completed_steps=("buoc 1",),
                                  tool_state={"env": {"API_KEY": SECRET}})
    erase_history(os_, needle=SUBJECT, reason="yêu cầu xoá", actor="tuananh",
                  confirm=True)

    restored = os_.restore_checkpoint("t1", checkpoint_id=saved.checkpoint_id)
    assert restored is not None, "hàng vẫn còn — chỉ nội dung bị ghi đè"
    assert restored.completed_steps == ()
    assert restored.tool_state == {}
    assert restored.metadata.get("redacted") is True
    assert restored.metadata.get("actor") == "tuananh"


def test_a_redacted_payload_is_still_valid_json(os_):
    """Both stores json-decode these columns on read. A marker string where an
    object is expected would turn a redaction into a crash later."""
    _reject(os_)
    os_.create_checkpoint(tenant_id="t1", agent_id="ag1", workspace_id="w1",
                          goal=f"xu ly {SUBJECT}")
    erase_history(os_, needle=SUBJECT, reason="yêu cầu xoá", actor="tuananh",
                  confirm=True)

    for table in ("rejected_inputs", "agent_checkpoints"):
        for blob in _column(os_, table, "payload_json"):
            assert json.loads(blob)["redacted"] is True, table
    # And the store's own reader still works end to end.
    assert os_.rejected.all()[0]["payload"] == json.loads(
        _column(os_, "rejected_inputs", "payload_json")[0])


def test_an_unrelated_rejection_is_left_alone(os_):
    """Redaction is scoped by the probe, like every other level. A store-wide
    wipe would be a much easier implementation and a much worse one."""
    _reject(os_)
    _reject(os_, "password: khac-hoan-toan-9911 cua Cong ty Khac",
            entity="Cong ty Khac")

    erase_history(os_, needle=SUBJECT, reason="yêu cầu xoá", actor="tuananh",
                  confirm=True)

    contents = sorted(_column(os_, "rejected_inputs", "content"))
    assert REDACTION_MARKER in contents
    assert any("khac-hoan-toan-9911" in c for c in contents), \
        "bản ghi của khách khác không được đụng tới"
    assert os_.rejected.counts() == {"pending": 1, "discarded": 1}
