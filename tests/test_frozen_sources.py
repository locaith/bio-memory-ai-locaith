"""A benchmark artefact used as "before" is read-only.

On 17/08 a migration ran against four finished run databases with `--apply`.
The migration was correct and its numbers were true. The damage was that
`.staging/lifetime/run.db` had produced the `before` arm of three published
comparisons and could no longer reproduce its own numbers — the evidence for
a claim was edited by the work that claim was measuring.

Nothing caught it. The suite was green, the gate passed, and the report said
`"applied": true` in a field nobody read as a warning.

    frozen source -> clone -> transform the clone -> measure
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.evals import frozen


def _make_db(path: Path, rows: int = 3) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cognitive_memories (memory_id TEXT, "
                 "content TEXT, structured_json TEXT DEFAULT '{}')")
    conn.executemany(
        "INSERT INTO cognitive_memories(memory_id, content) VALUES (?,?)",
        [(f"m{i}", f"Phạm Vy sống ở thành phố {i}.") for i in range(rows)])
    conn.commit()
    conn.close()
    return path


def test_a_frozen_source_that_changed_refuses_the_comparison(tmp_path):
    """Raises rather than warns. A warning about evidence integrity is one
    that gets read after the report is written."""
    db = _make_db(tmp_path / "run.db")
    frozen.freeze(tmp_path, [db])
    frozen.verify(tmp_path, db)                    # unchanged: fine

    conn = sqlite3.connect(db)
    conn.execute("UPDATE cognitive_memories SET structured_json='{\"a\":1}'")
    conn.commit()
    conn.close()

    with pytest.raises(frozen.FrozenSourceChanged) as caught:
        frozen.verify(tmp_path, db)
    assert "before" in str(caught.value)


def test_an_unfrozen_artefact_is_not_policed(tmp_path):
    """Nothing is being claimed about it, so nothing is enforced. The rule is
    about evidence, not about files."""
    db = _make_db(tmp_path / "scratch.db")
    frozen.verify(tmp_path, db)


def test_a_clone_carries_the_sidecar_files(tmp_path):
    """A WAL left behind would make the copy disagree with its source in a
    way no hash of the main file can see."""
    db = _make_db(tmp_path / "run.db")
    Path(str(db) + "-wal").write_bytes(b"wal")
    copy = frozen.clone(db, tmp_path / "derived" / "run.db")
    assert copy.exists()
    assert Path(str(copy) + "-wal").exists()
    assert frozen.digest(copy) == frozen.digest(db)


def test_transforming_the_clone_leaves_the_source_alone(tmp_path):
    db = _make_db(tmp_path / "run.db")
    frozen.freeze(tmp_path, [db])
    before = frozen.digest(db)

    copy = frozen.clone(db, tmp_path / "derived" / "run.db")
    conn = sqlite3.connect(copy)
    conn.execute("UPDATE cognitive_memories SET structured_json='{\"a\":1}'")
    conn.commit()
    conn.close()

    assert frozen.digest(db) == before
    assert frozen.digest(copy) != before
    frozen.verify(tmp_path, db)


def test_a_report_can_name_the_exact_artefact_that_produced_it(tmp_path):
    """Two hashes, because they answer different questions: is the `before`
    still the `before`, and which file produced these numbers."""
    db = _make_db(tmp_path / "run.db")
    copy = frozen.clone(db, tmp_path / "derived" / "run.db")
    record = frozen.provenance(tmp_path, source=db, derived=copy,
                               migration_version="structured_slot@1")
    assert record["source_snapshot_hash"]
    assert record["derived_db_hash"]
    assert record["migration_version"] == "structured_slot@1"
    assert record["runtime_fingerprint"]


def test_the_gate_writes_to_a_clone_and_not_to_the_source(tmp_path, monkeypatch):
    """The specific mistake, prevented at the place it happened."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import slot_gate

    db = _make_db(tmp_path / "run.db")
    monkeypatch.setattr(slot_gate, "_REPO", tmp_path)
    frozen.freeze(tmp_path, [db])
    before = frozen.digest(db)

    result = slot_gate.gate(db, apply=True)
    assert result["source_untouched"] is True
    assert frozen.digest(db) == before
    assert result["derived_db"] != str(db)
    assert result["derived_db_hash"] != result["source_snapshot_hash"] or \
        result["first_pass"]["filled"] == 0
