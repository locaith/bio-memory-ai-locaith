"""Điều khiển thời gian GIÀNH/THU HỒI một cách tường minh trong test.

Trước ngày 18/08, cách chuẩn của cả lane này để ép thu hồi ngay là
`lease_seconds=0` — 59 điểm gọi, 11 file. Một sweep 125 run trên hai tiến trình
thật chứng minh zero lease cho phép hai worker cùng được cấp quyền trên một job
(`double_ack <=> locked_at_B <= stale_before_A`, khớp 125/125), nên
`validate_lease_seconds` nay từ chối nó ngay tại `claim()`.

Các test cũ không sai về bất biến; chúng chạy trong một execution domain giờ
được xác định là không hợp lệ. Bằng chứng của chúng phải được tái lập trong
miền lease hợp lệ — và cách đúng là lease dương + đẩy `claim_now` qua hạn một
cách TƯỜNG MINH, có guard, thay vì làm mọi cửa mở cùng lúc bằng zero.
"""
from __future__ import annotations

import sqlite3

# Đủ dài để không lease nào "tự" hết hạn giữa chừng vì đồng hồ thật trôi;
# mọi lần hết hạn trong test là do `claim_now` được đẩy qua nó một cách chủ ý.
TEST_LEASE_SECONDS = 300.0


def _conn_of(store) -> sqlite3.Connection:
    if isinstance(store, sqlite3.Connection):
        return store
    direct = getattr(store, "conn", None)
    if isinstance(direct, sqlite3.Connection):
        return direct                     # một store cụ thể (events/memories)
    for path in ("memories", "events"):
        holder = getattr(store, path, None)
        conn = getattr(holder, "conn", None)
        if conn is not None:
            return conn
    raise TypeError(f"khong tim thay connection tren {store!r}")


def past_expiry(store, lease_seconds: float = TEST_LEASE_SECONDS,
                *, epsilon: float = 1.0) -> float:
    """Một `claim_now` chắc chắn ĐÃ vượt hạn của mọi lease đang giữ.

    Guard nằm ngay trong hàm: kết quả được khẳng định là lớn hơn
    `locked_at + lease_seconds` của mọi hàng `in_progress`. Thiếu guard này,
    một test có thể xanh chỉ vì nó không còn đi qua đường thu hồi — đúng lớp
    false-green đã bắt sáu lần trong lane H1.
    """
    conn = _conn_of(store)
    row = conn.execute(
        "SELECT MAX(COALESCE(locked_at, 0)), MAX(available_at) "
        "FROM projection_outbox").fetchone()
    latest_locked = float(row[0] or 0.0)
    latest_available = float(row[1] or 0.0)
    now = max(latest_locked + lease_seconds, latest_available) + epsilon
    assert now > latest_locked + lease_seconds, (
        "claim_now chưa vượt hạn lease — phép thu hồi sẽ không xảy ra và ca "
        "test sẽ đo một thứ khác")
    return now
