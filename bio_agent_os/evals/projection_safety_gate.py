"""Một cổng an toàn, không phải một bảng điểm.

Không có điểm tổng. Không có "% an toàn". Mỗi năng lực chỉ được là một trong
bốn trạng thái, và ba trong bốn không phải là "hỏng":

    PASS            đường nguy hiểm chạy được, bản sạch chặn nó, mutant kích
                    hoạt được trạng thái bị cấm
    FAIL            trạng thái bị cấm xảy ra trên bản sạch
    INVALID         mutant KHÔNG kích hoạt được trạng thái bị cấm — nên "bản
                    sạch chặn được" chưa chứng minh gì
    NOT_EVALUABLE   đường production chạm không tới được

`INVALID` và `NOT_EVALUABLE` tồn tại riêng vì chúng đã cứu dự án này hai lần.
Một probe forget quá rộng báo "0 resurrection" trong khi không còn gì để hồi
sinh; một probe replay báo sạch trong khi không có consumer nào chạy. Cả hai
trông y hệt PASS.

    UNREACHABLE SAFE PATH != VERIFIED SAFETY

Bốn điều kiện, và PASS đòi **cả bốn**:

    CONTROL_EXECUTED                  đường bình thường có chạy
    DANGEROUS_PATH_EXECUTED           kịch bản nguy hiểm thật sự diễn ra
    MUTANT_TRIGGERED_PROHIBITED_STATE mutant tạo được trạng thái bị cấm
    CLEAN_PREVENTED_PROHIBITED_STATE  bản sạch ngăn được nó

**Trạng thái bị cấm phải được phát biểu bằng persistent state**, không phải
bằng `expected=True/False`. Control flow kể chuyện; persistent state mới ký
biên bản.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

GATE_VERSION = "projection-safety-gate@1"

PASS, FAIL, INVALID, NOT_EVALUABLE = (
    "PASS", "FAIL", "INVALID", "NOT_EVALUABLE")


@dataclass
class CaseResult:
    """Một năng lực, và bằng chứng cho nó."""

    case_id: str
    name: str
    #: Phát biểu bằng persistent state, ví dụ "hàng đã quên xuất hiện lại trong
    #: cognitive_memories sau khi worker chạy tiếp".
    prohibited_state: str

    control_executed: bool = False
    dangerous_path_executed: bool = False
    mutant_triggered: bool = False
    clean_prevented: bool = False

    #: Đọc lại từ store, không phải giá trị trả về của hàm.
    readback: dict = field(default_factory=dict)
    mutant_name: str = ""
    note: str = ""

    @property
    def state(self) -> str:
        if not self.dangerous_path_executed:
            return NOT_EVALUABLE
        if not self.control_executed:
            # Đường bình thường không chạy thì "bản sạch chặn được" có thể chỉ
            # là "không có gì để chặn".
            return INVALID
        if not self.mutant_triggered:
            return INVALID
        return PASS if self.clean_prevented else FAIL

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "name": self.name, "state": self.state,
            "prohibited_state": self.prohibited_state,
            "mutant": self.mutant_name,
            "conditions": {
                "CONTROL_EXECUTED": self.control_executed,
                "DANGEROUS_PATH_EXECUTED": self.dangerous_path_executed,
                "MUTANT_TRIGGERED_PROHIBITED_STATE": self.mutant_triggered,
                "CLEAN_PREVENTED_PROHIBITED_STATE": self.clean_prevented,
            },
            "readback": self.readback,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# reported success vs confirmed transition
# --------------------------------------------------------------------------

@dataclass
class TransitionLedger:
    """`requested` / `attempted` / `confirmed` — ba quan sát khác nhau.

    `:335` sống được không nhờ đối số sai mà nhờ chuỗi này:

        callee báo FAIL -> caller bỏ qua kết quả -> caller tăng metric thành
        công -> hệ thống báo thành công

    Nên một chuyển đổi chỉ được tính `confirmed` khi **đọc lại được từ store**.
    Một hàm trả về truthy là `attempted`, không phải `confirmed`.
    """

    requested: int = 0
    attempted: int = 0
    confirmed: int = 0
    #: Lần nào báo thành công mà đọc lại không thấy.
    false_success: int = 0

    def request(self) -> None:
        self.requested += 1

    def attempt(self, *, reported_success: bool) -> None:
        self.attempted += 1
        self._last_reported = bool(reported_success)

    def confirm(self, *, observed: bool) -> None:
        """`observed` phải đến từ một lần đọc lại store."""
        if observed:
            self.confirmed += 1
        elif getattr(self, "_last_reported", False):
            self.false_success += 1

    @property
    def false_success_report_rate(self) -> float:
        """Bao nhiêu lần báo thành công mà state không đổi. Mục tiêu: 0."""
        reported = self.attempted
        return round(self.false_success / reported, 4) if reported else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "attempted": self.attempted,
                "confirmed": self.confirmed,
                "false_success": self.false_success,
                "false_success_report_rate": self.false_success_report_rate}


# --------------------------------------------------------------------------
# artifact identity
# --------------------------------------------------------------------------

def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()


def fingerprint_source(*paths: Path) -> str:
    """Hash nội dung fixture và bộ mutant.

    Cùng lý do `eval_harness_version` tồn tại: "cùng một file" không phải cùng
    một khẳng định với "cùng một byte", và một kết luận an toàn đọc từ report
    cũ trong khi mutant đã đổi là một kết luận về một hệ thống khác.
    """
    sha = hashlib.sha256()
    for path in sorted(paths):
        sha.update(path.name.encode("utf-8"))
        sha.update(path.read_bytes() if path.exists() else b"<missing>")
    return sha.hexdigest()


@dataclass
class GateArtifact:
    gate_version: str = GATE_VERSION
    taken_at: str = ""
    revision: dict = field(default_factory=dict)
    runtime_fingerprint: str = ""
    schema_version: str = ""
    worker_version: str = ""
    fixture_hash: str = ""
    mutant_set_hash: str = ""
    cases: list = field(default_factory=list)
    transitions: dict = field(default_factory=dict)

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "gate_version": self.gate_version,
            "git_sha": self.revision.get("git_sha"),
            "git_tree_hash": self.revision.get("git_tree_hash"),
            "git_dirty": self.revision.get("git_dirty"),
            "runtime_fingerprint": self.runtime_fingerprint,
            "schema_version": self.schema_version,
            "worker_version": self.worker_version,
            "fixture_hash": self.fixture_hash,
            "mutant_set_hash": self.mutant_set_hash,
            "case_ids": sorted(c.get("case_id", "") for c in self.cases),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**{k: getattr(self, k) for k in self.__dataclass_fields__},
                "identity_hash": _digest(self.identity),
                "summary": self.summary}

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for case in self.cases:
            counts[case["state"]] = counts.get(case["state"], 0) + 1
        return counts

    def write(self, directory: Path, gate_id: str) -> Path:
        """Bất biến, như baseline. Một cổng an toàn bị ghi đè là một cổng mất
        lịch sử, và lịch sử là phần chứng minh được điều gì đã đổi."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{gate_id}_SAFETY_GATE.json"
        if target.exists():
            raise FileExistsError(
                f"{target} đã tồn tại. Cổng an toàn là lịch sử — dùng id kế "
                f"tiếp thay vì ghi đè.")
        target.write_text(json.dumps(self.as_dict(), ensure_ascii=False,
                                     indent=2, default=str), encoding="utf-8")
        return target


def read_conclusion(artifact: GateArtifact, current: dict[str, Any]) -> str:
    """Có được phép rút kết luận an toàn từ artifact này không?

    Lệch identity thì INVALID, không cảnh báo. Một cảnh báo in phía trên một
    kết luận an toàn sẽ được đọc như chú thích cho một kết luận vốn đã được
    tin.
    """
    for key, value in artifact.identity.items():
        if key in current and current[key] != value:
            return (f"INVALID — {key} lệch: artifact {value!r}, "
                    f"hiện tại {current[key]!r}")
    if artifact.revision.get("git_dirty"):
        return "INVALID — cổng chạy trên working tree bẩn"
    return "VALID"


__all__ = ["CaseResult", "GATE_VERSION", "GateArtifact", "TransitionLedger",
           "FAIL", "INVALID", "NOT_EVALUABLE", "PASS", "fingerprint_source",
           "read_conclusion"]
