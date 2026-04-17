from pathlib import Path

import pytest

from bio_agent_os.core.sqlite_store import SQLiteStore


TEST_DATA_DIR = Path("test_data")
SQLITE_FILES = [
    TEST_DATA_DIR / "bio_agent_os.db",
    TEST_DATA_DIR / "bio_agent_os.db-shm",
    TEST_DATA_DIR / "bio_agent_os.db-wal",
]


def _reset_sqlite():
    db_path = SQLITE_FILES[0]
    if not db_path.exists():
        return
    store = SQLiteStore(storage_dir=str(TEST_DATA_DIR))
    tables = store.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    for row in tables:
        table_name = row["name"]
        if table_name.startswith("sqlite_"):
            continue
        store.execute(f"DROP TABLE IF EXISTS {table_name}")


@pytest.fixture(autouse=True)
def clean_test_storage():
    TEST_DATA_DIR.mkdir(exist_ok=True)
    _reset_sqlite()
    yield
    _reset_sqlite()
