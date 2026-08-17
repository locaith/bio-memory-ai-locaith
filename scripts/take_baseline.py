"""Take an immutable baseline, from a clean tree, with no expected score.

    python scripts/take_baseline.py --id B0

Nothing about what the numbers *should* be enters this script. The last
baseline was believed for weeks partly because every report quoted the number
the previous report expected; a runner that is told what to expect is a runner
that can confirm it.

It refuses a dirty tree rather than recording one. A baseline measured on
uncommitted work names a revision that never existed and cannot be returned to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from bio_agent_os.evals.baseline_contract import (            # noqa: E402
    CONTRACT_VERSION, Baseline, revision)

#: Where baselines live. One file per baseline, never overwritten.
BASELINES = _REPO / "benchmark_reports" / "baselines"


def _blob_hash(*paths: Path) -> str:
    """Content hash of the evaluator itself.

    An A/B is only 'same evaluator' if the evaluator did not move between the
    two runs, and 'it is the same file' is not the same claim as 'it is the
    same bytes'.
    """
    sha = hashlib.sha256()
    for path in sorted(paths):
        sha.update(path.name.encode("utf-8"))
        sha.update(path.read_bytes() if path.exists() else b"<missing>")
    return sha.hexdigest()


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                     sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(prog="take_baseline")
    ap.add_argument("--id", required=True,
                    help="B0, B1, … Never reuses an existing id.")
    ap.add_argument("--seed", type=int, default=20260814)
    ap.add_argument("--per-family", type=int, default=6)
    ap.add_argument("--ticks", type=int, default=1000)
    ap.add_argument("--subjects", type=int, default=20)
    ap.add_argument("--allow-dirty", action="store_true",
                    help="Record a dirty tree anyway. The artifact carries "
                         "git_dirty=true and `compare` will refuse it — this "
                         "exists for debugging the runner, not for taking a "
                         "baseline.")
    args = ap.parse_args()

    rev = revision(_REPO)
    print("REVISION")
    print(f"  git   : {rev['git_short']}  ({rev['git_branch']})")
    print(f"  tree  : {rev['git_tree_hash'][:16]}…")
    print(f"  dirty : {rev['git_dirty']}")
    if rev["git_dirty"] and not args.allow_dirty:
        print("\n  DỪNG: cây bẩn. Một baseline đo trên việc chưa commit đặt tên "
              "cho một revision chưa từng tồn tại và không quay lại được.")
        for name in rev["git_dirty_files"]:
            print(f"    {name}")
        return 2

    target = BASELINES / f"{args.id}_ARTIFACT.json"
    if target.exists():
        print(f"\n  DỪNG: {target.name} đã tồn tại. Baseline là lịch sử, "
              f"không phải một tên file bị ghi đè — dùng id kế tiếp.")
        return 2

    from scripts.integration_gate import run_now                # noqa: E402
    from bio_agent_os.cognitive import temporal_operator as T    # noqa: E402
    from bio_agent_os.core.embedder import Embedder              # noqa: E402
    from bio_agent_os.core.llm_engine import LLMEngine           # noqa: E402
    from bio_agent_os.core.provenance import identity            # noqa: E402
    from bio_agent_os.evals.lifetime_world import generate       # noqa: E402

    engine = LLMEngine.from_env()
    events, ledger, people = generate(ticks=args.ticks,
                                      subjects=args.subjects, seed=args.seed)
    workdir = _REPO / ".staging" / f"baseline_{args.id}"
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"\nĐo {args.id} — không truyền điểm kỳ vọng nào vào runner.")
    started = time.perf_counter()
    rows, execution = run_now(events, ledger, people, Embedder(), engine,
                              args.seed, args.per_family, workdir)
    seconds = time.perf_counter() - started

    families: dict[str, dict[str, int]] = {}
    for row in rows.values():
        bucket = families.setdefault(row["family"], {"asked": 0, "correct": 0})
        bucket["asked"] += 1
        bucket["correct"] += 1 if row["correct"] else 0

    # Conflict identity from the EXECUTION layer, never from the rendered
    # answer. B0 derived it by looking for a Vietnamese word in `answer` and
    # returned an empty list — which cannot be told apart from "there were no
    # conflicts", and is therefore worth nothing either way. A synthesis step
    # is free to reword an answer without anything having changed underneath.
    conflict_claims = dict(T.CONFLICT_CASES)
    conflict_keys = sorted(conflict_claims)

    who = identity()
    baseline = Baseline(
        baseline_id=args.id,
        contract_version=CONTRACT_VERSION,
        taken_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        revision=rev,
        dataset_hash=_digest({"ticks": args.ticks, "subjects": args.subjects,
                              "seed": args.seed,
                              "events": len(events)}),
        question_hash=_digest(sorted(rows)),
        config_hash=_digest({"per_family": args.per_family,
                             "seed": args.seed, "lifecycle_mode": "safe"}),
        runtime_fingerprint=who.fingerprint,
        lifecycle_mode="safe",
        feature_flags={"SUBJECT_IDENTITY_READ": T._SUBJECT_IDENTITY_READ},
        model=getattr(engine, "model", "") or "",
        temperature=float(getattr(engine, "temperature", 0.0) or 0.0),
        eval_harness_version=_blob_hash(
            _REPO / "scripts" / "integration_gate.py",
            _REPO / "bio_agent_os" / "evals" / "lifetime_world.py",
            _REPO / "bio_agent_os" / "evals" / "lifetime_questions.py",
            _REPO / "bio_agent_os" / "evals" / "lifetime_adapter.py"),
        families=families,
        # Both sources. `run_now` reports its own routing stats; the operator
        # reports which paths ran inside it. B0 recorded only the first and so
        # carried four counters where the gate prints fifteen — a contract that
        # names fewer paths than exist is a contract several paths can change
        # underneath.
        execution_contract={**dict(execution), **T.execution_report()},
        conflict_case_ids=conflict_keys,
        conflict_claim_sets=conflict_claims,
    )

    print("\nĐO ĐƯỢC — số này trở thành baseline, không kéo từ báo cáo cũ")
    for family in sorted(families):
        got = families[family]
        print(f"  {family:<12} {got['correct']}/{got['asked']}")
    print(f"\n  conflict cases : {len(conflict_keys)}")
    print(f"  evaluator hash : {baseline.eval_harness_version[:16]}…")
    print(f"  seconds        : {seconds:.1f}")

    written = baseline.write(BASELINES)
    rows_path = BASELINES / f"{args.id}_ROWS.json"
    rows_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"\n  -> {written}")
    print(f"  -> {rows_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
