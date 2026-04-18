"""
Database adapters for local and multi-node persistence.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any, Iterable, Optional


def _translate_qmark_placeholders(sql: str) -> str:
    count = sql.count("?")
    if count == 0:
        return sql
    translated = sql
    for _ in range(count):
        translated = translated.replace("?", "%s", 1)
    return translated


def _translate_insert_or_replace(sql: str) -> str:
    pattern = re.compile(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+([^\s(]+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(sql)
    if not match:
        return sql
    table_name = match.group(1).strip()
    columns = [part.strip() for part in match.group(2).split(",")]
    values = match.group(3).strip()
    if not columns:
        return sql
    primary_column = columns[0]
    updates = ", ".join(f"{column}=EXCLUDED.{column}" for column in columns[1:])
    if not updates:
        updates = f"{primary_column}=EXCLUDED.{primary_column}"
    return (
        f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values}) "
        f"ON CONFLICT ({primary_column}) DO UPDATE SET {updates}"
    )


class DBAdapter(ABC):
    @abstractmethod
    def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        raise NotImplementedError

    @abstractmethod
    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        raise NotImplementedError

    @abstractmethod
    def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[dict[str, Any]]:
        raise NotImplementedError


class SQLiteAdapter(DBAdapter):
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        if self.db_path not in self._locks:
            self._locks[self.db_path] = threading.Lock()
        self._lock = self._locks[self.db_path]

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        return connection

    def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        with self._lock:
            with self.connect() as connection:
                connection.execute(sql, tuple(parameters or []))
                connection.commit()

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        with self._lock:
            with self.connect() as connection:
                connection.executemany(sql, rows)
                connection.commit()

    def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        with self._lock:
            with self.connect() as connection:
                cursor = connection.execute(sql, tuple(parameters or []))
                return [dict(row) for row in cursor.fetchall()]

    def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            with self.connect() as connection:
                cursor = connection.execute(sql, tuple(parameters or []))
                row = cursor.fetchone()
                return dict(row) if row else None


class PostgresAdapter(DBAdapter):
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, dsn: str):
        self.dsn = dsn
        if self.dsn not in self._locks:
            self._locks[self.dsn] = threading.Lock()
        self._lock = self._locks[self.dsn]
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL backend requested but psycopg is not installed. "
                "Install with `pip install bio-agent-os[postgres]`."
            ) from exc

    def _connect(self):
        from psycopg import connect
        from psycopg.rows import dict_row

        connection = connect(self.dsn, row_factory=dict_row)
        connection.autocommit = False
        return connection

    def _translate_sql(self, sql: str) -> str:
        sql = _translate_insert_or_replace(sql)
        sql = _translate_qmark_placeholders(sql)
        return sql

    def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        sql = self._translate_sql(sql)
        with self._lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, tuple(parameters or []))
                connection.commit()

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        sql = self._translate_sql(sql)
        with self._lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(sql, list(rows))
                connection.commit()

    def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        sql = self._translate_sql(sql)
        with self._lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, tuple(parameters or []))
                    return list(cursor.fetchall())

    def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[dict[str, Any]]:
        sql = self._translate_sql(sql)
        with self._lock:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, tuple(parameters or []))
                    return cursor.fetchone()


def resolve_database_backend(
    storage_dir: str,
    db_name: str = "bio_agent_os.db",
    backend: Optional[str] = None,
    database_url: Optional[str] = None,
) -> DBAdapter:
    resolved_backend = (backend or os.getenv("BIO_AGENT_DB_BACKEND") or "sqlite").strip().lower()
    resolved_url = database_url or os.getenv("BIO_AGENT_DATABASE_URL", "").strip()
    if resolved_url.startswith("postgres://") or resolved_url.startswith("postgresql://"):
        return PostgresAdapter(resolved_url)
    if resolved_backend == "postgres":
        if not resolved_url:
            raise RuntimeError(
                "BIO_AGENT_DB_BACKEND=postgres requires BIO_AGENT_DATABASE_URL to be set."
            )
        return PostgresAdapter(resolved_url)
    db_path = os.path.join(storage_dir, db_name)
    return SQLiteAdapter(db_path)


def postgres_table_name(agent_name: str, suffix: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", agent_name.strip().lower()).strip("_") or "default"
    return f"{base}_{suffix}"
