import atexit
import os
from pathlib import Path

import pytest

from bio_agent_os.core.sqlite_store import SQLiteStore


TEST_DATA_DIR = Path("test_data")

#: Held for the length of one pytest process.
#:
#: `clean_test_storage` below drops every table in a single shared database
#: before and after *every* test. That is correct for one process running tests
#: in sequence, and catastrophic for two: on 2026-08-15 a suite running in the
#: background while another pytest ran in the foreground produced fifteen
#: failures across six unrelated files, all `no such table`, none of them real.
#: An hour went into bisecting a regression that did not exist.
#:
#: The cascade is unreadable; this message is not.
_LOCK = TEST_DATA_DIR / ".pytest-running.lock"


def _claim_lock() -> None:
    TEST_DATA_DIR.mkdir(exist_ok=True)
    if _LOCK.exists():
        try:
            owner = _LOCK.read_text(encoding="utf-8").strip()
        except OSError:
            owner = "?"
        # A stale lock from a killed run must not block the next one forever.
        alive = False
        if owner.isdigit():
            try:
                os.kill(int(owner), 0)
                alive = True
            except (OSError, PermissionError):
                alive = os.name == "nt" and _pid_alive_windows(int(owner))
        if alive:
            raise pytest.UsageError(
                f"Một tiến trình pytest khác (PID {owner}) đang chạy trên cùng "
                f"{TEST_DATA_DIR}/. Fixture dọn dẹp xoá TOÀN BỘ bảng trong một "
                f"database dùng chung trước và sau mỗi test, nên hai tiến trình "
                f"sẽ xoá bảng của nhau và sinh ra hàng loạt lỗi 'no such table' "
                f"không liên quan gì tới code. Chờ tiến trình kia xong, hoặc xoá "
                f"{_LOCK} nếu nó đã chết."
            )
    _LOCK.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_release_lock)


def _pid_alive_windows(pid: int) -> bool:
    import subprocess

    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=10)
        return str(pid) in out.stdout
    except Exception:                                  # noqa: BLE001
        return False


def _release_lock() -> None:
    try:
        if _LOCK.exists() and _LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _LOCK.unlink()
    except OSError:
        pass


def _pin_projection_mode_env() -> None:
    """Suite đo hành vi MẶC ĐỊNH (legacy) — không đo env rơi rớt của phiên.

    18/08: session env còn mang BIO_AGENT_PROJECTION_MODE=outbox từ một lần
    A5.4 đã revert trong settings; 5 ca đỏ vì suite chạy dưới default sai.
    Test nào cần mode khác phải tự set TƯỜNG MINH (monkeypatch/env con).
    """
    os.environ.pop("BIO_AGENT_PROJECTION_MODE", None)


def pytest_configure(config):                          # noqa: ARG001
    _pin_projection_mode_env()
    _claim_lock()


def pytest_unconfigure(config):                        # noqa: ARG001
    _release_lock()
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
