"""
Async SQLite path for FastAPI and concurrent workflows.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional


class AsyncSQLiteStore:
    def __init__(self, storage_dir: str = "data", db_name: str = "bio_agent_os.db"):
        self.storage_dir = storage_dir
        self.db_path = os.path.join(storage_dir, db_name)
        os.makedirs(storage_dir, exist_ok=True)
        self._aiosqlite = None

    async def _module(self):
        if self._aiosqlite is None:
            try:
                import aiosqlite  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "Async SQLite path requested but aiosqlite is not installed. "
                    "Install with `pip install bio-agent-os[async-sqlite]`."
                ) from exc
            import aiosqlite

            self._aiosqlite = aiosqlite
        return self._aiosqlite

    async def connect(self):
        aiosqlite = await self._module()
        connection = await aiosqlite.connect(self.db_path, timeout=30)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL;")
        await connection.execute("PRAGMA synchronous=NORMAL;")
        return connection

    async def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        async with await self.connect() as connection:
            await connection.execute(sql, tuple(parameters or []))
            await connection.commit()

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        async with await self.connect() as connection:
            await connection.executemany(sql, list(rows))
            await connection.commit()

    async def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        async with await self.connect() as connection:
            async with connection.execute(sql, tuple(parameters or [])) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[dict[str, Any]]:
        async with await self.connect() as connection:
            async with connection.execute(sql, tuple(parameters or [])) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    @staticmethod
    def dumps_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def loads_json(value: Optional[str], default: Any):
        if value in (None, ""):
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    @staticmethod
    def sanitize_identifier(value: str) -> str:
        sanitized = "".join(char if char.isalnum() else "_" for char in value.strip().lower())
        sanitized = sanitized.strip("_")
        return sanitized or "default"
