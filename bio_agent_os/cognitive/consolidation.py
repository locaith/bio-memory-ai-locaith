"""Merging related memories into one, which is what a night of sleep does.

Phase 3 measured the hippocampus label against retrieval and found **no
difference at all** — 148/194 either way, not one question changed. The label
describes a memory; ranking needs to know whether a memory answers *this*
question. Different quantities, and the embedding already does the second.

So the remaining explanation for `bio-memory` scoring 0.4123 against
`cognitive`'s 0.3658 is the thing `bio-memory` has and this layer does not:
consolidation. And the failures point the same way. Of the questions this layer
misses on real data, most are list-shaped —

    "kể tên 4 loại hallucination của LLM"
    "ba thành phần cơ bản của một robot AI là gì"

— where the four items live in four separate memories. Retrieval scores
memories one at a time, so each fragment competes alone and the top three
contain three quarters of an answer. Nothing is wrong with the ranking; the
answer was never in one place.

Consolidation puts it in one place. Cluster by embedding, ask a model to merge
each cluster into a single statement, store that as a new memory beside the
originals.

Rules, unchanged from the rest of this layer and for the same reasons:

* **Originals are never deleted.** A consolidated memory is a model's reading of
  several facts; if it is wrong, the facts it came from must still be there. It
  records `consolidated_from` so any claim can be traced back.
* **No model on the write path.** This runs offline, like the label upgrader.
* **Nothing here imports an engine.** One is passed in.
* **Off unless asked for.** A merged memory that turns out to mislead is worse
  than four fragments that merely rank poorly.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

from .semantic_index import cosine, load_vectors

CONSOLIDATED_MARKER = "consolidated_from"


@dataclass
class Cluster:
    memory_ids: list[str]
    contents: list[str]
    mean_similarity: float = 0.0


@dataclass
class ConsolidationMetrics:
    clusters_found: int = 0
    consolidated: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)


def _already_consolidated(conn: sqlite3.Connection) -> set[str]:
    """Memory ids that are already inside a consolidated memory.

    Without this, every pass re-merges the same cluster and the store fills with
    near-duplicates that all compete with one another — which would make
    retrieval worse, not better, while looking like progress.
    """
    used: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT metadata FROM cognitive_memories WHERE metadata LIKE ?",
            (f"%{CONSOLIDATED_MARKER}%",),
        ).fetchall()
    except sqlite3.OperationalError:
        return used
    for (blob,) in rows:
        try:
            meta = json.loads(blob) if isinstance(blob, str) else (blob or {})
        except (TypeError, ValueError):
            continue
        for memory_id in (meta or {}).get(CONSOLIDATED_MARKER, []) or []:
            used.add(str(memory_id))
    return used


def find_clusters(conn: sqlite3.Connection, *, threshold: float = 0.72,
                  min_size: int = 3, max_size: int = 8,
                  limit: int = 2000) -> list[Cluster]:
    """Group memories that say related things.

    Greedy single-pass clustering on the stored vectors: take an unused memory
    as a seed, pull in everything above `threshold`, cap the size, repeat. Not
    the best clustering available, and deliberately so — it is deterministic,
    needs no extra library, and costs one pass over vectors that already exist.

    `max_size` matters more than it looks. A cluster of thirty memories merges
    into a paragraph that is about everything and therefore ranks for nothing.
    """
    try:
        rows = conn.execute(
            "SELECT memory_id, content FROM cognitive_memories "
            "ORDER BY rowid LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    if not rows:
        return []

    contents = {memory_id: content for memory_id, content in rows}
    vectors = load_vectors(conn, list(contents))
    if not vectors:
        return []

    used = _already_consolidated(conn)
    ordered = [m for m in contents if m in vectors and m not in used]
    taken: set[str] = set()
    clusters: list[Cluster] = []

    for seed in ordered:
        if seed in taken:
            continue
        seed_vector = vectors[seed]
        members = [seed]
        scores: list[float] = []
        for other in ordered:
            if other == seed or other in taken:
                continue
            score = cosine(seed_vector, vectors[other])
            if score >= threshold:
                members.append(other)
                scores.append(score)
                if len(members) >= max_size:
                    break
        if len(members) >= min_size:
            taken.update(members)
            clusters.append(Cluster(
                memory_ids=members,
                contents=[contents[m] for m in members],
                mean_similarity=round(sum(scores) / len(scores), 4) if scores else 0.0,
            ))
    return clusters


def _source_events(conn: sqlite3.Connection, memory_ids: list[str]) -> list[str]:
    """Every event behind the memories in a cluster, de-duplicated."""
    if not memory_ids:
        return []
    placeholders = ",".join("?" * len(memory_ids))
    try:
        rows = conn.execute(
            f"SELECT source_event_ids FROM cognitive_memories "
            f"WHERE memory_id IN ({placeholders})", memory_ids
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    seen: list[str] = []
    for (blob,) in rows:
        try:
            ids = json.loads(blob) if isinstance(blob, str) else (blob or [])
        except (TypeError, ValueError):
            continue
        for event_id in ids or []:
            if event_id not in seen:
                seen.append(str(event_id))
    return seen


def build_prompt(cluster: Cluster) -> str:
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cluster.contents))
    return (
        "Dưới đây là những mẩu ký ức rời rạc về cùng một chủ đề.\n"
        "Gộp chúng thành MỘT đoạn duy nhất, đầy đủ, đứng riêng vẫn hiểu được.\n"
        "\n"
        "Bắt buộc:\n"
        "- Giữ nguyên mọi con số, tên riêng, ngày tháng.\n"
        "- Nếu các mẩu liệt kê nhiều mục, gộp thành MỘT danh sách đầy đủ.\n"
        "- KHÔNG thêm thông tin không có trong các mẩu trên.\n"
        "- Nếu hai mẩu mâu thuẫn nhau, giữ cả hai và nói rõ là chúng mâu thuẫn.\n"
        "- Trả về đúng đoạn văn, không mở đầu, không giải thích.\n"
        "\n"
        f"{numbered}"
    )


async def consolidate(conn: sqlite3.Connection, engine: Any,
                      clusters: Iterable[Cluster], *,
                      memory_store: Any, tenant_id: str, workspace_id: str,
                      timeout_seconds: float = 60.0,
                      max_clusters: int = 100) -> ConsolidationMetrics:
    """Merge each cluster into one memory. One model call per cluster.

    Outside every transaction, one commit per memory. The same rule the label
    upgrader keeps, for the same reason: a model call inside a write transaction
    is a write with no upper bound.
    """
    import asyncio

    from .models import CognitiveMemory, MemoryType

    metrics = ConsolidationMetrics()
    for index, cluster in enumerate(clusters):
        if index >= max_clusters:
            break
        metrics.clusters_found += 1
        try:
            merged = await asyncio.wait_for(
                engine.generate(build_prompt(cluster), temperature=0.1),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            metrics.failures.append(f"{type(exc).__name__}: {exc}"[:200])
            metrics.skipped += 1
            continue

        text = str(merged or "").strip()
        if len(text) < 40:
            # Shorter than the shortest fragment it was meant to merge. Keeping
            # it would replace four findable facts with one vague sentence.
            metrics.skipped += 1
            continue

        # Provenance is the union of the sources' events, not a fresh id. A
        # consolidated memory is a claim about several observations, and the
        # store requires it to say which — rightly, because a merged statement
        # nobody can trace back is exactly the kind of confident, unfalsifiable
        # memory this project keeps refusing to create.
        source_events = _source_events(conn, cluster.memory_ids)

        memory = CognitiveMemory(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            memory_type=MemoryType.SEMANTIC,
            content=text,
            source_event_ids=source_events,
            confidence=0.75,
            metadata={
                CONSOLIDATED_MARKER: cluster.memory_ids,
                "cluster_size": len(cluster.memory_ids),
                "mean_similarity": cluster.mean_similarity,
            },
        )
        memory_store.put(memory, commit=True)
        metrics.consolidated += 1

    return metrics


__all__ = [
    "CONSOLIDATED_MARKER",
    "Cluster",
    "ConsolidationMetrics",
    "build_prompt",
    "consolidate",
    "find_clusters",
]
