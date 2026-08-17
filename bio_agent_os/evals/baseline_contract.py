"""A baseline is not a number. It is a number *plus what produced it*.

`31/38` was treated as a baseline for weeks. It was not one. The artifacts
holding it recorded `git_sha 04911707` while HEAD had moved 23 commits — 12 of
them touching the operator, the state machine, the lifecycle, the relations and
the resolver. Every before/after table printed since attributed 23 commits'
worth of change to whatever had been edited that hour, and three separate
reports were written on those numbers, confidently.

The gate already carried the warning `WRONG BASELINE != PRODUCT REGRESSION`,
written after the last time this happened on a different axis. It was true and
nobody could act on it, because nothing checked whether the recorded revision
still matched the running one.

    STALE BASELINE != CAUSAL DELTA

So a baseline from here is:

    31/38
    at commit X
    on dataset Y
    with config Z
    through execution contract E
    using evaluator V

Missing any part, it is not a baseline, and this module refuses to pretend
otherwise. `compare()` returns INVALID and **no delta table** rather than a
warning above one: a warning printed above a delta table is read as a caveat on
numbers that are already being believed.

Artifacts are immutable and versioned — `B0`, `B1`, `B2`. "The baseline" as a
filename that gets overwritten is how a baseline loses its history, and the
history is the part that lets a later reader tell a fix from a story.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Bumped when the *contract* changes shape, not when a baseline is taken.
CONTRACT_VERSION = "baseline-contract@2"


# --------------------------------------------------------------------------
# revision
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(("git", "-C", str(repo)) + args,
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def revision(repo: Path) -> dict[str, Any]:
    """What is checked out, and whether it is honest about it.

    `git_tree_hash` rather than the commit alone: two runs at the same commit
    with different uncommitted edits are two different experiments, and only
    the tree hash notices. `git_dirty` is recorded separately so a dirty run
    can be *taken* and merely refused as a baseline, rather than being
    impossible to record at all.
    """
    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {
        "git_sha": _git(repo, "rev-parse", "HEAD"),
        "git_short": _git(repo, "rev-parse", "--short", "HEAD"),
        "git_tree_hash": _git(repo, "rev-parse", "HEAD^{tree}"),
        "git_dirty": bool(porcelain),
        "git_dirty_files": [l[3:] for l in porcelain.splitlines()][:40],
        "git_branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
    }


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# the artifact
# --------------------------------------------------------------------------

@dataclass
class Baseline:
    """One measurement, and everything needed to know it is the same one."""

    baseline_id: str = "B0"
    contract_version: str = CONTRACT_VERSION
    taken_at: str = ""

    revision: dict = field(default_factory=dict)
    #: Frozen inputs by content hash. A dataset that changed under a stable
    #: filename is the same failure as a moved commit.
    source_snapshot_hashes: dict = field(default_factory=dict)
    derived_db_hashes: dict = field(default_factory=dict)
    dataset_hash: str = ""
    question_hash: str = ""

    runtime_fingerprint: str = ""
    config_hash: str = ""
    lifecycle_mode: str = ""
    feature_flags: dict = field(default_factory=dict)
    model: str = ""
    temperature: float = 0.0
    eval_harness_version: str = ""

    #: `{family: {"asked": int, "correct": int}}`
    families: dict = field(default_factory=dict)
    #: Which paths ran. Two runs that score the same through different code are
    #: not the same result — a suite stayed green for a week while cosine was
    #: reinstated on 37 of 38 queries.
    execution_contract: dict = field(default_factory=dict)
    #: Identity, not counts. `conflict_detected 5 -> 5` hid a membership change
    #: completely; a count equal at both ends reads as "nothing moved".
    conflict_case_ids: list = field(default_factory=list)
    conflict_claim_sets: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def identity(self) -> dict[str, Any]:
        """The fields that must match for a comparison to mean anything."""
        return {
            "contract_version": self.contract_version,
            "git_sha": self.revision.get("git_sha"),
            "git_tree_hash": self.revision.get("git_tree_hash"),
            "dataset_hash": self.dataset_hash,
            "question_hash": self.question_hash,
            "config_hash": self.config_hash,
            "lifecycle_mode": self.lifecycle_mode,
            "feature_flags": self.feature_flags,
            "model": self.model,
            "temperature": self.temperature,
            "eval_harness_version": self.eval_harness_version,
        }

    def write(self, directory: Path) -> Path:
        """Immutable. A baseline that can be overwritten is a baseline whose
        history can be lost, and the history is the evidence."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{self.baseline_id}_ARTIFACT.json"
        if target.exists():
            raise FileExistsError(
                f"{target} đã tồn tại. Baseline là lịch sử, không phải một "
                f"tên file bị ghi đè — dùng baseline_id kế tiếp (B1, B2, …).")
        target.write_text(json.dumps(self.as_dict(), ensure_ascii=False,
                                     indent=2, default=str),
                          encoding="utf-8")
        return target

    @classmethod
    def read(cls, path: Path) -> "Baseline":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# --------------------------------------------------------------------------
