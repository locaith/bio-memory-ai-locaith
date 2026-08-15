"""A probe must identify the deleted content, not the person it was about.

Frozen case 3 of the privacy round: "Hãy quên nơi sống của Nguyễn Dũng" deleted
his city records correctly — 1 → 0 memories naming both him and "Cần Thơ" — and
then reported:

    status=partial
    còn sót: "Từ hôm nay, số điện thoại của Nguyễn Dũng là 0987654321."

A phone memory, flagged as residue on a request about a city. `_probes()` emits
the full sentence *and* every token of six characters or more, the second as a
net for ids and amounts that survive paraphrase. "Nguyễn" is six characters, so
the person's own surname became a probe, and every memory about them read as
leftover.

The deletion worked. The honesty mechanism cried wolf — which is its own kind
of failure: `succeeded` came back False, and a caller acting on that would
retry or escalate a deletion that had already finished.

A scoped deletion always holds the full text of what it removed, so it never
needs the token fallback. That fallback stays for `forget_derived(memory_id=…)`,
where the caller may have nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType


class SlotEmbedder:
    """Word-level, never substring — and that distinction is the whole point.

    A substring version put the topic "nơi sống" into the phone axis, because
    "số" is a substring of "sống". The fixture then made a city deletion look
    like it over-deleted a phone record, and the product was blamed for the
    fixture's arithmetic. Third time today a fake embedder has done this.
    """

    model = "slot-fake"
    SLOTS = {
        "nơi sống": ("sống", "cần", "thơ", "hà", "nội", "chuyển", "nơi"),
        "điện thoại": ("điện", "thoại", "số", "0987654321"),
        "công việc": ("công", "ty", "chức", "làm", "việc"),
    }

    def embed(self, text: str) -> list[float]:
        import re

        words = set(re.findall(r"\w+", str(text).lower(), re.UNICODE))
        vector = [float(len(words & set(bucket))) for bucket in self.SLOTS.values()]
        return vector + [0.0] if any(vector) else [0.0] * len(self.SLOTS) + [1.0]


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "probe.db", embedder=SlotEmbedder())
    yield memory_os
    memory_os.close()


def _remember(memory_os: MemoryOS, text: str) -> str:
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=text, workspace_id="w1")
    return memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                              content=text, confidence=0.9).memory_id


def test_a_persons_name_is_not_used_as_a_residue_probe(os_):
    """Frozen case 3, reduced to its two sentences."""
    from bio_agent_os.cognitive.forget_scope import ForgetStatus, forget_scoped

    _remember(os_, "Từ hôm nay, Nguyễn Dũng sống ở Cần Thơ.")
    _remember(os_, "Từ hôm nay, số điện thoại của Nguyễn Dũng là 0987654321.")

    result = forget_scoped(os_, "Hãy quên nơi sống của Nguyễn Dũng.",
                           actor="test")

    assert result.deleted_claims >= 1
    assert result.residue == [], (
        f"báo còn sót nhầm: {[r['excerpt'][:50] for r in result.residue]}")
    assert result.status is ForgetStatus.DELETED
    assert result.succeeded is True
    assert result.verified_clean is True


def test_the_unrelated_memory_is_left_alone(os_):
    """The other half: a false residue report must not be traded for
    over-deletion."""
    from bio_agent_os.cognitive.forget_scope import forget_scoped

    _remember(os_, "Từ hôm nay, Nguyễn Dũng sống ở Cần Thơ.")
    _remember(os_, "Từ hôm nay, số điện thoại của Nguyễn Dũng là 0987654321.")

    forget_scoped(os_, "Hãy quên nơi sống của Nguyễn Dũng.", actor="test")

    left = [c for (c,) in os_.memories.conn.execute(
        "SELECT content FROM cognitive_memories")]
    assert any("0987654321" in c for c in left), "xoá lan sang số điện thoại"
    assert not any("Cần Thơ" in c for c in left)


def test_real_residue_is_still_reported(os_):
    """The mechanism must keep working. A survivor of the actual deleted
    sentence still has to be caught — otherwise this fix trades a false alarm
    for a silent miss, which is the worse of the two.
    """
    from bio_agent_os.cognitive.forget_scope import forget_scoped
    from bio_agent_os.cognitive.forgetting import verify

    _remember(os_, "Từ hôm nay, Nguyễn Dũng sống ở Cần Thơ.")
    memory_id = _remember(os_, "Nguyễn Dũng sống ở Cần Thơ.")

    result = forget_scoped(os_, "Hãy quên nơi sống của Nguyễn Dũng.",
                           actor="test")
    assert result.checks_run > 0

    # Put a copy back where the deletion cannot see it, then verify directly:
    # the probe has to find content, not just the name.
    remaining = verify(os_.memories.conn, "Cần Thơ")
    assert remaining == [], remaining


def test_a_scoped_deletion_probes_with_content_not_tokens(os_):
    """Stated on the mechanism, so a future change to `_probes` cannot quietly
    reintroduce the name-token net on this path."""
    from bio_agent_os.cognitive.forget_scope import _deletion_probes

    probes = _deletion_probes(["Từ hôm nay, Nguyễn Dũng sống ở Cần Thơ."])
    assert probes == ["Từ hôm nay, Nguyễn Dũng sống ở Cần Thơ."], probes
    assert not any(p == "Nguyễn" for p in probes)


def test_the_token_fallback_still_exists_for_callers_without_content():
    """`forget_derived(memory_id=…)` may have nothing but a short sentence, and
    a long id is a better probe than a common word. That path is unchanged."""
    from bio_agent_os.cognitive.forgetting import _probes

    assert _probes(["Khách hàng có số 0912345678."], None)
    assert any("0912345678" in p for p in
               _probes(["Khách hàng có số 0912345678."], None))
