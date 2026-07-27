"""
Regression — consolidation must mark survivors by entry_id, not timestamp.

``mark_encoded`` matches on the stable UUID. When the hippocampus handed it a
float ``timestamp`` instead, the call silently matched nothing: a survivor that
had just been consolidated stayed ``raw`` and came back through
``get_survivors()`` on every later sleep cycle, so the same memory was compiled
again and again (duplicate L2 rows, repeated rule promotion, wasted LLM calls).

The two guarantees pinned here:
  1. marking by entry_id encodes exactly that survivor;
  2. an encoded survivor never reappears in get_survivors().
"""

import uuid

from bio_agent_os.memory.l1_working import L1WorkingMemory

STORAGE = "test_data"


def _fresh(name: str) -> L1WorkingMemory:
    return L1WorkingMemory(agent_name=f"{name}-{uuid.uuid4().hex[:8]}", storage_dir=STORAGE)


def _age_into_survivor(l1: L1WorkingMemory, entry_id: str, nights: int = 5) -> None:
    """Push one entry past its TTL so consolidation would pick it up."""
    l1.load()
    for entry in l1._entries:
        if str(entry.get("entry_id")) == str(entry_id):
            entry["nights_passed"] = nights
            break
    l1.save()


def test_mark_encoded_by_entry_id_encodes_that_survivor():
    l1 = _fresh("mark-encoded-id")
    entry = l1.add(content="isolate the breaker before opening the hatch", source="worker")
    entry_id = str(entry["entry_id"])
    _age_into_survivor(l1, entry_id)

    assert entry_id in {str(row["entry_id"]) for row in l1.get_survivors()}

    l1.mark_encoded(entry_id)

    l1.load()
    stored = [row for row in l1._entries if str(row["entry_id"]) == entry_id][0]
    assert stored["status"] == "encoded"


def test_encoded_survivor_is_not_recompiled_on_the_next_sleep():
    l1 = _fresh("mark-encoded-once")
    entry = l1.add(content="torque the flange bolts to 120 Nm", source="worker")
    entry_id = str(entry["entry_id"])
    _age_into_survivor(l1, entry_id)

    l1.mark_encoded(entry_id)

    survivors = {str(row["entry_id"]) for row in l1.get_survivors()}
    assert entry_id not in survivors, (
        "an encoded survivor reappeared in get_survivors(): consolidation would "
        "compile the same memory again on every subsequent sleep cycle"
    )


def test_timestamp_is_not_an_identity_and_must_not_encode():
    """The exact defect: a float timestamp must never satisfy the identity match."""
    l1 = _fresh("mark-encoded-timestamp")
    entry = l1.add(content="verify lockout tag before the second isolation", source="worker")
    entry_id = str(entry["entry_id"])
    _age_into_survivor(l1, entry_id)

    l1.mark_encoded(entry["timestamp"])

    l1.load()
    stored = [row for row in l1._entries if str(row["entry_id"]) == entry_id][0]
    assert stored["status"] == "raw", (
        "mark_encoded matched on a timestamp — identity must stay the entry_id, "
        "otherwise two events sharing a millisecond mark the wrong survivor"
    )


def test_two_survivors_sharing_a_timestamp_are_marked_independently():
    """Why the identifier matters: identical timestamps, distinct memories."""
    l1 = _fresh("mark-encoded-collision")
    first = l1.add(content="pump A seal replaced", source="worker")
    second = l1.add(content="pump B seal inspected", source="worker")

    l1.load()
    shared = first["timestamp"]
    for entry in l1._entries:
        entry["timestamp"] = shared
        entry["nights_passed"] = 5
    l1.save()

    l1.mark_encoded(str(first["entry_id"]))

    l1.load()
    by_id = {str(row["entry_id"]): row for row in l1._entries}
    assert by_id[str(first["entry_id"])]["status"] == "encoded"
    assert by_id[str(second["entry_id"])]["status"] == "raw", (
        "marking one survivor encoded the other: identity collapsed onto the timestamp"
    )


def test_consolidation_never_marks_survivors_by_timestamp():
    """Guard the defect site itself.

    The tests above pin the L1 contract; this one pins the caller. The original
    defect was that the hippocampus' successful-promotion path handed
    ``entry["timestamp"]`` to ``mark_encoded`` while both rejection paths
    correctly handed ``entry["entry_id"]`` — so the bug was invisible to any
    test that exercised L1 alone. Reading the consolidation source keeps that
    exact regression from creeping back without needing an LLM in the loop.
    """
    import inspect

    from bio_agent_os.background_jobs import hippocampus

    source = inspect.getsource(hippocampus)
    calls = [
        line.strip()
        for line in source.splitlines()
        if "mark_encoded(" in line and not line.strip().startswith("#")
    ]
    assert calls, "no mark_encoded call found in the consolidation source"
    for call in calls:
        assert 'entry["entry_id"]' in call or "entry['entry_id']" in call, (
            f"consolidation marks a survivor by something other than its entry_id: {call!r} "
            "— a timestamp never matches, so the survivor is re-compiled every sleep cycle"
        )
