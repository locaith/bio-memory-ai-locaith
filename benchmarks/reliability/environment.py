"""What the machine was, so two numbers can be compared honestly.

A throughput figure without the machine it came from is not a measurement, it
is a rumour. Everything here is captured per run and written next to the
results.

Deliberately dependency-free: RSS and RAM come from `ctypes` on Windows and
`resource` on POSIX rather than psutil, because adding a package to the
runtime in order to measure the runtime is a cost the measurement does not
justify.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

_IS_WINDOWS = sys.platform == "win32"


# -- memory -----------------------------------------------------------------

def rss_bytes() -> int:
    """Resident set size of this process, or 0 where it cannot be read."""
    if _IS_WINDOWS:
        return _windows_rss()
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes; macOS reports bytes.
        return int(peak) * (1024 if sys.platform.startswith("linux") else 1)
    except Exception:
        return 0


def peak_rss_bytes() -> int:
    """Peak RSS since process start, or 0 where it cannot be read."""
    if _IS_WINDOWS:
        return _windows_rss(peak=True)
    return rss_bytes()


def _windows_rss(*, peak: bool = False) -> int:
    """Working set from psapi.

    `argtypes` and `restype` are not optional here. Without them ctypes treats
    the process handle as a 32-bit int, the call fails, and this returns 0 —
    which reads as "no memory used" rather than "measurement broken". The
    first version of this function had exactly that bug, and every RSS figure
    it produced was a silent zero.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        handle = kernel32.GetCurrentProcess()

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_info = psapi.GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if not get_info(handle, ctypes.byref(counters), counters.cb):
            return 0
        return int(counters.PeakWorkingSetSize if peak else counters.WorkingSetSize)
    except Exception:
        return 0


def total_ram_bytes() -> int:
    if _IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.ullTotalPhys)
        except Exception:
            return 0
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return 0


# -- machine ----------------------------------------------------------------

def cpu_model() -> str:
    if _IS_WINDOWS:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            with key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or platform.machine()


def filesystem_of(path: str | Path) -> str:
    target = Path(path).resolve()
    root = target.anchor or str(target)
    if _IS_WINDOWS:
        try:
            import ctypes

            name = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), name, 261, None, None, None, fs, 261
            )
            return fs.value or "unknown"
        except Exception:
            return "unknown"
    try:
        import shutil

        shutil.disk_usage(root)
        return "posix"
    except Exception:
        return "unknown"


def commit_sha(repo: str | Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def working_tree_clean(repo: str | Path) -> bool:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
        return out.stdout.strip() == ""
    except Exception:
        return False


# -- database ---------------------------------------------------------------

def sqlite_settings(db_path: str | Path) -> dict[str, Any]:
    """Pragmas as the database actually reports them, not as we intended."""
    settings: dict[str, Any] = {}
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            for pragma in (
                "journal_mode", "synchronous", "busy_timeout", "page_size",
                "cache_size", "temp_store", "foreign_keys", "wal_autocheckpoint",
            ):
                row = conn.execute(f"PRAGMA {pragma}").fetchone()
                settings[pragma] = row[0] if row else None
        finally:
            conn.close()
    except Exception as exc:
        settings["error"] = f"{type(exc).__name__}: {exc}"
    return settings


def database_footprint(db_path: str | Path) -> dict[str, int]:
    """Main file, WAL and shared-memory index, in bytes."""
    base = Path(db_path)
    out = {}
    for suffix, key in (("", "db_bytes"), ("-wal", "wal_bytes"), ("-shm", "shm_bytes")):
        candidate = Path(str(base) + suffix)
        out[key] = candidate.stat().st_size if candidate.exists() else 0
    out["total_bytes"] = sum(out.values())
    return out


# -- the record ------------------------------------------------------------

def capture(
    *,
    repo: str | Path,
    db_path: str | Path | None = None,
    workers: int | None = None,
    producers: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "platform": platform.platform(),
        "cpu_model": cpu_model(),
        "cpu_cores_logical": os.cpu_count(),
        "ram_bytes": total_ram_bytes(),
        "ram_gb": round(total_ram_bytes() / (1024 ** 3), 2),
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_module": sqlite3.version,
        "process_start_method": mp.get_start_method(allow_none=True) or "default",
        "commit_sha": commit_sha(repo),
        "working_tree_clean": working_tree_clean(repo),
        "producers": producers,
        "workers": workers,
        "measurement_tooling": "stdlib only (ctypes/resource); psutil not installed",
    }
    if db_path is not None:
        record["db_path"] = str(db_path)
        record["filesystem"] = filesystem_of(db_path)
        record["sqlite_settings"] = sqlite_settings(db_path)
        record["database_footprint"] = database_footprint(db_path)
    if extra:
        record.update(extra)
    return record


def write(record: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


__all__ = [
    "capture",
    "commit_sha",
    "cpu_model",
    "database_footprint",
    "filesystem_of",
    "peak_rss_bytes",
    "rss_bytes",
    "sqlite_settings",
    "total_ram_bytes",
    "working_tree_clean",
    "write",
]
