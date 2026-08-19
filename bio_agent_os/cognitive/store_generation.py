"""Thay thế một SQLite store bằng GENERATION mới — không bao giờ đè tại chỗ.

    GENERATION REPLACEMENT BEATS IN-PLACE RESTORATION.

Sự cố 18/08 và RCA lane 1 chứng minh bằng 5/5 trial có control: một handle
sống mang PAGE CACHE của file cũ; sau khi file bị copy-đè, mọi write hay
checkpoint của handle đó — kể cả cái checkpoint ngầm trong `close()` — áp
trang cũ lên lineage mới và làm lệch btree/index đúng chữ ký đã thấy trên
store thật (92 findings). Không phải stale WAL frames: nhánh đó sạch 5/5 và
được giải thích (cùng lineage thì replay tiến nhất quán).

Nên primitive này không cố thắng cuộc đua "đã đóng hết mọi handle chưa". Nó
đổi bàn cờ: canonical path được MOVE-ASIDE thành một generation bundle rồi
candidate vào CHỖ TRỐNG. Một handle cũ nếu còn sống thì hoặc:

  A. làm move-aside thất bại (Windows khoá file đang mở) → FAIL CLOSED,
     chưa có gì được cài, store cũ nguyên vẹn;
  B. tiếp tục trỏ vào inode/file CŨ đã bị move — mọi checkpoint của nó chỉ
     làm bẩn cái xác trong quarantine, không có chìa khoá vào generation mới.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

SIDECARS = ("-wal", "-shm")


class GenerationError(RuntimeError):
    """Fail-closed: canonical chưa bị đụng, hoặc bundle cũ còn nguyên để trả lại."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def certify_candidate(candidate: Path) -> str:
    """Candidate phải tự chứng minh trước khi được phép lại gần canonical."""
    if not candidate.exists():
        raise GenerationError(f"candidate không tồn tại: {candidate}")
    # Chỉ -wal CÓ NỘI DUNG mới là bằng chứng candidate chưa checkpoint xong.
    # Một reader mở WAL db (kể cả chính certify này, mode=ro) tạo ra -shm và
    # -wal RỖNG — đó là hành vi chuẩn của SQLite, không phải khuyết tật của
    # candidate. Probe 18/08: lần certify thứ hai vấp đúng cái -wal rỗng mà
    # lần certify thứ nhất tạo ra.
    wal = Path(str(candidate) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise GenerationError(
            f"candidate mang -wal {wal.stat().st_size} bytes — phải "
            f"checkpoint TRUNCATE trước khi certify")
    conn = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
    try:
        ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as exc:
        raise GenerationError(f"candidate hỏng tới mức không kiểm được: {exc}")
    finally:
        conn.close()
    if ic != ["ok"]:
        raise GenerationError(f"candidate không sạch: {ic[:3]}")
    return _sha(candidate)


def install_generation(canonical: Path, candidate: Path,
                       quarantine_dir: Path) -> dict:
    """CERTIFY → MOVE-ASIDE → INSTALL vào chỗ trống → VERIFY độc lập.

    Trả về manifest. Ném `GenerationError` với canonical còn nguyên (nếu chưa
    qua move-aside) hoặc với bundle cũ nằm đủ trong quarantine (nếu install
    hỏng giữa chừng) — không có nhánh nào để lại canonical ở trạng thái lai.
    """
    canonical, candidate = Path(canonical), Path(candidate)
    want = certify_candidate(candidate)

    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    bundle = quarantine_dir / f"gen-{want[:12]}"
    bundle.mkdir(exist_ok=True)

    # MOVE-ASIDE: trên Windows, một file đang bị handle sống giữ sẽ làm move
    # thất bại — và đó CHÍNH LÀ hàng rào fail-closed, không phải phiền toái.
    moved: list[tuple[Path, Path]] = []
    try:
        for name in [canonical.name] + [canonical.name + e for e in SIDECARS]:
            src = canonical.parent / name
            if src.exists():
                dst = bundle / name
                shutil.move(str(src), str(dst))
                moved.append((src, dst))
    except OSError as exc:
        for src, dst in reversed(moved):      # trả lại nguyên trạng
            shutil.move(str(dst), str(src))
        raise GenerationError(
            f"move-aside thất bại (handle còn sống?): {exc} — FAIL CLOSED, "
            f"canonical nguyên vẹn, chưa có gì được cài") from exc

    # INSTALL vào chỗ TRỐNG — copy sang file tạm cùng thư mục rồi rename.
    #
    # Nửa này TỪNG nằm ngoài mọi restore: move-aside có nhánh trả-lại, nhưng
    # nếu copy2/replace/verify hỏng thì canonical đã bị dời đi mà không có gì
    # thay thế, và lời hứa "FAIL CLOSED" trong docstring chỉ đúng nửa đường.
    # Trên Windows đó không phải giả thuyết xa xôi: một trình quét mở file
    # `.installing` là `replace` trả ACCESS_DENIED. Ba lăng kính review độc
    # lập trước HBF-3 cùng chỉ vào đây.
    tmp = canonical.parent / (canonical.name + ".installing")
    try:
        shutil.copy2(candidate, tmp)
        tmp.replace(canonical)

        got = _sha(canonical)
        conn = sqlite3.connect(f"file:{canonical.as_posix()}?mode=ro", uri=True)
        try:
            ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        finally:
            conn.close()
        if got != want or ic != ["ok"]:
            raise GenerationError(
                f"installed generation không qua verify (hash {got[:8]} vs "
                f"{want[:8]}, ic={ic[:2]})")
    except BaseException as exc:
        # Trả canonical về nguyên trạng: xoá phần đã cài dở, đưa bundle cũ về.
        restored, restore_error = [], None
        try:
            if tmp.exists():
                tmp.unlink()
            for name in [canonical.name] + [canonical.name + e for e in SIDECARS]:
                p = canonical.parent / name
                if p.exists():
                    p.unlink()
            for src, dst in reversed(moved):
                shutil.move(str(dst), str(src))
                restored.append(src.name)
        except OSError as restore_exc:                     # noqa: PERF203
            restore_error = restore_exc
        if restore_error is not None:
            raise GenerationError(
                f"install hỏng ({exc}) VÀ khôi phục hỏng ({restore_error}) — "
                f"canonical KHÔNG xác định; bundle cũ nằm ở {bundle}"
            ) from exc
        raise GenerationError(
            f"install hỏng: {exc} — ĐÃ KHÔI PHỤC canonical từ bundle "
            f"({', '.join(restored) or 'không có gì để trả'}), "
            f"generation cũ vẫn đang phục vụ") from exc
    return {"installed_sha256": got, "quarantine_bundle": str(bundle),
            "moved": [d.name for _s, d in moved]}
