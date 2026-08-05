from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .sqlite_utils import connect_sqlite

from .models import (
    BeliefState,
    CognitiveMemory,
    EpistemicStatus,
    MemoryType,
    Modality,
    SecurityLabel,
    TrustTier,
    VerificationStatus,
)


class SQLiteMemoryStore:
    """Versioned bitemporal projection store.

    Memories are never overwritten. ``supersede`` closes the old version and
    inserts a new row, preserving full lineage. Schema migration is additive,
    making this module safe to merge into the existing Bio-Agent OS database.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = connect_sqlite(self.path)
        self.conn.row_factory = sqlite3.Row
        self.fts_available = False
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS cognitive_memories (
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_json TEXT NOT NULL,
                source_event_ids_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                salience REAL NOT NULL,
                utility REAL NOT NULL,
                trust_tier INTEGER NOT NULL,
                security_label TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                observed_at TEXT NOT NULL,
                superseded_at TEXT,
                reinforcement_count INTEGER NOT NULL,
                contradiction_count INTEGER NOT NULL,
                retrieval_count INTEGER NOT NULL,
                last_accessed_at TEXT,
                lifecycle_state TEXT NOT NULL,
                governed_exception_for TEXT,
                approved_by TEXT,
                approval_expires_at TEXT,
                allowed_agents_json TEXT NOT NULL,
                allowed_roles_json TEXT NOT NULL,
                purpose_allowlist_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                epistemic_status TEXT NOT NULL DEFAULT 'observed',
                verification_status TEXT NOT NULL DEFAULT 'unverified',
                counterevidence_event_ids_json TEXT NOT NULL DEFAULT '[]',
                applicable_context_json TEXT NOT NULL DEFAULT '{}',
                modality TEXT NOT NULL DEFAULT 'text',
                simulation_id TEXT,
                reversible_forget_at TEXT,
                PRIMARY KEY(memory_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_scope
            ON cognitive_memories(tenant_id, workspace_id, memory_type, lifecycle_state);
            CREATE INDEX IF NOT EXISTS idx_memory_temporal
            ON cognitive_memories(tenant_id, valid_from, valid_to, superseded_at);
            CREATE INDEX IF NOT EXISTS idx_memory_epistemic
            ON cognitive_memories(tenant_id, epistemic_status, verification_status);

            -- Which events a memory was built from, as rows rather than as a
            -- JSON array inside a column.
            --
            -- "Does a projection exist for this event" used to be asked as
            --     source_event_ids_json LIKE '%' || event_id || '%'
            -- which cannot use an index and so scanned every memory, once per
            -- event asked about. The doctor did it inside a correlated
            -- subquery and became O(N*M); the shadow comparator did it in a
            -- loop and spent 62.5s on 10,000 events.
            --
            -- ON DELETE CASCADE keeps this honest when a memory row goes away:
            -- a link row that outlived its memory would answer "yes, projected"
            -- for a projection that no longer exists.
            CREATE TABLE IF NOT EXISTS memory_source_events (
                event_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                tenant_id TEXT NOT NULL,
                PRIMARY KEY(event_id, memory_id, version),
                FOREIGN KEY(memory_id, version)
                    REFERENCES cognitive_memories(memory_id, version)
                    ON DELETE CASCADE
            );
            -- Exactly one secondary index, and only because the join back to
            -- cognitive_memories and ON DELETE CASCADE both need it. The
            -- primary key already leads with event_id, which is the lookup
            -- every caller does, and an index nobody queries is pure write
            -- cost: a (tenant_id, event_id) index here made the 50,000-event
            -- build 51% slower and was never used by a single query.
            CREATE INDEX IF NOT EXISTS idx_memory_source_memory
            ON memory_source_events(memory_id, version);
            DROP INDEX IF EXISTS idx_memory_source_event;
            DROP INDEX IF EXISTS idx_memory_source_tenant;

            -- Records that the one-off backfill has run, so opening a store
            -- does not re-scan the whole table to find out.
            CREATE TABLE IF NOT EXISTS memory_store_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                high_water INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._backfill_source_events()
        self._ensure_column("epistemic_status", "TEXT NOT NULL DEFAULT 'observed'")
        self._ensure_column("verification_status", "TEXT NOT NULL DEFAULT 'unverified'")
        self._ensure_column("counterevidence_event_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        self._ensure_column("applicable_context_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("modality", "TEXT NOT NULL DEFAULT 'text'")
        self._ensure_column("simulation_id", "TEXT")
        self._ensure_column("reversible_forget_at", "TEXT")
        self.conn.commit()
        self._init_fts()

    def _init_fts(self) -> None:
        try:
            self.conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS cognitive_memory_fts USING fts5(
                    memory_key UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, content, tokenize='unicode61'
                )"""
            )
            # An FTS row must not outlive its memory. A second entry under one
            # key makes SQLite report "malformed inverted index for FTS5
            # table" — corruption, from an ordinary rebuild.
            #
            # The trigger puts that cost on the delete path, which production
            # does not have, instead of on every write. Deleting from FTS5 by
            # an UNINDEXED column scans the whole index, and doing it before
            # every insert made a 10,000-event build four times slower for a
            # case that needs a manual DELETE to reach.
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS cognitive_memories_fts_delete "
                "AFTER DELETE ON cognitive_memories BEGIN "
                "  DELETE FROM cognitive_memory_fts "
                "  WHERE memory_key = old.memory_id || ':' || old.version; "
                "END"
            )
            count = self.conn.execute("SELECT count(*) FROM cognitive_memory_fts").fetchone()[0]
            if count == 0:
                rows = self.conn.execute(
                    "SELECT memory_id,version,tenant_id,workspace_id,content FROM cognitive_memories"
                ).fetchall()
                self.conn.executemany(
                    "INSERT INTO cognitive_memory_fts(memory_key,tenant_id,workspace_id,content) VALUES(?,?,?,?)",
                    [(f"{r['memory_id']}:{r['version']}", r['tenant_id'], r['workspace_id'] or '', r['content']) for r in rows],
                )
            self.conn.commit()
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False

    def _ensure_column(self, name: str, declaration: str) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(cognitive_memories)")}
        if name not in columns:
            self.conn.execute(f"ALTER TABLE cognitive_memories ADD COLUMN {name} {declaration}")

    _BACKFILL = "memory_source_events_backfill"

    def _backfill_source_events(self) -> None:
        """Populate the link table for memories written before it existed.

        One pass over `cognitive_memories`, once, and never again: the marker
        row records the highest rowid it covered, and `MAX(rowid)` is O(1) on
        a rowid table. The first version of this asked
        `COUNT(DISTINCT memory_id || ':' || version)` on every open, which is
        a full scan of the link table at startup — a cost that grows with the
        database, on the path every process takes.
        """
        try:
            high_water = self.conn.execute(
                "SELECT MAX(rowid) FROM cognitive_memories"
            ).fetchone()[0] or 0
            marker = self.conn.execute(
                "SELECT high_water FROM memory_store_migrations WHERE name=?",
                (self._BACKFILL,),
            ).fetchone()
        except sqlite3.OperationalError:  # pragma: no cover - fresh database
            return

        covered = int(marker["high_water"]) if marker else 0
        if marker is not None and high_water <= covered:
            return

        rows = []
        for row in self.conn.execute(
            "SELECT memory_id, version, tenant_id, source_event_ids_json"
            " FROM cognitive_memories WHERE rowid > ?", (covered,),
        ).fetchall():
            try:
                event_ids = json.loads(row["source_event_ids_json"] or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(event_ids, list):
                event_ids = [event_ids]
            for event_id in event_ids:
                if event_id:
                    rows.append((str(event_id), row["memory_id"], row["version"],
                                 row["tenant_id"]))
        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO memory_source_events("
                " event_id, memory_id, version, tenant_id) VALUES(?,?,?,?)",
                rows,
            )
        self.conn.execute(
            "INSERT INTO memory_store_migrations(name, applied_at, high_water)"
            " VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET"
            "  applied_at=excluded.applied_at, high_water=excluded.high_water",
            (self._BACKFILL, datetime.now(timezone.utc).isoformat(), high_water),
        )
        self.conn.commit()

    def _link_source_events(self, memory: CognitiveMemory) -> None:
        """Write the link rows inside whatever transaction the caller holds.

        Never commits: `put()` decides that, and the projection worker needs
        the memory, its links and the ledger row to become durable together.
        """
        rows = [
            (str(event_id), memory.memory_id, memory.version, memory.tenant_id)
            for event_id in (memory.source_event_ids or [])
            if event_id
        ]
        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO memory_source_events("
                " event_id, memory_id, version, tenant_id) VALUES(?,?,?,?)",
                rows,
            )

    def put(self, memory: CognitiveMemory, *, commit: bool = True) -> CognitiveMemory:
        """Store a memory version.

        `commit=False` leaves the row in the caller's open transaction. The
        projection worker needs that: the target-local ledger row and the
        projection it records have to become durable together, and a store
        that always committed on its own forced a second transaction to
        record what the first one had produced.

        The commit used to live inside the FTS branch below, which meant a
        build on a SQLite without FTS5 left the projection uncommitted and
        durable only by whatever the caller happened to do next. Durability
        must not depend on how the engine was compiled.
        """
        self.conn.execute(
            """
            INSERT INTO cognitive_memories(
                memory_id,version,tenant_id,workspace_id,memory_type,content,
                structured_json,source_event_ids_json,confidence,importance,
                salience,utility,trust_tier,security_label,valid_from,valid_to,
                observed_at,superseded_at,reinforcement_count,contradiction_count,
                retrieval_count,last_accessed_at,lifecycle_state,governed_exception_for,
                approved_by,approval_expires_at,allowed_agents_json,allowed_roles_json,
                purpose_allowlist_json,created_at,metadata_json,epistemic_status,
                verification_status,counterevidence_event_ids_json,applicable_context_json,
                modality,simulation_id,reversible_forget_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                memory.memory_id, memory.version, memory.tenant_id, memory.workspace_id,
                memory.memory_type.value, memory.content,
                json.dumps(memory.structured_content, ensure_ascii=False, sort_keys=True),
                json.dumps(memory.source_event_ids), memory.confidence, memory.importance,
                memory.salience, memory.utility, int(memory.trust_tier),
                memory.security_label.value, memory.valid_from, memory.valid_to,
                memory.observed_at, memory.superseded_at, memory.reinforcement_count,
                memory.contradiction_count, memory.retrieval_count, memory.last_accessed_at,
                memory.lifecycle_state.value, memory.governed_exception_for,
                memory.approved_by, memory.approval_expires_at,
                json.dumps(memory.allowed_agents), json.dumps(memory.allowed_roles),
                json.dumps(memory.purpose_allowlist), memory.created_at,
                json.dumps(memory.metadata, ensure_ascii=False, sort_keys=True),
                memory.epistemic_status.value, memory.verification_status.value,
                json.dumps(memory.counterevidence_event_ids),
                json.dumps(memory.applicable_context, ensure_ascii=False, sort_keys=True),
                memory.modality.value, memory.simulation_id, memory.reversible_forget_at,
            ),
        )
        self._link_source_events(memory)
        if self.fts_available:
            try:
                self.conn.execute(
                    "INSERT INTO cognitive_memory_fts(memory_key,tenant_id,workspace_id,content) VALUES(?,?,?,?)",
                    (f"{memory.memory_id}:{memory.version}", memory.tenant_id, memory.workspace_id or "", memory.content),
                )
            except sqlite3.OperationalError:
                self.fts_available = False
        if commit:
            self.conn.commit()
        return memory


    def put_many(self, memories: list[CognitiveMemory]) -> list[CognitiveMemory]:
        """Insert projections and FTS entries in one transaction."""
        rows = []
        fts_rows = []
        for memory in memories:
            rows.append((
                memory.memory_id, memory.version, memory.tenant_id, memory.workspace_id,
                memory.memory_type.value, memory.content,
                json.dumps(memory.structured_content, ensure_ascii=False, sort_keys=True),
                json.dumps(memory.source_event_ids), memory.confidence, memory.importance,
                memory.salience, memory.utility, int(memory.trust_tier),
                memory.security_label.value, memory.valid_from, memory.valid_to,
                memory.observed_at, memory.superseded_at, memory.reinforcement_count,
                memory.contradiction_count, memory.retrieval_count, memory.last_accessed_at,
                memory.lifecycle_state.value, memory.governed_exception_for,
                memory.approved_by, memory.approval_expires_at,
                json.dumps(memory.allowed_agents), json.dumps(memory.allowed_roles),
                json.dumps(memory.purpose_allowlist), memory.created_at,
                json.dumps(memory.metadata, ensure_ascii=False, sort_keys=True),
                memory.epistemic_status.value, memory.verification_status.value,
                json.dumps(memory.counterevidence_event_ids),
                json.dumps(memory.applicable_context, ensure_ascii=False, sort_keys=True),
                memory.modality.value, memory.simulation_id, memory.reversible_forget_at,
            ))
            fts_rows.append((f"{memory.memory_id}:{memory.version}", memory.tenant_id, memory.workspace_id or "", memory.content))
        self.conn.executemany(
            """INSERT INTO cognitive_memories(
                memory_id,version,tenant_id,workspace_id,memory_type,content,
                structured_json,source_event_ids_json,confidence,importance,
                salience,utility,trust_tier,security_label,valid_from,valid_to,
                observed_at,superseded_at,reinforcement_count,contradiction_count,
                retrieval_count,last_accessed_at,lifecycle_state,governed_exception_for,
                approved_by,approval_expires_at,allowed_agents_json,allowed_roles_json,
                purpose_allowlist_json,created_at,metadata_json,epistemic_status,
                verification_status,counterevidence_event_ids_json,applicable_context_json,
                modality,simulation_id,reversible_forget_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        for memory in memories:
            self._link_source_events(memory)
        if self.fts_available:
            try:
                self.conn.executemany(
                    "INSERT INTO cognitive_memory_fts(memory_key,tenant_id,workspace_id,content) VALUES(?,?,?,?)",
                    fts_rows,
                )
            except sqlite3.OperationalError:
                self.fts_available = False
        self.conn.commit()
        return memories

    def latest(self, memory_id: str, tenant_id: str) -> CognitiveMemory | None:
        row = self.conn.execute(
            """SELECT * FROM cognitive_memories
               WHERE memory_id=? AND tenant_id=? ORDER BY version DESC LIMIT 1""",
            (memory_id, tenant_id),
        ).fetchone()
        return self._row(row) if row else None

    def supersede(self, memory_id: str, tenant_id: str, **changes) -> CognitiveMemory:
        current = self.latest(memory_id, tenant_id)
        if current is None:
            raise KeyError(memory_id)
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE cognitive_memories SET superseded_at=? WHERE memory_id=? AND version=?",
            (now, memory_id, current.version),
        )
        data = current.__dict__.copy()
        data.update(changes)
        data["version"] = current.version + 1
        data["superseded_at"] = None
        data["observed_at"] = changes.get("observed_at", now)
        successor = CognitiveMemory(**data)
        self.put(successor)
        return successor

    def active(self, tenant_id: str, workspace_id: str | None = None, as_of: str | None = None) -> list[CognitiveMemory]:
        query = "SELECT * FROM cognitive_memories WHERE tenant_id=? AND superseded_at IS NULL"
        args: list[object] = [tenant_id]
        if workspace_id is not None:
            query += " AND (workspace_id=? OR workspace_id IS NULL)"
            args.append(workspace_id)
        rows = self.conn.execute(query, args).fetchall()
        result = [self._row(row) for row in rows]
        if as_of:
            result = [m for m in result if self._valid_at(m, as_of)]
        return result


    def candidate_pool(
        self,
        tenant_id: str,
        query_text: str,
        workspace_id: str | None = None,
        as_of: str | None = None,
        limit: int = 250,
    ) -> list[CognitiveMemory]:
        """Retrieve an indexed candidate set before cognitive reranking.

        FTS is only a fast first stage. Governance-critical memories and recent
        active memories are always unioned so lexical mismatch cannot hide a
        policy, exception or prospective obligation.
        """
        selected: dict[tuple[str, int], CognitiveMemory] = {}
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "what", "which", "who", "where",
            "when", "why", "how", "does", "do", "did", "can", "could", "should", "would",
            "we", "i", "you", "it", "to", "for", "of", "in", "on", "and", "or", "use", "uses",
            "là", "gì", "có", "được", "không", "như", "thế", "nào", "cho", "của", "và", "tôi", "anh",
        }
        lowered_query = query_text.lower()
        identifiers = re.findall(r"\b[\w]+(?:-[\w]+)+\b", lowered_query, flags=re.UNICODE)
        identifier_parts = {part for identifier in identifiers for part in identifier.split("-")}
        tokens = [
            t for t in re.findall(r"\w+", lowered_query, flags=re.UNICODE)
            if (len(t) > 1 or t.isdigit()) and t not in stopwords and t not in identifier_parts
        ]
        phrase_expressions = [f'"{" ".join(identifier.split("-"))}"' for identifier in identifiers[:4]]
        token_expressions = [f'"{t}"' for t in tokens[:8]]
        expressions = phrase_expressions + token_expressions
        if self.fts_available and expressions:
            # Hyphenated identifiers such as unit-47 become exact FTS phrases
            # ("unit 47"), preventing a numeric token from matching protocol-47
            # or owner-team-47 elsewhere in the corpus.
            matches = [
                " AND ".join(expressions),
                " OR ".join(expressions),
            ]
            try:
                rows = []
                for match in matches:
                    rows = self.conn.execute(
                        """SELECT m.* FROM cognitive_memory_fts f
                           JOIN cognitive_memories m
                             ON f.memory_key=(m.memory_id || ':' || m.version)
                           WHERE cognitive_memory_fts MATCH ? AND f.tenant_id=?
                             AND m.superseded_at IS NULL
                             AND (? IS NULL OR m.workspace_id=? OR m.workspace_id IS NULL)
                           ORDER BY bm25(cognitive_memory_fts) LIMIT ?""",
                        (match, tenant_id, workspace_id, workspace_id, limit),
                    ).fetchall()
                    if rows:
                        break
                for row in rows:
                    memory = self._row(row)
                    selected[(memory.memory_id, memory.version)] = memory
            except sqlite3.OperationalError:
                self.fts_available = False

        critical_rows = self.conn.execute(
            """SELECT * FROM cognitive_memories WHERE tenant_id=? AND superseded_at IS NULL
               AND memory_type IN ('policy','exception','identity','prospective','self_model')
               AND (? IS NULL OR workspace_id=? OR workspace_id IS NULL)
               ORDER BY importance DESC, confidence DESC LIMIT 100""",
            (tenant_id, workspace_id, workspace_id),
        ).fetchall()
        for row in critical_rows:
            memory = self._row(row)
            selected[(memory.memory_id, memory.version)] = memory

        # Cold-start and semantically indirect queries still receive a bounded
        # recency/importance sample rather than a full-table scan.
        if len(selected) < min(40, limit):
            recent_rows = self.conn.execute(
                """SELECT * FROM cognitive_memories WHERE tenant_id=? AND superseded_at IS NULL
                   AND (? IS NULL OR workspace_id=? OR workspace_id IS NULL)
                   ORDER BY importance DESC, observed_at DESC LIMIT ?""",
                (tenant_id, workspace_id, workspace_id, min(limit, 120)),
            ).fetchall()
            for row in recent_rows:
                memory = self._row(row)
                selected[(memory.memory_id, memory.version)] = memory

        memories = list(selected.values())
        if as_of:
            memories = [m for m in memories if self._valid_at(m, as_of)]
        return memories[:limit]

    def history(self, memory_id: str, tenant_id: str) -> list[CognitiveMemory]:
        rows = self.conn.execute(
            "SELECT * FROM cognitive_memories WHERE memory_id=? AND tenant_id=? ORDER BY version",
            (memory_id, tenant_id),
        ).fetchall()
        return [self._row(row) for row in rows]

    def mark_retrieved(self, memory: CognitiveMemory) -> None:
        self.conn.execute(
            """UPDATE cognitive_memories SET retrieval_count=retrieval_count+1,
               last_accessed_at=? WHERE memory_id=? AND version=?""",
            (datetime.now(timezone.utc).isoformat(), memory.memory_id, memory.version),
        )
        self.conn.commit()

    def mark_retrieved_many(self, memories: list[CognitiveMemory]) -> None:
        """Record access telemetry in one transaction instead of one commit per result."""
        if not memories:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """UPDATE cognitive_memories SET retrieval_count=retrieval_count+1,
               last_accessed_at=? WHERE memory_id=? AND version=?""",
            [(now, memory.memory_id, memory.version) for memory in memories],
        )
        self.conn.commit()

    def reversible_forget(self, memory_id: str, tenant_id: str, restore_until: str) -> CognitiveMemory:
        return self.supersede(
            memory_id,
            tenant_id,
            lifecycle_state=BeliefState.ARCHIVED,
            reversible_forget_at=restore_until,
        )

    def restore(self, memory_id: str, tenant_id: str) -> CognitiveMemory:
        current = self.latest(memory_id, tenant_id)
        if current is None:
            raise KeyError(memory_id)
        if current.lifecycle_state != BeliefState.ARCHIVED:
            return current
        return self.supersede(
            memory_id,
            tenant_id,
            lifecycle_state=BeliefState.REINFORCED,
            reversible_forget_at=None,
        )

    @staticmethod
    def _valid_at(memory: CognitiveMemory, as_of: str) -> bool:
        return (memory.valid_from is None or memory.valid_from <= as_of) and (
            memory.valid_to is None or as_of < memory.valid_to
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> CognitiveMemory:
        return CognitiveMemory(
            memory_id=row["memory_id"], version=row["version"], tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"], memory_type=MemoryType(row["memory_type"]),
            content=row["content"], structured_content=json.loads(row["structured_json"]),
            source_event_ids=json.loads(row["source_event_ids_json"]),
            confidence=row["confidence"], importance=row["importance"], salience=row["salience"],
            utility=row["utility"], trust_tier=TrustTier(row["trust_tier"]),
            security_label=SecurityLabel(row["security_label"]), valid_from=row["valid_from"],
            valid_to=row["valid_to"], observed_at=row["observed_at"],
            superseded_at=row["superseded_at"], reinforcement_count=row["reinforcement_count"],
            contradiction_count=row["contradiction_count"], retrieval_count=row["retrieval_count"],
            last_accessed_at=row["last_accessed_at"], lifecycle_state=BeliefState(row["lifecycle_state"]),
            governed_exception_for=row["governed_exception_for"], approved_by=row["approved_by"],
            approval_expires_at=row["approval_expires_at"],
            allowed_agents=json.loads(row["allowed_agents_json"]),
            allowed_roles=json.loads(row["allowed_roles_json"]),
            purpose_allowlist=json.loads(row["purpose_allowlist_json"]),
            created_at=row["created_at"], metadata=json.loads(row["metadata_json"]),
            epistemic_status=EpistemicStatus(row["epistemic_status"]),
            verification_status=VerificationStatus(row["verification_status"]),
            counterevidence_event_ids=json.loads(row["counterevidence_event_ids_json"]),
            applicable_context=json.loads(row["applicable_context_json"]),
            modality=Modality(row["modality"]), simulation_id=row["simulation_id"],
            reversible_forget_at=row["reversible_forget_at"],
        )
