"""
Database migration utilities.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from bio_agent_os.core.db_adapter import PostgresAdapter


SQLITE_TYPE_MAP = {
    "TEXT": "TEXT",
    "INTEGER": "BIGINT",
    "REAL": "DOUBLE PRECISION",
    "BLOB": "BYTEA",
    "NUMERIC": "NUMERIC",
}


def map_sqlite_type(sqlite_type: str) -> str:
    normalized = (sqlite_type or "TEXT").strip().upper()
    for prefix, postgres_type in SQLITE_TYPE_MAP.items():
        if normalized.startswith(prefix):
            return postgres_type
    return "TEXT"


@dataclass
class MigrationSummary:
    tables_created: int
    rows_copied: int
    tables_skipped: list[str]


class SQLiteToPostgresMigrator:
    def __init__(self, sqlite_path: str, postgres_dsn: str):
        self.sqlite_path = sqlite_path
        self.postgres = PostgresAdapter(postgres_dsn)

    def _sqlite_connect(self):
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _sqlite_tables(self) -> list[str]:
        with self._sqlite_connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC"
            ).fetchall()
        return [row["name"] for row in rows]

    def _table_info(self, table_name: str) -> list[dict[str, Any]]:
        with self._sqlite_connect() as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [dict(row) for row in rows]

    def _create_table_sql(self, table_name: str, columns: list[dict[str, Any]]) -> Optional[str]:
        if not columns:
            return None
        primary_keys = [column["name"] for column in columns if int(column.get("pk") or 0) > 0]
        parts = []
        for column in columns:
            line = f"{column['name']} {map_sqlite_type(str(column.get('type') or 'TEXT'))}"
            if int(column.get("notnull") or 0):
                line += " NOT NULL"
            if column.get("dflt_value") is not None:
                line += f" DEFAULT {column['dflt_value']}"
            parts.append(line)
        if primary_keys:
            parts.append(f"PRIMARY KEY ({', '.join(primary_keys)})")
        return f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(parts)})"

    def migrate(self) -> MigrationSummary:
        if not os.path.exists(self.sqlite_path):
            raise FileNotFoundError(f"SQLite database not found: {self.sqlite_path}")

        tables_created = 0
        rows_copied = 0
        skipped: list[str] = []

        for table_name in self._sqlite_tables():
            columns = self._table_info(table_name)
            create_sql = self._create_table_sql(table_name, columns)
            if not create_sql:
                skipped.append(table_name)
                continue
            self.postgres.execute(create_sql)
            tables_created += 1

            column_names = [column["name"] for column in columns]
            placeholders = ", ".join(["?"] * len(column_names))
            insert_sql = (
                f"INSERT OR REPLACE INTO {table_name} ({', '.join(column_names)}) "
                f"VALUES ({placeholders})"
            )
            with self._sqlite_connect() as connection:
                rows = connection.execute(f"SELECT * FROM {table_name}").fetchall()
            payload = [tuple(row[column] for column in column_names) for row in rows]
            if payload:
                self.postgres.executemany(insert_sql, payload)
                rows_copied += len(payload)

        return MigrationSummary(
            tables_created=tables_created,
            rows_copied=rows_copied,
            tables_skipped=skipped,
        )
