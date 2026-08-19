"""Safe rollback primitive — generation replacement beats in-place restoration.

Mutant quan trọng nhất ở đây là CHÍNH THAO TÁC CŨ: copy2 đè canonical khi
handle còn sống rồi để handle checkpoint. RCA lane 1 chứng minh 5/5 nó tạo
đúng chữ ký sự cố; test này giữ nó vĩnh viễn đỏ nếu ai đưa nó trở lại.
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import shutil
import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.store_generation import (GenerationError,
                                                     certify_candidate,
                                                     install_generation)


def _mkdb(path: Path, rows: int, tag: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_v ON t(v)")
    conn.executemany("INSERT INTO t (v) VALUES (?)",
                     [(f"{tag}-{i}",) for i in range(rows)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for ext in ("-wal", "-shm"):
        p = Path(str(path) + ext)
        if p.exists():
            p.unlink()


def test_happy_path_installs_and_quarantines(tmp_path):
    canonical, candidate = tmp_path / "store.db", tmp_path / "cand.db"
    _mkdb(canonical, 50, "old")
    _mkdb(candidate, 80, "new")
    manifest = install_generation(canonical, candidate, tmp_path / "quar")
    conn = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 80
    assert [r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"]
    conn.close()
    bundle = Path(manifest["quarantine_bundle"])
    assert (bundle / "store.db").exists(), "bản cũ phải nằm trong quarantine"


def test_corrupt_candidate_is_refused_before_touching_canonical(tmp_path):
    canonical, candidate = tmp_path / "store.db", tmp_path / "cand.db"
    _mkdb(canonical, 50, "old")
    candidate.write_bytes(b"SQLite format 3\x00" + b"\xde\xad" * 400)
    before = canonical.read_bytes()
    with pytest.raises(GenerationError):
        install_generation(canonical, candidate, tmp_path / "quar")
    assert canonical.read_bytes() == before, "canonical bị đụng dù candidate hỏng"


def test_candidate_with_sidecar_is_refused(tmp_path):
    candidate = tmp_path / "cand.db"
    _mkdb(candidate, 10, "x")
    Path(str(candidate) + "-wal").write_bytes(b"stale")
    with pytest.raises(GenerationError):
        certify_candidate(candidate)


def test_live_handle_fails_closed_and_canonical_survives(tmp_path):
    """Nhánh A của thiết kế: handle sống → move-aside thất bại → FAIL CLOSED."""
    canonical, candidate = tmp_path / "store.db", tmp_path / "cand.db"
    _mkdb(canonical, 50, "old")
    _mkdb(candidate, 80, "new")
    live = sqlite3.connect(str(canonical))       # handle sống giữ file
    live.execute("SELECT COUNT(*) FROM t").fetchone()
    try:
        with pytest.raises(GenerationError, match="FAIL CLOSED"):
            install_generation(canonical, candidate, tmp_path / "quar")
        # canonical nguyên vẹn và VẪN là generation cũ
        assert sqlite3.connect(str(canonical)).execute(
            "SELECT COUNT(*) FROM t").fetchone()[0] == 50
        # handle cũ vẫn dùng được — không có trạng thái lai
        live.executemany("INSERT INTO t (v) VALUES (?)", [("post",)] * 3)
        live.commit()
    finally:
        live.close()
    conn = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
    assert [r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"]
    conn.close()


def _hold_open(path, opened, release, done):
    conn = sqlite3.connect(str(path))
    conn.execute("SELECT COUNT(*) FROM t").fetchone()
    opened.set()
    release.wait(timeout=30)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # hành vi kẻ giết cũ
        conn.close()
    finally:
        done.set()


def test_process_level_stale_handle_cannot_reach_new_generation(tmp_path):
    """Nhân chứng process thật: handle ở TIẾN TRÌNH KHÁC giữ file →
    primitive fail-closed; sau khi handle chết, install thành công và
    checkpoint của kẻ chết không chạm generation mới."""
    canonical, candidate = tmp_path / "store.db", tmp_path / "cand.db"
    _mkdb(canonical, 50, "old")
    _mkdb(candidate, 80, "new")
    ctx = mp.get_context("spawn")
    opened, release, done = ctx.Event(), ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_open,
                       args=(str(canonical), opened, release, done))
    proc.start()
    assert opened.wait(timeout=30), "INVALID — tiến trình giữ file chưa mở"
    with pytest.raises(GenerationError, match="FAIL CLOSED"):
        install_generation(canonical, candidate, tmp_path / "quar")
    release.set()
    assert done.wait(timeout=30)
    proc.join(timeout=30)
    manifest = install_generation(canonical, candidate, tmp_path / "quar")
    conn = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 80
    assert [r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"]
    conn.close()


def test_mutant_the_old_bug_still_corrupts_and_the_primitive_does_not(tmp_path):
    """EXACT-OLD-BUG MUTANT: copy2 đè khi handle sống + checkpoint sau đó.

    RCA 5/5: nó tạo đúng chữ ký sự cố. Nếu một ngày mutant này KHÔNG còn hỏng,
    môi trường đã đổi và phải xem lại toàn bộ lập luận an toàn — nên nó phải
    đỏ-khi-sạch, như mọi mutant khác trong lab.
    """
    canonical, s0 = tmp_path / "store.db", tmp_path / "s0.db"
    _mkdb(canonical, 200, "base")
    shutil.copy2(canonical, s0)
    conn = sqlite3.connect(str(canonical))
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executemany("INSERT INTO t (v) VALUES (?)",
                     [(f"post-{i}",) for i in range(300)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # WAL rỗng — hình Control D
    shutil.copy2(s0, canonical)                       # <- THAO TÁC CŨ
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("kill",)] * 50)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    probe = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
    try:
        try:
            ic = [r[0] for r in probe.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError:
            ic = ["fatal"]
    finally:
        probe.close()
    assert ic != ["ok"], (
        "thao tác cũ KHÔNG còn gây hỏng trên môi trường này — mọi lập luận "
        "an toàn của primitive phải được xem lại")


def test_no_copy_over_canonical_rollback_code_returns(tmp_path):
    """Guard tĩnh: không ai được đưa copy-đè-canonical trở lại làm rollback.

    Quét activation/ tìm `shutil.copy2(..., REAL)` — allowlist chỉ gồm các
    artifact pháp y lịch sử đã đóng băng và install-vào-chỗ-trống của recovery
    (chạy SAU move-aside, được R5 chứng minh).
    """
    import re
    root = Path(__file__).parent.parent / "activation"
    allow = {"R_recover.py", "A4_real_canary.py", "A5_staged.py",
             "A4_1_forensic.py", "A3_clone_rehearsal.py"}
    hits = []
    for f in root.rglob("*.py"):
        if f.name in allow:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"copy2\([^)]*REAL", line):
                hits.append(f"{f.name}:{i}")
    assert not hits, (
        f"copy-đè-canonical quay lại ở {hits} — dùng "
        f"store_generation.install_generation, không đè file sống")


def test_install_failure_after_move_aside_restores_canonical(tmp_path,
                                                             monkeypatch):
    """FAIL CLOSED phải đúng trọn đường, không chỉ nửa move-aside.

    Trước HBF-3, chỉ vòng move-aside có nhánh trả-lại; copy2/replace/verify
    nằm ngoài. Hỏng ở đó là canonical đã bị dời đi mà chưa có gì thay thế —
    và người gọi đọc nhầm thành "chưa install", bật lại writer vào một store
    không tồn tại. Ba lăng kính review độc lập cùng chỉ vào đây."""
    import shutil as _shutil

    from bio_agent_os.cognitive import store_generation as sg

    canonical = tmp_path / "canonical.db"
    candidate = tmp_path / "candidate.db"
    for path, mark in ((canonical, "SONG"), (candidate, "MOI")):
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (mark,))
        conn.commit()
        conn.close()
    before = hashlib.sha256(canonical.read_bytes()).hexdigest()

    def boom(src, dst):                       # hỏng ĐÚNG sau move-aside
        raise OSError("trình quét giữ file .installing")

    monkeypatch.setattr(_shutil, "copy2", boom)
    with pytest.raises(sg.GenerationError, match="ĐÃ KHÔI PHỤC"):
        sg.install_generation(canonical, candidate, tmp_path / "q")

    assert canonical.exists(), "canonical biến mất — fail-closed đã vỡ"
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == before
    conn = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "SONG"
    finally:
        conn.close()
