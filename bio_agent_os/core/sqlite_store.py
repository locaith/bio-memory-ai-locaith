"""
Shared persistence facade.

The name stays `SQLiteStore` for backward compatibility, but the implementation
can route to SQLite or PostgreSQL through adapter resolution.
"""

import json
from typing import Any, Iterable, Optional

from bio_agent_os.core.db_adapter import resolve_database_backend


class SQLiteStore:
    def __init__(
        self,
        storage_dir: str = "data",
        db_name: str = "bio_agent_os.db",
        backend: Optional[str] = None,
        database_url: Optional[str] = None,
    ):
        self.storage_dir = storage_dir
        self.db_name = db_name
        self.adapter = resolve_database_backend(
            storage_dir=storage_dir,
            db_name=db_name,
            backend=backend,
            database_url=database_url,
        )

    def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        self.adapter.execute(sql, parameters)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        self.adapter.executemany(sql, rows)

    def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        return self.adapter.fetchall(sql, parameters)

    def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[dict[str, Any]]:
        return self.adapter.fetchone(sql, parameters)

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