# the refusal
# --------------------------------------------------------------------------

class GateInvalid(Exception):
    """Raised instead of printing a delta nobody may attribute."""


@dataclass
class Comparison:
    valid: bool
    reasons: list = field(default_factory=list)
    families: dict = field(default_factory=dict)

    def require_valid(self) -> None:
        if not self.valid:
            raise GateInvalid("GATE INVALID — " + "; ".join(self.reasons))


def compare(baseline: Baseline, candidate: Baseline, *,
            allow: tuple[str, ...] = ()) -> Comparison:
    """A delta table, or nothing.

    `allow` names the identity fields a run is *deliberately* varying — the
    one being tested. Comparing B0 against a candidate that differs only in
    `git_sha` is the whole point of an A/B; comparing against one that also
    changed the dataset is not a comparison at all.

    Everything not named in `allow` must match exactly. There is no partial
    credit and no warning-and-continue: a warning above a delta table is read
    as a footnote to numbers already believed.
    """
    reasons: list[str] = []
    base_id, cand_id = baseline.identity, candidate.identity
    for key in sorted(base_id):
        if key in allow:
            continue
        if base_id[key] != cand_id[key]:
            reasons.append(
                f"{key}: baseline {base_id[key]!r} != candidate {cand_id[key]!r}")

    if candidate.revision.get("git_dirty"):
        reasons.append("candidate đo trên working tree bẩn — "
                       f"{candidate.revision.get('git_dirty_files')}")
    if baseline.revision.get("git_dirty"):
        reasons.append("baseline được lấy trên working tree bẩn")

    if reasons:
        return Comparison(valid=False, reasons=reasons)

    families = {}
    for family in sorted(set(baseline.families) | set(candidate.families)):
        before = baseline.families.get(family, {})
        after = candidate.families.get(family, {})
        if before.get("asked") != after.get("asked"):
            return Comparison(
                valid=False,
                reasons=[f"{family}: hỏi {before.get('asked')} câu ở baseline "
                         f"và {after.get('asked')} ở candidate — khác bộ đề"])
        families[family] = {
            "asked": after.get("asked"),
            "before": before.get("correct"),
            "after": after.get("correct"),
            "delta": (after.get("correct", 0) - before.get("correct", 0)),
        }
    return Comparison(valid=True, families=families)


def ancestry_ok(repo: Path, baseline_sha: str, candidate_sha: str) -> bool:
    """Is the candidate exactly the baseline plus the change under test?

    `merge-base(B0, C1) == B0`. Without it, "B0 vs C1" can quietly mean "B0 vs
    B0 + 23 other commits + the change", which is the failure this module
    exists for.
    """
    if not baseline_sha or not candidate_sha:
        return False
    base = _git(repo, "merge-base", baseline_sha, candidate_sha)
    return bool(base) and base == baseline_sha


__all__ = ["Baseline", "Comparison", "CONTRACT_VERSION", "GateInvalid",
           "ancestry_ok", "compare", "revision"]
