"""
Shared SQLite utilities for production-safe local persistence.
"""

import json
import os
import sqlite3
import threading
from typing import Any, Iterable, Optional


class SQLiteStore:
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, storage_dir: str = "data", db_name: str = "bio_agent_os.db"):
        self.storage_dir = storage_dir
        self.db_path = os.path.join(storage_dir, db_name)
        os.makedirs(storage_dir, exist_ok=True)
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
                cursor = connection.execute(sql, tuple(parameters or []))
                connection.commit()
                return cursor

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        with self._lock:
            with self.connect() as connection:
                connection.executemany(sql, rows)
                connection.commit()

    def fetchall(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> list[sqlite3.Row]:
        with self._lock:
            with self.connect() as connection:
                cursor = connection.execute(sql, tuple(parameters or []))
                return cursor.fetchall()

    def fetchone(self, sql: str, parameters: Optional[Iterable[Any]] = None) -> Optional[sqlite3.Row]:
        with self._lock:
            with self.connect() as connection:
                cursor = connection.execute(sql, tuple(parameters or []))
                return cursor.fetchone()

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
