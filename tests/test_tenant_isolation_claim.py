"""Cô lập tenant phải ràng buộc việc GIÀNH, không chỉ việc XỬ LÝ.

    ISOLATION MUST CONSTRAIN ACQUISITION, NOT MERELY PROCESSING.

Sai cũ, đo được trước khi sửa: một hàng đợi chỉ có job của `tenant-B`, một
worker giới hạn `tenant-A`.

    metrics  claimed=0 completed=0        mọi vòng
    store    tenant-B job -> in_progress, locked_by=<worker của A>, attempts=1

Ranh giới đã bị vượt từ trước khi ai đó nhìn tới tenant, và metric **không báo
gì cả** — không phải một con số sai, mà là một con số vắng mặt.

Và không sửa được bằng cách nhả nhanh sau khi claim: lease đã bị lấy, hàng đợi
của tenant khác đã bị chạm, và worker chính chủ đã bị chặn suốt cửa sổ đó.

Nên vị từ tenant nằm trong SQL, ở **cả** SELECT lẫn guard của UPDATE — guard
đặc biệt quan trọng, vì đó mới là câu lệnh quyết định cuộc đua.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from lease_time import past_expiry

A, B = "tenant-A", "tenant-B"
TEXT_A = "Số điện thoại của Bùi Cường là 0911000111."
TEXT_B = "Số điện thoại của Hoàng Yến là 0922000222."


def _store(tmp_path: Path, name: str, tenants: list[str]):
    memory_os = MemoryOS(tmp_path / f"{name}.db", projection_mode="outbox")
    for index, tenant in enumerate(tenants):
        memory_os.observe(tenant_id=tenant, actor="a", source="u",
                          content=(TEXT_A if tenant == A else TEXT_B) +
                                  f" #{index}",
                          workspace_id="w1")
    return memory_os


def _jobs(memory_os) -> list[dict]:
    return [dict(r) for r in memory_os.memories.conn.execute(
        "SELECT tenant_id, status, locked_by, attempts FROM projection_outbox")]


def _touched(memory_os, tenant: str) -> list[dict]:
    """Hàng của tenant này đã bị ai đó chạm chưa — đọc lại từ store."""
    return [j for j in _jobs(memory_os)
            if j["tenant_id"] == tenant
            and (j["locked_by"] not in (None, "") or j["attempts"] > 0)]


def _run(memory_os, tenant: str, cycles: int = 3, worker_id: str | None = None):
    kwargs = {"tenant_id": tenant}
    if worker_id:
        kwargs["worker_id"] = worker_id
    worker = worker_for(memory_os, **kwargs)
    return [worker.run_once() for _ in range(cycles)]


# ---------------------------------------------------------------------------
# 1–7
# ---------------------------------------------------------------------------

def test_1_a_worker_never_touches_another_tenants_only_job(tmp_path):
    memory_os = _store(tmp_path, "only-b", [B])
    try:
        _run(memory_os, A)
        assert _touched(memory_os, B) == [], (
            f"worker của {A} đã chạm hàng của {B}: {_jobs(memory_os)}")
        assert _jobs(memory_os)[0]["status"] == "pending"
    finally:
        memory_os.close()


def test_2_another_tenant_at_the_head_does_not_block(tmp_path):
    """Head-of-line: B đứng đầu hàng, A đứng sau. A vẫn phải chạy được.

    Một bản sửa 'claim rồi bỏ qua' sẽ đốt cả batch vào job của B và bỏ đói A."""
    memory_os = _store(tmp_path, "head-of-line", [B, A])
    try:
        _run(memory_os, A)
        done = [j for j in _jobs(memory_os)
                if j["tenant_id"] == A and j["status"] == "completed"]
        assert done, f"{A} bị chặn đầu hàng: {_jobs(memory_os)}"
        assert _touched(memory_os, B) == []
    finally:
        memory_os.close()


def test_3_an_interleaved_queue_yields_only_this_tenant(tmp_path):
    memory_os = _store(tmp_path, "interleaved", [A, B, A, B, A])
    try:
        _run(memory_os, A, cycles=4)
        assert _touched(memory_os, B) == []
        completed_a = [j for j in _jobs(memory_os)
                       if j["tenant_id"] == A and j["status"] == "completed"]
        assert len(completed_a) == 3, _jobs(memory_os)
    finally:
        memory_os.close()


def test_4_two_workers_never_cross_claim(tmp_path):
    memory_os = _store(tmp_path, "concurrent", [A, B, A, B])
    try:
        _run(memory_os, A, cycles=2, worker_id="worker-A")
        _run(memory_os, B, cycles=2, worker_id="worker-B")
        for job in _jobs(memory_os):
            owner = job["locked_by"]
            if owner:
                assert owner.endswith(job["tenant_id"][-1]), (
                    f"chéo tenant: {job}")
        assert all(j["status"] == "completed" for j in _jobs(memory_os)), (
            _jobs(memory_os))
    finally:
        memory_os.close()


def test_5_reclaim_keeps_tenant_ownership(tmp_path):
    """Thu hồi một lease quá hạn vẫn phải tôn trọng tenant."""
    memory_os = _store(tmp_path, "reclaim", [B])
    try:
        outbox = worker_for(memory_os).outbox
        outbox.claim(worker_id="ghost", tenant_id=B)      # B tự giữ, rồi chết
        # Lease của ghost PHẢI đã hết hạn dưới mắt A — nếu không, ca này xanh
        # rỗng: không có gì đủ điều kiện thu hồi thì cấm-xuyên-tenant chưa hề
        # bị thử. Đẩy đồng hồ qua hạn rồi mới hỏi.
        worker = worker_for(memory_os, tenant_id=A, lease_seconds=300)
        worker.run_once(claim_now=past_expiry(memory_os, 300))
        job = _jobs(memory_os)[0]
        assert job["locked_by"] == "ghost", (
            f"worker của {A} thu hồi được lease của {B}: {job}")
    finally:
        memory_os.close()


def test_6_restart_cannot_recover_another_tenants_job(tmp_path):
    memory_os = _store(tmp_path, "restart", [B])
    try:
        first = worker_for(memory_os, tenant_id=B)
        first.outbox.claim(worker_id="crashed-B", tenant_id=B)
        del first
        _run(memory_os, A, cycles=3)                      # tiến trình mới của A
        job = _jobs(memory_os)[0]
        assert job["locked_by"] == "crashed-B", (
            f"{A} khôi phục job của {B} sau restart: {job}")
    finally:
        memory_os.close()


def test_7_claimed_counts_only_tenant_valid_claims(tmp_path):
    """`claimed` phải đếm chuyển đổi đã xác nhận, không đếm lời gọi đã thử.

    Trước bản vá, `claimed` đếm danh sách SAU khi lọc, nên nó báo 0 trong khi
    một hàng của tenant khác đã bị khoá — một con số vắng mặt cho một sự kiện
    đã xảy ra."""
    memory_os = _store(tmp_path, "metrics", [B, A])
    try:
        metrics = _run(memory_os, A, cycles=1)[0]
        assert metrics.claimed == 1, metrics
        assert _touched(memory_os, B) == []
        # Sổ và store phải kể cùng một câu chuyện.
        locked_or_done = [j for j in _jobs(memory_os)
                          if j["tenant_id"] == A and j["attempts"] > 0]
        assert len(locked_or_done) == metrics.claimed
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# mutant
# ---------------------------------------------------------------------------

def test_mutant_claim_globally_then_filter_tenant(tmp_path, monkeypatch):
    """CLAIM_GLOBALLY_THEN_FILTER_TENANT — chính code trước bản vá.

    Nếu mutant không tái tạo được việc chiếm lease xuyên tenant thì bảy ca trên
    chưa chứng minh bản vá sửa gì."""
    from bio_agent_os.cognitive import outbox as outbox_module

    real_claim = outbox_module.ProjectionOutbox.claim

    def global_claim(self, worker_id, *, limit=1, now=None,
                     lease_seconds=300.0, tenant_id=None):
        # Giành trước, hỏi tenant sau — đúng hình dạng cũ.
        jobs = real_claim(self, worker_id, limit=limit, now=now,
                          lease_seconds=lease_seconds, tenant_id=None)
        return [j for j in jobs if tenant_id is None or j.tenant_id == tenant_id]

    monkeypatch.setattr(outbox_module.ProjectionOutbox, "claim", global_claim)

    memory_os = _store(tmp_path, "mutant", [B])
    try:
        _run(memory_os, A)
        assert _touched(memory_os, B), (
            "mutant không tái tạo được việc chiếm lease xuyên tenant — bảy ca "
            "trên chưa chạm tới cơ chế chúng định đo")
    finally:
        memory_os.close()


def test_an_unscoped_worker_still_takes_everything(tmp_path):
    """Nửa phải không được vỡ: `tenant_id=None` nghĩa là mọi tenant, thứ mà một
    drain không giới hạn hoặc một triển khai một-tenant cần."""
    memory_os = _store(tmp_path, "unscoped", [A, B])
    try:
        worker = worker_for(memory_os)
        for _ in range(4):
            worker.run_once()
        assert all(j["status"] == "completed" for j in _jobs(memory_os)), (
            _jobs(memory_os))
    finally:
        memory_os.close()
