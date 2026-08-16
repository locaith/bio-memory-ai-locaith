"""A benchmark artefact used as "before" is read-only, and this enforces it.

On 17/08 a migration was run against four finished run databases with
`--apply`. The migration was correct and the numbers it produced were true.
The damage was elsewhere: `.staging/lifetime/run.db` had produced the
`before` arm of three published comparisons, and after the write it could no
longer reproduce its own numbers. The evidence for a claim was edited by the
work that claim was measuring.

Nothing caught it. The suite was green, the gate passed, and the report said
`"applied": true` in a field nobody was reading as a warning.

So:

    frozen source  ->  clone  ->  transform the clone  ->  measure

and a comparison whose source hash has moved refuses to run rather than
quietly comparing against something else.

Two hashes, because they answer different questions:

    source_snapshot_hash   is the "before" still the "before"
    derived_db_hash        which exact artefact produced these numbers

The first is the gate. The second is what makes a number in a report
traceable to a file a year later.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Where the hashes of frozen sources live. Deliberately a file in the repo
#: rather than a table inside one of the databases: a manifest stored in the
#: thing it describes cannot detect the thing being replaced.
MANIFEST = "benchmark_reports/frozen_sources.json"


class FrozenSourceChanged(RuntimeError):
    """A benchmark source used as `before` is not what it was."""


@dataclass
class Snapshot:
    path: str
    sha256: str
    bytes: int
    rows: int = 0
    frozen_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256,
                "bytes": self.bytes, "rows": self.rows,
                "frozen_at": self.frozen_at}


def digest(path: Path) -> str:
    """Content hash of a database file.

    The file itself, not a query over it: a WAL checkpoint or a VACUUM changes
    bytes without changing meaning, and a hash that ignores those would also
    ignore a row being rewritten in place.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _row_count(path: Path) -> int:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def snapshot(path: Path) -> Snapshot:
    path = Path(path)
    return Snapshot(path=str(path), sha256=digest(path),
                    bytes=path.stat().st_size, rows=_row_count(path))


def load_manifest(repo: Path) -> dict[str, dict]:
    manifest = repo / MANIFEST
    if not manifest.exists():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def freeze(repo: Path, paths: list[Path]) -> dict[str, dict]:
    """Record what these artefacts are now, so a later change is visible."""
    manifest = load_manifest(repo)
    for path in paths:
        path = Path(path)
        if path.exists():
            manifest[str(path.relative_to(repo)) if path.is_relative_to(repo)
                     else str(path)] = snapshot(path).as_dict()
    target = repo / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return manifest


def verify(repo: Path, path: Path) -> None:
    """Refuse to proceed if this frozen source has moved.

    Raises rather than warns. A warning about evidence integrity is a warning
    that gets read after the report is written.
    """
    manifest = load_manifest(repo)
    key = (str(Path(path).relative_to(repo))
           if Path(path).is_relative_to(repo) else str(path))
    recorded = manifest.get(key)
    if recorded is None:
        return                      # never frozen; nothing is being claimed
    current = digest(Path(path))
    if current != recorded["sha256"]:
        raise FrozenSourceChanged(
            f"{key} đã thay đổi kể từ khi được đóng băng.\n"
            f"  đã ghi : {recorded['sha256'][:16]}  "
            f"({recorded['rows']} hàng, {recorded['bytes']} byte)\n"
            f"  hiện tại: {current[:16]}\n"
            f"  Một artefact dùng làm 'before' mà bị sửa thì so sánh "
            f"before/after không còn nghĩa gì. Hãy clone rồi biến đổi bản "
            f"clone.")


def clone(source: Path, destination: Path) -> Path:
    """Copy a frozen source so a migration can run on the copy.

    The whole point of this module in one function: `shutil.copy2` was always
    available and was not used, because nothing said it had to be.
    """
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    for suffix in ("-wal", "-shm"):
        side = Path(str(source) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(destination) + suffix))
    return destination


def provenance(repo: Path, *, source: Path | None = None,
               derived: Path | None = None,
               migration_version: str = "") -> dict[str, Any]:
    """The four fields every comparison report should carry."""
    from bio_agent_os.core.provenance import identity

    return {
        "source_snapshot_hash": digest(Path(source)) if source and Path(source).exists() else None,
        "derived_db_hash": digest(Path(derived)) if derived and Path(derived).exists() else None,
        "migration_version": migration_version,
        "runtime_fingerprint": identity().fingerprint,
    }


__all__ = ["FrozenSourceChanged", "MANIFEST", "Snapshot", "clone", "digest",
           "freeze", "load_manifest", "provenance", "snapshot", "verify"]
