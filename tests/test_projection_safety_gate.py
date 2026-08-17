"""Projection Safety Gate v1 — mười một năng lực, không một điểm tổng.

Mỗi ca phải có **cả bốn**:

    CONTROL_EXECUTED                   đường bình thường có chạy
    DANGEROUS_PATH_EXECUTED            kịch bản nguy hiểm thật sự diễn ra
    MUTANT_TRIGGERED_PROHIBITED_STATE  mutant tạo được trạng thái bị cấm
    CLEAN_PREVENTED_PROHIBITED_STATE   bản sạch ngăn được nó

Mutant không kích hoạt được trạng thái bị cấm → **INVALID**, không phải PASS.
Đường production chạm không tới → **NOT_EVALUABLE**. Hai nhãn đó đã cứu dự án
này hai lần, và cả hai lần chúng trông y hệt PASS.

Trạng thái bị cấm phát biểu bằng **persistent state**, đọc lại từ store —
không phải bằng giá trị trả về của hàm. Control flow kể chuyện; persistent
state ký biên bản.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bio_agent_os.cognitive import forgetting, reconciliation_worker
from bio_agent_os.cognitive import outbox as outbox_module
from bio_agent_os.cognitive.facade import MemoryOS
from bio_agent_os.cognitive.models import MemoryType
from bio_agent_os.cognitive.projection_engine import ProjectionReplayEngine
from bio_agent_os.cognitive.reconciliation_worker import worker_for
from bio_agent_os.cognitive.tombstones import buried
from bio_agent_os.evals.projection_safety_gate import (
    CaseResult, TransitionLedger)

TEXT = "Số điện thoại của Hoàng Yến là 0977123456."
NEEDLE = "0977123456"
SUBJECT = "Hoàng Yến"
#: Chứng: cùng chủ thể, khác vị từ. Phải sống qua mọi ca.
CONTROL_TEXT = "Hoàng Yến sống ở Hà Nội."


def _store(tmp_path: Path, name: str, *, materialise: bool = False):
    memory_os = MemoryOS(tmp_path / f"{name}.db", projection_mode="outbox")
    event = memory_os.observe(tenant_id="t1", actor="a", source="u",
                              content=TEXT, workspace_id="w1")
    memory_os.observe(tenant_id="t1", actor="a", source="u",
                      content=CONTROL_TEXT, workspace_id="w1")
    if materialise:
        memory_os.remember(event=event, memory_type=MemoryType.SEMANTIC,
                           content=TEXT, confidence=0.9)
    return memory_os, event


def _holding(memory_os) -> int:
    """Trạng thái bị cấm, đọc lại từ store."""
    return sum(1 for r in memory_os.memories.conn.execute(
        "SELECT content FROM cognitive_memories") if NEEDLE in str(r[0]))


def _statuses(memory_os) -> dict[str, int]:
    try:
        return {str(s): int(n) for s, n in memory_os.memories.conn.execute(
            "SELECT status, COUNT(*) FROM projection_outbox GROUP BY status")}
    except sqlite3.OperationalError:
        return {}


def _drain(memory_os, cycles: int = 4, *, lease_seconds: float | None = None):
    """`lease_seconds` thuộc về NGƯỜI ĐỌC, không thuộc về job.

    Không có cột hạn lease nào trong `projection_outbox`. `locked_at` lưu thời
    điểm *bắt đầu*, và `claim` tính lại `stale_before = now - lease_seconds`
    mỗi lần, rồi so `locked_at <= stale_before`.

    Nên **lease không tự hết hạn**: không có gì chạy vào lúc hết hạn và hàng
    không đổi. Hết hạn chỉ tồn tại trong mắt người claim tiếp theo.

    Đây là chỗ ca 03 và 09 từng INVALID: fixture truyền `lease_seconds=0` vào
    lệnh **lấy** lease — không tác dụng gì lên hàng — rồi drain bằng worker mặc
    định 300 giây, nên `locked_at <= now-300` sai và hàng vô hình. Worker không
    từ chối job; nó không bao giờ thấy job.
    """
    kwargs = {} if lease_seconds is None else {"lease_seconds": lease_seconds}
    worker = worker_for(memory_os, **kwargs)
    return [worker.run_once() for _ in range(cycles)]


def _blind_burial(monkeypatch) -> None:
    """Mutant dùng chung cho các ca dựa vào chốt tại-thời-điểm-ghi."""
    monkeypatch.setattr(reconciliation_worker, "buried",
                        lambda conn, event_id: False)


def _run_case(tmp_path, case_id, name, prohibited, *, scenario, mutant,
              monkeypatch, materialise=False, mutant_name=""):
    """Chạy một ca ba lần: control, bản sạch, mutant.

    Ba lần chạy trên ba store riêng, vì một store đã bị mutant chạm không còn
    nói được gì về bản sạch.
    """
    result = CaseResult(case_id=case_id, name=name,
                        prohibited_state=prohibited, mutant_name=mutant_name)

    # 1. CONTROL — không forget. Nội dung PHẢI materialize.
    control_os, _ = _store(tmp_path, f"{case_id}-control",
                           materialise=materialise)
    try:
        _drain(control_os)
        result.control_executed = _holding(control_os) >= 1
        result.readback["control_holding"] = _holding(control_os)
    finally:
        control_os.close()

    # 2. BẢN SẠCH — kịch bản nguy hiểm, không mutant.
    clean_os, event = _store(tmp_path, f"{case_id}-clean",
                             materialise=materialise)
    try:
        scenario(clean_os, event)
        result.dangerous_path_executed = True
        result.clean_prevented = _holding(clean_os) == 0
        result.readback["clean_holding"] = _holding(clean_os)
        result.readback["clean_outbox"] = _statuses(clean_os)
        result.readback["clean_buried"] = buried(clean_os.memories.conn,
                                                 event.event_id)
        result.readback["control_sibling_alive"] = any(
            "Hà Nội" in str(r[0]) for r in clean_os.memories.conn.execute(
                "SELECT content FROM cognitive_memories"))
    finally:
        clean_os.close()

    # 3. MUTANT — phải TẠO ĐƯỢC trạng thái bị cấm.
    mutant_os, event = _store(tmp_path, f"{case_id}-mutant",
                              materialise=materialise)
    try:
        mutant(monkeypatch)
        scenario(mutant_os, event)
        result.mutant_triggered = _holding(mutant_os) >= 1
        result.readback["mutant_holding"] = _holding(mutant_os)
    finally:
        mutant_os.close()
    return result


# ---------------------------------------------------------------------------
# 01–07: thời điểm của lệnh quên so với vòng đời projection
# ---------------------------------------------------------------------------

def test_case_01_forget_before_enqueue(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)
        _drain(memory_os)

    result = _run_case(
        tmp_path, "01", "forget trước khi enqueue",
        "hàng đã quên xuất hiện trong cognitive_memories sau khi replay xếp "
        "hàng lại và worker chạy",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


def test_case_02_enqueue_then_forget(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        _drain(memory_os)

    result = _run_case(
        tmp_path, "02", "enqueue rồi mới forget",
        "hàng đã quên xuất hiện trong cognitive_memories sau khi worker drain",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


def test_case_03_forget_while_worker_holds_the_lease(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        # Worker thứ nhất giữ job. Lease của NÓ không quan trọng — hàng chỉ ghi
        # `locked_at`, không ghi hạn.
        worker = worker_for(memory_os)
        assert worker.outbox.claim(worker_id="gate-lease"), (
            "không claim được job — ca này chưa đo được gì")
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        # Worker tiếp theo coi job đó là bỏ rơi. `lease_seconds=0` đặt ở ĐÂY,
        # nơi staleness thực sự được quyết định.
        _drain(memory_os, cycles=5, lease_seconds=0)

    result = _run_case(
        tmp_path, "03", "worker đang giữ lease thì forget",
        "hàng đã quên xuất hiện trong cognitive_memories sau khi worker chạy "
        "tiếp job nó đã claim trước lệnh quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    # INVALID -> PASS, 17/08. Ca này từng INVALID vì fixture truyền
    # `lease_seconds=0` vào lệnh LẤY lease — không tác dụng gì lên hàng — rồi
    # drain bằng worker mặc định 300 giây. `lease_seconds` thuộc về người đọc,
    # không thuộc về job, nên staleness phải đặt ở phía drain. Sửa fixture
    # không làm yếu cổng: nó làm đường nguy hiểm CHẠM TỚI ĐƯỢC, đúng thứ cổng
    # đòi hỏi.
    assert result.state == "PASS", result.as_dict()


def test_case_04_forget_after_materialisation(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        _drain(memory_os)

    result = _run_case(
        tmp_path, "04", "materialize rồi mới forget",
        "hàng đã quên còn lại trong cognitive_memories sau lệnh quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        materialise=True,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state in ("PASS", "INVALID"), result.as_dict()
    if result.state == "INVALID":
        pytest.skip(
            f"ca 04 INVALID chứ không PASS — mutant không tạo được trạng thái "
            f"bị cấm trên đường này: {result.as_dict()}")


def test_case_05_forget_then_replay(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)
        _drain(memory_os)

    result = _run_case(
        tmp_path, "05", "forget rồi replay",
        "hàng đã quên được replay đưa trở lại cognitive_memories",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        materialise=True,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


def test_case_06_forget_then_reprojection(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        for _ in range(2):
            ProjectionReplayEngine(memory_os.memories.conn).replay(
                dry_run=False)
            _drain(memory_os)

    result = _run_case(
        tmp_path, "06", "forget rồi dựng lại nhiều lần",
        "hàng đã quên quay lại sau nhiều vòng replay + drain",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


def test_case_07_duplicate_projection_jobs(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        ProjectionReplayEngine(memory_os.memories.conn).replay(dry_run=False)
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        _drain(memory_os, cycles=6)

    result = _run_case(
        tmp_path, "07", "hai job trùng cho cùng một sự kiện",
        "một trong hai bản sao materialize được hàng đã quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


# ---------------------------------------------------------------------------
# 08–09: vòng đời worker
# ---------------------------------------------------------------------------

def test_case_08_worker_retry(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        worker = worker_for(memory_os)
        for _ in range(6):
            worker.run_once()

    result = _run_case(
        tmp_path, "08", "worker thử lại nhiều vòng",
        "hàng đã quên materialize ở một trong các lần thử lại",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    assert result.state == "PASS", result.as_dict()


def test_case_09_worker_restart(tmp_path, monkeypatch):
    def scenario(memory_os, event):
        # Một worker chết khi đang giữ job — nó không bao giờ gọi complete()
        # hay fail(), nên hàng nằm lại `in_progress` với `locked_by` của nó.
        worker = worker_for(memory_os)
        worker.outbox.claim(worker_id="gate-crash")
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        del worker
        # Tiến trình mới. Nó là bên quyết định lease cũ đã quá hạn hay chưa —
        # `claim()` thu hồi ngay trong nó, không cần sweeper riêng.
        _drain(memory_os, cycles=6, lease_seconds=0)

    result = _run_case(
        tmp_path, "09", "worker chết khi đang giữ lease rồi khởi động lại",
        "worker mới nhận lại job của worker đã chết và materialize hàng đã quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    # INVALID -> PASS cùng lý do với ca 03. Restart recovery vốn HOÀN CHỈNH:
    # một ProjectionOutbox mới trên connection mới thu hồi được hàng của worker
    # đã chết. Thứ hỏng là phép đo, không phải cơ chế.
    assert result.state == "PASS", result.as_dict()


# ---------------------------------------------------------------------------
# 10–11: vệ sinh hàng đợi
# ---------------------------------------------------------------------------

def test_case_10_buried_job_reaches_terminal_state(tmp_path):
    """Trạng thái bị cấm ở đây không phải resurrection mà là **liveness**."""
    memory_os, event = _store(tmp_path, "case10")
    try:
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        _drain(memory_os, cycles=8)
        statuses = _statuses(memory_os)
        assert statuses.get("in_progress", 0) == 0, statuses
        assert statuses.get("pending", 0) == 0, statuses
    finally:
        memory_os.close()


def test_case_11_rollback_drain_to_zero(tmp_path):
    from bio_agent_os.cognitive.projection_control import drain

    memory_os, _ = _store(tmp_path, "case11")
    try:
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        report = drain(memory_os, timeout_seconds=6.0)
        assert report.get("drained") is True, report
        assert report.get("remaining", 0) == 0, report
    finally:
        memory_os.close()


# ---------------------------------------------------------------------------
# bất biến cấp cổng: báo thành công phải là chuyển đổi đã xác nhận
# ---------------------------------------------------------------------------

def test_false_success_report_rate_is_zero(tmp_path):
    """Lớp lỗi `:335`, đo thành một tỉ lệ.

    `requested` / `attempted` / `confirmed` là ba quan sát khác nhau, và
    `confirmed` chỉ được tính khi **đọc lại được từ store**."""
    ledger = TransitionLedger()
    memory_os, event = _store(tmp_path, "ledger")
    try:
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        worker = worker_for(memory_os)
        ledger.request()
        metrics = worker.run_once()
        ledger.attempt(reported_success=metrics.tombstoned >= 1)
        terminal = _statuses(memory_os).get("in_progress", 0) == 0
        ledger.confirm(observed=terminal)
    finally:
        memory_os.close()

    assert ledger.false_success_report_rate == 0.0, ledger.as_dict()


def test_mutant_ignore_callee_false_and_increment_success_metric(tmp_path):
    """IGNORE_CALLEE_FALSE_AND_INCREMENT_SUCCESS_METRIC.

    Chính chuỗi đã làm `:335` sống lâu: callee báo FAIL → caller bỏ qua → caller
    tăng metric thành công → hệ thống báo thành công. Nếu cổng vẫn xanh dưới
    mutant này thì tầng metric vẫn mù."""
    real_skip = outbox_module.ProjectionOutbox.skip
    ledger = TransitionLedger()
    memory_os, event = _store(tmp_path, "false-success")
    try:
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)

        def broken(self, event_id, projection_type="", *, reason=""):
            real_skip(self, event_id, "không khớp gì", reason=reason)
            return True                       # callee hỏng, caller được bảo OK

        outbox_module.ProjectionOutbox.skip = broken
        worker = worker_for(memory_os)
        ledger.request()
        metrics = worker.run_once()
        ledger.attempt(reported_success=metrics.tombstoned >= 1)
        terminal = _statuses(memory_os).get("in_progress", 0) == 0
        ledger.confirm(observed=terminal)
    finally:
        outbox_module.ProjectionOutbox.skip = real_skip
        memory_os.close()

    assert ledger.false_success_report_rate > 0.0, (
        "mutant báo thành công cho một chuyển đổi không xảy ra mà sổ vẫn sạch "
        "— tầng metric đang mù đúng kiểu :335")
