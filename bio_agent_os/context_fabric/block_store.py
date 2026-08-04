from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bio_agent_os.cognitive.sqlite_utils import connect_sqlite
from typing import Iterable

from .models import ContextBlock, ContextBlockKind, StorageTier
from bio_agent_os.cognitive.models import SecurityLabel


def estimate_tokens(text: str) -> int:
    # Conservative multilingual approximation without tokenizer dependency.
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def canonical_hash(tenant_id: str, workspace_id: str | None, kind: ContextBlockKind, content: str) -> str:
    payload = json.dumps(
        {"tenant_id": tenant_id, "workspace_id": workspace_id, "kind": kind.value, "content": content.strip()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ContextBlockStore:
    """Content-addressed, tenant-isolated context block store.

    Blocks are deduplicated by a tenant/workspace-scoped SHA-256 digest. The
    store deliberately treats blocks as reconstructible cache artifacts rather
    than the canonical source of truth; provenance always points back to
    cognitive memories/events.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = connect_sqlite(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS context_blocks (
                block_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                token_count INTEGER NOT NULL,
                source_memory_ids_json TEXT NOT NULL,
                source_event_ids_json TEXT NOT NULL,
                trust_score REAL NOT NULL,
                relevance_score REAL NOT NULL,
                importance REAL NOT NULL,
                latency_sensitivity REAL NOT NULL,
                recomputation_cost REAL NOT NULL,
                expected_reuse REAL NOT NULL,
                tier TEXT NOT NULL,
                security_label TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT,
                access_count INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(tenant_id, workspace_id, content_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_context_scope
              ON context_blocks(tenant_id, workspace_id, tier, kind);
            CREATE INDEX IF NOT EXISTS idx_context_hash
              ON context_blocks(tenant_id, content_hash);
            CREATE TABLE IF NOT EXISTS context_shares (
                tenant_id TEXT NOT NULL,
                block_id TEXT NOT NULL,
                source_agent_id TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                permission TEXT NOT NULL DEFAULT 'read',
                created_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, block_id, target_agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_context_shares_target
              ON context_shares(tenant_id, target_agent_id, created_at);
            CREATE TABLE IF NOT EXISTS context_cache_entries (
                cache_key TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT,
                block_ids_json TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT
            );
            """
        )
        self.conn.commit()

    def put(self, block: ContextBlock) -> ContextBlock:
        block.content = block.content.strip()
        block.content_hash = block.content_hash or canonical_hash(block.tenant_id, block.workspace_id, block.kind, block.content)
        block.token_count = block.token_count or estimate_tokens(block.content)
        existing = self.conn.execute(
            """SELECT * FROM context_blocks
               WHERE tenant_id=? AND workspace_id IS ? AND content_hash=?""",
            (block.tenant_id, block.workspace_id, block.content_hash),
        ).fetchone()
        if existing:
            current = self._row(existing)
            merged_memories = sorted(set(current.source_memory_ids) | set(block.source_memory_ids))
            merged_events = sorted(set(current.source_event_ids) | set(block.source_event_ids))
            self.conn.execute(
                """UPDATE context_blocks SET
                   source_memory_ids_json=?, source_event_ids_json=?,
                   trust_score=max(trust_score,?), importance=max(importance,?),
                   expected_reuse=max(expected_reuse,?), access_count=access_count+1,
                   last_accessed_at=? WHERE block_id=?""",
                (
                    json.dumps(merged_memories), json.dumps(merged_events), block.trust_score,
                    block.importance, block.expected_reuse, self._now(), current.block_id,
                ),
            )
            self.conn.commit()
            return self.get(current.block_id, block.tenant_id) or current
        self.conn.execute(
            """INSERT INTO context_blocks(
                block_id,tenant_id,workspace_id,kind,content,content_hash,version,token_count,
                source_memory_ids_json,source_event_ids_json,trust_score,relevance_score,
                importance,latency_sensitivity,recomputation_cost,expected_reuse,tier,
                security_label,expires_at,created_at,last_accessed_at,access_count,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                block.block_id, block.tenant_id, block.workspace_id, block.kind.value,
                block.content, block.content_hash, block.version, block.token_count,
                json.dumps(block.source_memory_ids), json.dumps(block.source_event_ids),
                block.trust_score, block.relevance_score, block.importance,
                block.latency_sensitivity, block.recomputation_cost, block.expected_reuse,
                block.tier.value, block.security_label.value, block.expires_at,
                block.created_at, block.last_accessed_at, block.access_count,
                json.dumps(block.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        self.conn.commit()
        return block

    def get(self, block_id: str, tenant_id: str) -> ContextBlock | None:
        row = self.conn.execute(
            "SELECT * FROM context_blocks WHERE block_id=? AND tenant_id=?", (block_id, tenant_id)
        ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        tenant_id: str,
        workspace_id: str | None = None,
        tiers: Iterable[StorageTier] | None = None,
        limit: int = 100,
    ) -> list[ContextBlock]:
        query = "SELECT * FROM context_blocks WHERE tenant_id=?"
        args: list[object] = [tenant_id]
        if workspace_id is not None:
            query += " AND (workspace_id=? OR workspace_id IS NULL)"
            args.append(workspace_id)
        if tiers:
            tier_values = [tier.value for tier in tiers]
            query += " AND tier IN (%s)" % ",".join("?" for _ in tier_values)
            args.extend(tier_values)
        query += " ORDER BY expected_reuse DESC, importance DESC, access_count DESC LIMIT ?"
        args.append(limit)
        return [self._row(row) for row in self.conn.execute(query, args).fetchall()]


    def share_blocks(
        self,
        block_ids: list[str],
        *,
        tenant_id: str,
        source_agent_id: str,
        target_agent_id: str,
        permission: str = "read",
    ) -> list[str]:
        """Share reconstructible context blocks inside one tenant boundary."""
        shared: list[str] = []
        for block_id in block_ids:
            block = self.get(block_id, tenant_id)
            if block is None or block.tier == StorageTier.QUARANTINE:
                continue
            self.conn.execute(
                """INSERT INTO context_shares(
                   tenant_id,block_id,source_agent_id,target_agent_id,permission,created_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(tenant_id,block_id,target_agent_id) DO UPDATE SET
                   source_agent_id=excluded.source_agent_id,permission=excluded.permission,created_at=excluded.created_at""",
                (tenant_id, block_id, source_agent_id, target_agent_id, permission, self._now()),
            )
            shared.append(block_id)
        self.conn.commit()
        return shared

    def shared_with(self, tenant_id: str, target_agent_id: str, limit: int = 100) -> list[ContextBlock]:
        rows = self.conn.execute(
            """SELECT b.* FROM context_shares s JOIN context_blocks b ON b.block_id=s.block_id
               WHERE s.tenant_id=? AND s.target_agent_id=? AND b.tenant_id=?
               ORDER BY s.created_at DESC LIMIT ?""",
            (tenant_id, target_agent_id, tenant_id, limit),
        ).fetchall()
        return [self._row(row) for row in rows]

    def revoke_share(self, block_id: str, tenant_id: str, target_agent_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM context_shares WHERE block_id=? AND tenant_id=? AND target_agent_id=?",
            (block_id, tenant_id, target_agent_id),
        )
        self.conn.commit()
        return bool(cursor.rowcount)

    def promote(self, block_id: str, tenant_id: str, tier: StorageTier) -> ContextBlock:
        self.conn.execute(
            "UPDATE context_blocks SET tier=?, last_accessed_at=?, access_count=access_count+1 WHERE block_id=? AND tenant_id=?",
            (tier.value, self._now(), block_id, tenant_id),
        )
        self.conn.commit()
        block = self.get(block_id, tenant_id)
        if block is None:
            raise KeyError(block_id)
        return block

    def evict_expired(self, now: str | None = None) -> int:
        effective = now or self._now()
        cursor = self.conn.execute(
            "DELETE FROM context_blocks WHERE expires_at IS NOT NULL AND expires_at<=?", (effective,)
        )
        self.conn.commit()
        return int(cursor.rowcount)


    def invalidate_scope(self, tenant_id: str, workspace_id: str | None = None) -> int:
        """Invalidate compiled packets after canonical memory changes."""
        if workspace_id is None:
            cursor = self.conn.execute(
                "DELETE FROM context_cache_entries WHERE tenant_id=?", (tenant_id,)
            )
        else:
            cursor = self.conn.execute(
                "DELETE FROM context_cache_entries WHERE tenant_id=? AND (workspace_id=? OR workspace_id IS NULL)",
                (tenant_id, workspace_id),
            )
        self.conn.commit()
        return int(cursor.rowcount)

    def cache_packet(
        self,
        cache_key: str,
        tenant_id: str,
        workspace_id: str | None,
        block_ids: list[str],
        expires_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO context_cache_entries(
               cache_key,tenant_id,workspace_id,block_ids_json,expires_at,created_at
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET block_ids_json=excluded.block_ids_json,
               expires_at=excluded.expires_at, created_at=excluded.created_at""",
            (cache_key, tenant_id, workspace_id, json.dumps(block_ids), expires_at, self._now()),
        )
        self.conn.commit()

    def get_cached_packet(self, cache_key: str, tenant_id: str, now: str | None = None) -> list[ContextBlock] | None:
        effective = now or self._now()
        row = self.conn.execute(
            """SELECT * FROM context_cache_entries WHERE cache_key=? AND tenant_id=?
               AND (expires_at IS NULL OR expires_at>?)""",
            (cache_key, tenant_id, effective),
        ).fetchone()
        if not row:
            return None
        block_ids = json.loads(row["block_ids_json"])
        blocks: list[ContextBlock] = []
        for block_id in block_ids:
            block = self.get(block_id, tenant_id)
            if block:
                blocks.append(block)
        self.conn.execute(
            "UPDATE context_cache_entries SET access_count=access_count+1,last_accessed_at=? WHERE cache_key=?",
            (effective, cache_key),
        )
        self.conn.commit()
        return blocks

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row(row: sqlite3.Row) -> ContextBlock:
        return ContextBlock(
            block_id=row["block_id"], tenant_id=row["tenant_id"], workspace_id=row["workspace_id"],
            kind=ContextBlockKind(row["kind"]), content=row["content"], content_hash=row["content_hash"],
            version=int(row["version"]), token_count=int(row["token_count"]),
            source_memory_ids=json.loads(row["source_memory_ids_json"]),
            source_event_ids=json.loads(row["source_event_ids_json"]), trust_score=float(row["trust_score"]),
            relevance_score=float(row["relevance_score"]), importance=float(row["importance"]),
            latency_sensitivity=float(row["latency_sensitivity"]), recomputation_cost=float(row["recomputation_cost"]),
            expected_reuse=float(row["expected_reuse"]), tier=StorageTier(row["tier"]),
            security_label=SecurityLabel(row["security_label"]), expires_at=row["expires_at"],
            created_at=row["created_at"], last_accessed_at=row["last_accessed_at"],
            access_count=int(row["access_count"]), metadata=json.loads(row["metadata_json"]),
        )
