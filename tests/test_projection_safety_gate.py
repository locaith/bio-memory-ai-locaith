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


def _drain(memory_os, cycles: int = 4):
    worker = worker_for(memory_os)
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
        # `lease_seconds=0`: job được claim rồi lease hết hạn ngay, nên vòng
        # sau claim lại được. Không có nó, job nằm khoá trong cửa sổ lease và
        # đường nguy hiểm chạm không tới — ca này trả về INVALID chứ không
        # phải PASS, và INVALID là câu trả lời đúng cho một phép đo chưa chạy.
        worker = worker_for(memory_os)
        assert worker.outbox.claim(worker_id="gate-lease", lease_seconds=0), (
            "không claim được job — ca này chưa đo được gì")
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        _drain(memory_os, cycles=5)

    result = _run_case(
        tmp_path, "03", "worker đang giữ lease thì forget",
        "hàng đã quên xuất hiện trong cognitive_memories sau khi worker chạy "
        "tiếp job nó đã claim trước lệnh quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    # **INVALID, và đó là câu trả lời đúng.** Đo được: worker không xử lý job
    # đã bị claim lần nào (`tombstoned=0, completed=0` qua năm vòng), kể cả với
    # `lease_seconds=0`. Mutant chạm không tới trạng thái bị cấm, nên "bản sạch
    # chặn được" chưa chứng minh gì cho ca này.
    #
    # `P0-A test_c_forget_while_worker_holds_the_lease` vẫn phủ hành vi an toàn
    # ở đây; thứ chưa có là **nhân chứng** rằng nó có tải.
    #
    # Đây là một cái chuông: ngày ai đó làm job re-claimable trong ca này, nó
    # thành PASS và assertion dưới đây đỏ lên. Lúc đó đổi thành PASS.
    assert result.state == "INVALID", (
        f"ca 03 đã đo được — cập nhật assertion thành PASS: {result.as_dict()}")


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
        # Một worker chết khi đang giữ job. Lease hết hạn là cách hệ thống
        # nhận ra điều đó — `claim()` thu hồi lease quá hạn ngay trong nó, chứ
        # không cần một sweeper riêng. Nên `lease_seconds=0` mô phỏng đúng cái
        # chết đó, và là thứ làm cho worker mới nhận lại được job.
        worker = worker_for(memory_os)
        worker.outbox.claim(worker_id="gate-crash", lease_seconds=0)
        forgetting.forget_derived(memory_os, subject=SUBJECT, needle=NEEDLE)
        del worker
        _drain(memory_os, cycles=6)                    # tiến trình mới

    result = _run_case(
        tmp_path, "09", "worker chết khi đang giữ lease rồi khởi động lại",
        "worker mới nhận lại job của worker đã chết và materialize hàng đã quên",
        scenario=scenario, mutant=_blind_burial, monkeypatch=monkeypatch,
        mutant_name="IGNORE_RECONCILIATION_WORKER_BURIED_CHECK")
    # INVALID cùng lý do với ca 03: một job đã bị claim không được worker mới
    # nhận lại trong kịch bản này, nên đường nguy hiểm chạm không tới. Chuông
    # giống hệt — ngày nó thành PASS, assertion này đỏ.
    assert result.state == "INVALID", (
        f"ca 09 đã đo được — cập nhật assertion thành PASS: {result.as_dict()}")


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
