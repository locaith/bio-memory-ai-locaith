"""When a claim was learned has to be something a caller can state.

Found on 2026-08-14 while designing the lifetime benchmark, which needs a world
where a thousand events span two simulated years. `observe()` and `remember()`
had no way to say when an observation happened:

    observe(...)   -> EventRecord.observed_at   = utc_now()
    remember(...)  -> CognitiveMemory.observed_at = utc_now()

`valid_from` and `valid_to` already pass through both, and `remember()` already
inherits them from the event. `observed_at` was the one temporal field with no
route in, so every memory in a simulated two-year history landed with an age of
zero. Consequences:

* `staleness.age_days` is 0 for everything, so `describe()` never marks a memory
  stale and `annotate()` never dates a claim. The one memory-specific mechanism
  with a measured win (2/3 against plain RAG's 0/3) becomes untestable.
* `annotate()` would stamp "[ghi nhận 2026-08-14]" on a price recorded in the
  simulated year 2024 — inventing a provenance date rather than reporting one.

So the benchmark could not have measured the thing it exists to measure, and
would have reported a number anyway. This is the benchmark forcing the design
rather than the design writing its own exam.

The default stays `None`, and `None` keeps the previous behaviour exactly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bio_agent_os.cognitive import staleness
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType

TWO_YEARS_AGO = "2024-06-01T09:00:00+00:00"


@pytest.fixture()
def os_(tmp_path: Path):
    memory_os = MemoryOS(tmp_path / "time.db")
    yield memory_os
    memory_os.close()


def test_observe_accepts_an_explicit_observation_time(os_):
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Giá bán là 6.500.000 VND", workspace_id="w1",
                        observed_at=TWO_YEARS_AGO)
    assert event.observed_at == TWO_YEARS_AGO


def test_the_memory_inherits_the_event_observation_time(os_):
    """Exactly as it already inherits `valid_from` and `valid_to`. A memory
    observed later than the event it came from is a claim about a time nobody
    stated."""
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Giá bán là 6.500.000 VND", workspace_id="w1",
                        observed_at=TWO_YEARS_AGO)
    memory = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="Giá bán là 6.500.000 VND", confidence=0.9)
    assert memory.observed_at == TWO_YEARS_AGO


def test_an_injected_past_time_makes_staleness_measurable(os_):
    """The reason any of this matters."""
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Giá bán là 6.500.000 VND", workspace_id="w1",
                        observed_at=TWO_YEARS_AGO)
    memory = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="Giá bán là 6.500.000 VND", confidence=0.9)

    info = staleness.describe(memory)
    assert info["age_days"] and info["age_days"] > 700
    assert info["time_sensitive"] is True
    assert info["stale"] is True
    assert staleness.annotate(memory.content, info).endswith("[ghi nhận 2024-06-01]")


def test_omitting_it_keeps_the_previous_behaviour(os_):
    """Default None must be byte-identical to what shipped, or every existing
    deployment quietly changes."""
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    event = os_.observe(tenant_id="t1", actor="a", source="u",
                        content="Máy chủ OCR chạy ở cổng 8000.", workspace_id="w1")
    memory = os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                          content="Máy chủ OCR chạy ở cổng 8000.", confidence=0.9)

    assert datetime.fromisoformat(event.observed_at) >= before
    assert datetime.fromisoformat(memory.observed_at) >= before
    assert staleness.age_days(memory) == pytest.approx(0.0, abs=0.01)


def test_a_simulated_history_orders_correctly_in_the_store(os_):
    """A thousand events spanning two years have to come back in the order they
    were observed, not the order they were written."""
    stamps = ["2024-01-01T00:00:00+00:00",
              "2025-01-01T00:00:00+00:00",
              "2026-01-01T00:00:00+00:00"]
    for index, stamp in enumerate(reversed(stamps)):        # written newest first
        event = os_.observe(tenant_id="t1", actor="a", source="u",
                            content=f"Sự kiện thứ {index}", workspace_id="w1",
                            observed_at=stamp)
        os_.remember(event=event, memory_type=MemoryType.SEMANTIC,
                     content=f"Sự kiện thứ {index}", confidence=0.9)

    rows = [r[0] for r in os_.memories.conn.execute(
        "SELECT observed_at FROM cognitive_memories ORDER BY observed_at")]
    assert rows == stamps
