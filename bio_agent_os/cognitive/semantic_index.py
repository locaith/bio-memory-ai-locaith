"""Semantic vectors for the transactional layer, computed off the write path.

`cognitive/` ranks by token overlap, and that is why it can neither reject an
irrelevant memory nor recognise a paraphrase. Measured against this project's
own domain corpus, three genuine query/memory pairs share **no tokens at all**:

    "Blender non destructive scale"  -> its own memory scores 0.000
    "child pickup reminder"          -> 0.000
    "experiment causal conclusion"   -> 0.000

Identical to nonsense. So no relevance threshold can work on that signal, and
both symptoms — confident wrong answers, and missed paraphrases — are the same
missing capability.

Three rules carried over from the hippocampus join, for the same reasons:

1. **Nothing here imports an embedder.** One is passed in, and anything with
   `.embed(text) -> list[float]` will do. `cognitive/` has to keep answering
   when the model host is down, so it cannot depend on one at import time. Note
   an embedder is *not* an LLM — milliseconds, deterministic, local if wanted —
   so this does not reopen the twenty-second problem that keeps the hippocampus
   off the write path.

2. **Never on the write path.** `observe()` costs what it costs today. Vectors
   are produced by `backfill_embeddings()`, discovered with a LEFT JOIN, in
   bounded batches — the same shape as `hippocampus_label.backfill_labels()`,
   and for the same reason: work that is recomputable from immutable data does
   not need to be remembered in a queue at the moment of writing.

3. **Degrade, never fail.** A database with no vectors retrieves exactly as it
   does today. A memory whose vector is missing is scored the old way rather
   than dropped. Turning this on must not be able to make anything disappear.
"""

from __future__ import annotations

import array
import hashlib
import math
import sqlite3
from typing import Any, Iterable, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id     TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    workspace_id  TEXT,
    model         TEXT NOT NULL,
    dims          INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    vector        BLOB NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_scope
    ON memory_embeddings(tenant_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model
    ON memory_embeddings(model, dims);
"""


class SupportsEmbed(Protocol):
    def embed(self, text: str) -> list[float]: ...


def content_hash(text: str) -> str:
    """So a memory edited after it was embedded is re-embedded rather than
    matched against a vector describing what it used to say."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def pack(vector: Iterable[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> array.array:
    out = array.array("f")
    out.frombytes(blob)
    return out


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    av, bv = list(a), list(b)
    if not av or not bv or len(av) != len(bv):
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    return dot / (na * nb) if na and nb else 0.0


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _describe(embedder: Any) -> str:
    for attr in ("model", "effective_backend"):
        value = getattr(embedder, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, str) and value:
            return value
    return "unknown"


def unembedded_count(conn: sqlite3.Connection) -> int:
    """How many memories still have no vector.

    A missing `memory_embeddings` table means **none of them do**, not zero —
    reporting 0 there would say "nothing to do" at the exact moment there is
    everything to do, which is how the first end-to-end run silently backfilled
    nothing.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM cognitive_memories m "
            "LEFT JOIN memory_embeddings e ON e.memory_id = m.memory_id "
            "WHERE e.memory_id IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        pass
    try:
        row = conn.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0                       # no memories either; absent, not broken


def backfill_embeddings(conn: sqlite3.Connection, embedder: SupportsEmbed, *,
                        limit: int = 200, model: str | None = None) -> int:
    """Embed memories that have no vector yet. Returns how many were written.

    Bounded and looped by the caller. An unbounded scan on a large database
    holds a read snapshot, and a held read snapshot is what ended canary runs
    8 and 9.
    """
    ensure_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT m.memory_id, m.tenant_id, m.workspace_id, m.content
            FROM cognitive_memories m
            LEFT JOIN memory_embeddings e ON e.memory_id = m.memory_id
            WHERE e.memory_id IS NULL
            ORDER BY m.rowid
            LIMIT ?
            """,
            (limit,),
        ).fetchall()        # fetchall releases the read transaction before the
                            # embedder is called and before anything is written
    except sqlite3.OperationalError:
        return 0

    if not rows:
        return 0

    # `effective_backend` is a property on this project's Embedder and a method
    # elsewhere. Calling a string is a TypeError that only appears once a real
    # embedder is passed, so `_describe` accepts both shapes.
    name = model or _describe(embedder)
    written = 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for memory_id, tenant_id, workspace_id, content in rows:
        text = str(content or "").strip()
        if not text:
            continue
        try:
            vector = embedder.embed(text)
        except Exception:
            # One bad memory must not stop the backfill; it stays unembedded
            # and the next pass picks it up.
            continue
        if not vector:
            continue
        conn.execute(
            "INSERT INTO memory_embeddings("
            "  memory_id, tenant_id, workspace_id, model, dims, content_hash,"
            "  vector, created_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(memory_id) DO UPDATE SET "
            "  vector=excluded.vector, dims=excluded.dims, model=excluded.model,"
            "  content_hash=excluded.content_hash, created_at=excluded.created_at "
            "WHERE memory_embeddings.content_hash <> excluded.content_hash",
            (memory_id, tenant_id, workspace_id, str(name), len(vector),
             content_hash(text), pack(vector), now),
        )
        written += 1

    conn.commit()
    return written


CALIBRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_calibration (
    model       TEXT PRIMARY KEY,
    dims        INTEGER NOT NULL,
    samples     INTEGER NOT NULL,
    mean        REAL NOT NULL,
    p95         REAL NOT NULL,
    p99         REAL NOT NULL,
    floor       REAL NOT NULL,
    measured_at TEXT NOT NULL
);
"""


def calibrate(conn: sqlite3.Connection, *, sample: int = 60,
              margin: float = 0.08) -> dict[str, Any] | None:
    """Measure what "unrelated" looks like for whichever embedder is in use.

    A fixed threshold cannot work across embedding models and the failure is
    silent. Measured on 2026-08-13:

        gemini-embedding-001      unrelated ~0.58, matching 0.67-0.75
        text-embedding-3-small    unrelated ~0.08, matching 0.57-0.69

    A floor of 0.64 is sensible for the first and destroys the second — it
    rejected a genuine match at 0.574 and took `cognitive` to 0.084 on LoCoMo,
    against 0.41 for the same content without a floor. The comment above
    `EMBEDDING_FLOOR` warned about exactly this, and the warning did not
    survive contact with a second model.

    So the floor is derived from the data instead of written down: sample
    random pairs of *different* memories, which are overwhelmingly unrelated,
    and put the floor a margin above the 99th percentile of that. Cheap — the
    vectors are already stored, no model call is needed.
    """
    conn.executescript(CALIBRATION_SCHEMA)
    try:
        rows = conn.execute(
            "SELECT memory_id, model, dims, vector FROM memory_embeddings "
            "ORDER BY RANDOM() LIMIT ?", (sample,)
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows) < 8:
        return None                # too few to say anything; leave the default

    model, dims = rows[0][1], rows[0][2]
    vectors = [unpack(r[3]) for r in rows]
    scores: list[float] = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            scores.append(cosine(vectors[i], vectors[j]))
    if not scores:
        return None

    scores.sort()
    def pct(q: float) -> float:
        return scores[min(len(scores) - 1, int(len(scores) * q))]

    p99 = pct(0.99)
    floor = round(min(0.95, p99 + margin), 4)
    from datetime import datetime, timezone
    result = {
        "model": model, "dims": dims, "samples": len(scores),
        "mean": round(sum(scores) / len(scores), 4),
        "p95": round(pct(0.95), 4), "p99": round(p99, 4), "floor": floor,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    conn.execute(
        "INSERT INTO embedding_calibration(model,dims,samples,mean,p95,p99,floor,measured_at)"
        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(model) DO UPDATE SET"
        " dims=excluded.dims, samples=excluded.samples, mean=excluded.mean,"
        " p95=excluded.p95, p99=excluded.p99, floor=excluded.floor,"
        " measured_at=excluded.measured_at",
        (result["model"], result["dims"], result["samples"], result["mean"],
         result["p95"], result["p99"], result["floor"], result["measured_at"]),
    )
    conn.commit()
    return result


def calibrated_floor(conn: sqlite3.Connection) -> float | None:
    """The measured floor for this store, or None if it has not been measured."""
    try:
        row = conn.execute(
            "SELECT floor FROM embedding_calibration ORDER BY measured_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return float(row[0]) if row else None


def load_vectors(conn: sqlite3.Connection, memory_ids: list[str]) -> dict[str, array.array]:
    """Vectors for the candidates under consideration, in one query.

    Returns whatever exists. A missing id is not an error — the caller scores
    that memory the old way, so switching this on cannot make anything vanish.
    """
    if not memory_ids:
        return {}
    out: dict[str, array.array] = {}
    chunk = 500                     # SQLite's parameter limit is not infinite
    for start in range(0, len(memory_ids), chunk):
        window = memory_ids[start:start + chunk]
        placeholders = ",".join("?" * len(window))
        try:
            rows = conn.execute(
                f"SELECT memory_id, vector FROM memory_embeddings "
                f"WHERE memory_id IN ({placeholders})",
                window,
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        for memory_id, blob in rows:
            out[memory_id] = unpack(blob)
    return out


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """How much of the store is embedded — the number that decides whether a
    relevance floor can be trusted yet."""
    try:
        total = conn.execute("SELECT COUNT(*) FROM cognitive_memories").fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        models = conn.execute(
            "SELECT model, dims, COUNT(*) FROM memory_embeddings GROUP BY model, dims"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"total": 0, "embedded": 0, "ratio": 0.0, "models": []}
    return {
        "total": total,
        "embedded": done,
        "ratio": round(done / total, 4) if total else 0.0,
        "models": [{"model": m, "dims": d, "count": c} for m, d, c in models],
    }


__all__ = [
    "SCHEMA",
    "SupportsEmbed",
    "backfill_embeddings",
    "calibrate",
    "calibrated_floor",
    "content_hash",
    "cosine",
    "coverage",
    "ensure_schema",
    "load_vectors",
    "pack",
    "unembedded_count",
    "unpack",
]
