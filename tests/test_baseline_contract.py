"""Một baseline thiếu provenance thì không phải baseline.

`31/38` được dùng làm baseline suốt nhiều tuần. Nó không phải. Artifact giữ nó
ghi `git_sha 04911707` trong khi HEAD đã đi 23 commit — 12 trong đó đụng vào
operator, state machine, lifecycle, relations và resolver. Mọi bảng before/after
in ra từ đó đã gán delta của 23 commit cho bất cứ thứ gì vừa được sửa trong giờ
đó, và ba bản báo cáo đã được viết trên những con số ấy, một cách rất tự tin.

    STALE BASELINE != CAUSAL DELTA

Điều bộ test này bảo vệ không phải "so sánh cho đúng" mà là **từ chối cho đúng**.
Một cảnh báo in phía trên một bảng delta sẽ được đọc như chú thích cho những con
số vốn đã được tin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_agent_os.evals.baseline_contract import (
    CONTRACT_VERSION, Baseline, GateInvalid, compare, revision)


def _baseline(**over) -> Baseline:
    base = Baseline(
        baseline_id=over.pop("baseline_id", "B0"),
        revision={"git_sha": "a" * 40, "git_tree_hash": "t" * 40,
                  "git_dirty": False, "git_dirty_files": []},
        dataset_hash="d1", question_hash="q1", config_hash="c1",
        lifecycle_mode="off", feature_flags={"SUBJECT_IDENTITY_READ": "on"},
        model="gpt-4o-mini", temperature=0.0,
        eval_harness_version="lifetime@1",
        families={"ever": {"asked": 40, "correct": 40},
                  "historical": {"asked": 38, "correct": 31}},
    )
    for key, value in over.items():
        setattr(base, key, value)
    return base


def test_matching_runs_produce_a_delta_table():
    after = _baseline()
    after.families = {"ever": {"asked": 40, "correct": 40},
                      "historical": {"asked": 38, "correct": 32}}
    result = compare(_baseline(), after)
    assert result.valid
    assert result.families["historical"]["delta"] == 1


def test_a_moved_commit_invalidates_the_comparison():
    """Chính xác lỗi đã xảy ra: artifact ghi một commit, checkout là commit
    khác, và bảng delta vẫn được in ra."""
    after = _baseline(revision={"git_sha": "b" * 40, "git_tree_hash": "t" * 40,
                                "git_dirty": False, "git_dirty_files": []})
    result = compare(_baseline(), after)
    assert not result.valid
    assert any("git_sha" in r for r in result.reasons)
    assert result.families == {}, "INVALID mà vẫn có bảng delta"


def test_invalid_raises_instead_of_warning():
    after = _baseline(revision={"git_sha": "b" * 40, "git_tree_hash": "t" * 40,
                                "git_dirty": False, "git_dirty_files": []})
    with pytest.raises(GateInvalid):
        compare(_baseline(), after).require_valid()


def test_the_field_under_test_may_be_allowed_to_differ():
    """A/B thật: commit khác nhau là *mục đích*, không phải lỗi. Nhưng chỉ
    trường được nêu tên mới được phép khác."""
    after = _baseline(revision={"git_sha": "b" * 40, "git_tree_hash": "u" * 40,
                                "git_dirty": False, "git_dirty_files": []})
    assert compare(_baseline(), after,
                   allow=("git_sha", "git_tree_hash")).valid


@pytest.mark.parametrize("field,value", [
    ("dataset_hash", "d2"), ("question_hash", "q2"), ("config_hash", "c2"),
    ("lifecycle_mode", "on"), ("model", "gpt-4o"), ("temperature", 0.7),
    ("eval_harness_version", "lifetime@2"),
    ("feature_flags", {"SUBJECT_IDENTITY_READ": "off"}),
])
def test_every_identity_field_can_invalidate(field, value):
    """Không trường nào trong hợp đồng là trang trí."""
    after = _baseline(**{field: value})
    result = compare(_baseline(), after)
    assert not result.valid, f"{field} đổi mà vẫn hợp lệ"
    assert any(field in r for r in result.reasons)


def test_a_dirty_tree_cannot_be_a_candidate():
    """Đo #7 dưới working tree bẩn là cách nó lẫn với mọi thứ khác đang sửa
    dở."""
    after = _baseline(revision={"git_sha": "a" * 40, "git_tree_hash": "t" * 40,
                                "git_dirty": True,
                                "git_dirty_files": ["x.py"]})
    result = compare(_baseline(), after)
    assert not result.valid
    assert any("bẩn" in r for r in result.reasons)


def test_a_different_question_count_is_a_different_benchmark():
    after = _baseline()
    after.families = {"ever": {"asked": 40, "correct": 40},
                      "historical": {"asked": 30, "correct": 31}}
    result = compare(_baseline(), after)
    assert not result.valid
    assert result.families == {}


def test_an_artifact_is_never_overwritten(tmp_path: Path):
    """Baseline là lịch sử, không phải một tên file. Ghi đè "the baseline" là
    cách đánh mất chính phần chứng minh được điều gì đã đổi."""
    written = _baseline().write(tmp_path)
    assert written.name == "B0_ARTIFACT.json"
    with pytest.raises(FileExistsError):
        _baseline().write(tmp_path)
    _baseline(baseline_id="B1").write(tmp_path)


def test_a_written_artifact_round_trips(tmp_path: Path):
    original = _baseline()
    restored = Baseline.read(original.write(tmp_path))
    assert restored.identity == original.identity
    assert restored.families == original.families


def test_the_contract_version_is_part_of_identity():
    after = _baseline(contract_version="baseline-contract@99")
    assert not compare(_baseline(), after).valid


def test_revision_reads_the_real_repository():
    """Hợp đồng phải đọc được kho thật, không chỉ dữ liệu bịa trong test."""
    info = revision(Path(__file__).resolve().parents[1])
    assert len(info["git_sha"]) == 40
    assert len(info["git_tree_hash"]) == 40
    assert isinstance(info["git_dirty"], bool)


def test_contract_version_is_declared():
    assert CONTRACT_VERSION.startswith("baseline-contract@")
