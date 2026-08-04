from __future__ import annotations
import sqlite3
from pathlib import Path

def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA wal_autocheckpoint=1000')
    return conn
