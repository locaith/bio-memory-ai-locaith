"""The gate Run 9 has to pass through, run as one command.

Run 7 started because I decided it was ready. That is the wrong kind of
authority for a twenty-four hour measurement: it makes "ready" a memory rather
than a check, and a memory does not notice a dirty working tree at 23:50.

So the decision moves into a script. It refuses on a dirty tree, refuses on a
red suite, refuses on a scope mismatch, refuses without a recent stress verdict,
and writes the pin — commit, runtime tree, harness digest, config digest —
before anything starts. Exit 0 means Run 9 may begin; anything else means it
may not.

It has already earned its keep twice. Before Run 8 it caught two flaky WAL tests
and a required suite whose filename did not exist, so had never run at all.

    python scripts/run9_gate.py            # check, print, exit 0/1
    python scripts/run9_gate.py --json     # same, machine readable
    python scripts/run9_gate.py --write-pin PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
#: Files whose content defines the harness. A change to any of them invalidates
#: a pin, because the thing doing the measuring is part of the measurement.
HARNESS_FILES = ("scripts/canary_supervisor.py", "scripts/staging_canary.py")
#: Suites that must be green. Named individually rather than "the whole suite"
#: so that a gate failure says which property was lost.
REQUIRED_SUITES = (
    ("full suite", []),
    ("wal state machine", ["tests/test_wal_state_machine.py"]),
    ("wal management", ["tests/test_wal_management.py"]),
    ("scope identity", ["tests/test_scope_identity.py"]),
    ("shadow", ["tests/test_shadow_mode.py"]),
    ("projection outbox", ["tests/test_projection_outbox.py"]),
    ("doctor", ["tests/test_doctor_reconcile.py"]),
    # Added for Run 9. The doctor was the reader that pinned the log in Run 8,
    # so the property that it now slices is a gate condition, not a nicety.
    ("doctor bounded snapshots", ["tests/test_doctor_bounded_snapshots.py"]),
    ("incremental doctor", ["tests/test_incremental_doctor.py"]),
    # Section 16 names these two and the Run 8 gate never ran either. A suite
    # that is required and not executed is a gate condition in name only.
    ("fault matrix", ["tests/test_fault_matrix.py"]),
)

#: The sibling project. It consumes this package through `locaith_os.memory`,
#: so a change here that breaks it is a break, and Run 8's gate could not see
#: it because it only ever looked at one repository.
LOCAITH_OS_REPO = Path(r"C:\locaith\Final Platform Agent AI OS Intelligent Tuan Anh")

#: The stress rehearsal from section 11. Its verdict is read, not re-run — a
#: thirty-minute test inside a gate makes the gate something nobody runs.
TORTURE_REPORT = "reports/v082/wal_torture_gate.json"
#: How old that verdict may be before it stops describing this code.
TORTURE_MAX_AGE_HOURS = 24.0


def _run(cmd: list[str], cwd: Path = REPO) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _git(*args: str) -> str:
    code, out = _run(["git", *args])
    return out.strip() if code == 0 else ""


def _digest(paths: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for rel in sorted(paths):
        f = REPO / rel
        h.update(rel.encode("utf-8"))
        h.update(f.read_bytes() if f.exists() else b"<missing>")
    return h.hexdigest()[:16]


class Gate:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str, blocking: bool = True) -> bool:
        self.checks.append({"name": name, "ok": ok, "detail": detail,
                            "blocking": blocking})
        return ok

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks if c["blocking"])

    def render(self) -> str:
        lines = ["=" * 74, "  RUN 9 GATE", "=" * 74]
        for c in self.checks:
            mark = "PASS" if c["ok"] else ("FAIL" if c["blocking"] else "warn")
            lines.append(f"  [{mark:>4}] {c['name']}")
            for line in c["detail"].splitlines():
                lines.append(f"         {line}")
        lines.append("=" * 74)
        lines.append("  RUN 9 MAY START" if self.passed else "  BLOCKED — Run 9 must not start")
        lines.append("=" * 74)
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run9_gate")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-pin", metavar="PATH", default=None)
    ap.add_argument("--skip-tests", action="store_true",
                    help="inspect the pin without spending five minutes on pytest")
    args = ap.parse_args(argv)
    g = Gate()

    # -- 1. the tree the measurement will actually run on --------------------
    dirty = _git("status", "--porcelain")
    g.check("working tree is clean", dirty == "",
            "clean" if dirty == "" else
            f"{len(dirty.splitlines())} modified path(s):\n" + dirty[:600])

    head = _git("rev-parse", "HEAD")
    runtime_tree = _git("rev-parse", "HEAD:bio_agent_os")
    harness_sha = _digest(HARNESS_FILES)
    config_sha = _digest(("scripts/staging_canary.py",))
    g.check("run identity resolvable", bool(head and runtime_tree),
            f"RUN9_HEAD          {head}\n"
            f"RUN9_RUNTIME_TREE  {runtime_tree}\n"
            f"HARNESS_SHA        {harness_sha}\n"
            f"CONFIG_SHA         {config_sha}")

    # -- 2. the properties Run 9 is meant to hold ----------------------------
    if args.skip_tests:
        g.check("test suites", True, "skipped by request (--skip-tests)", blocking=False)
    else:
        for label, paths in REQUIRED_SUITES:
            code, out = _run([sys.executable, "-m", "pytest", "-q", *paths])
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or ["no output"]
            g.check(f"tests: {label}", code == 0, tail[0][:300])

    # -- 3. every entry point in one partition -------------------------------
    try:
        from bio_agent_os.cognitive.scope import resolve_scope
        cli = resolve_scope()
        hook = resolve_scope(project_path=os.getcwd())
        same = cli.fingerprint == hook.fingerprint
        g.check("scope: entry points agree", same,
                f"cli  {cli.render()}\nhook {hook.render()}")
        g.check("scope: not an accidental fallback", not cli.is_fallback,
                f"tenant from {cli.tenant_source}, workspace from {cli.workspace_source}",
                blocking=False)
        g.check("scope: workspace is not a path", not cli.workspace_looks_like_a_path,
                f"workspace_id = {cli.workspace_id!r}")
    except Exception as exc:
        g.check("scope: entry points agree", False, f"{type(exc).__name__}: {exc}")

    # -- 3a. the sibling project still passes against this code --------------
    if not args.skip_tests:
        if (LOCAITH_OS_REPO / "tests").exists():
            code, out = _run([sys.executable, "-m", "pytest", "-q", "tests"],
                             cwd=LOCAITH_OS_REPO)
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or ["no output"]
            g.check("tests: Locaith OS", code == 0, tail[0][:300])
        else:
            g.check("tests: Locaith OS", True,
                    f"{LOCAITH_OS_REPO} not present on this machine", blocking=False)

    # -- 3b. the stress rehearsal actually passed, on this code --------------
    torture = REPO / TORTURE_REPORT
    if not torture.exists():
        g.check("wal torture gate", False,
                f"{TORTURE_REPORT} missing — run scripts/wal_torture_gate.py first")
    else:
        try:
            data = json.loads(torture.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {}
            g.check("wal torture gate", False, f"unreadable: {exc}")
        if data:
            age_h = (time.time() - torture.stat().st_mtime) / 3600
            hold = data.get("reader_hold_ms", {})
            failed = [c["name"] for c in data.get("checks", []) if not c.get("ok")]
            g.check("wal torture gate", bool(data.get("passed")) and not failed,
                    f"{data.get('minutes')} min, {data.get('writes', 0):,} writes, "
                    f"doctor every {data.get('doctor_every_seconds')}s\n"
                    f"reader hold p95 {hold.get('p95')} ms, max {hold.get('max')} ms\n"
                    + (f"failed: {', '.join(failed)}" if failed else "all checks passed"))
            g.check("torture verdict is recent", age_h <= TORTURE_MAX_AGE_HOURS,
                    f"{age_h:.1f} h old (limit {TORTURE_MAX_AGE_HOURS:.0f} h)")

    # -- 4. nothing from the previous runs was destroyed ---------------------
    staging = REPO / ".staging"
    kept = sorted(p.name for p in staging.glob("v082-canary-run*")) if staging.exists() else []
    g.check("runs 1-8 retained", len(kept) >= 8,
            f"{len(kept)} archived run(s): {', '.join(kept) or 'none'}")

    pin = {
        "RUN9_HEAD": head,
        "RUN9_RUNTIME_TREE": runtime_tree,
        "HARNESS_SHA": harness_sha,
        "CONFIG_SHA": config_sha,
        "working_tree_clean": dirty == "",
        "gate_passed": g.passed,
        "checks": g.checks,
    }
    if args.write_pin and g.passed:
        Path(args.write_pin).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write_pin).write_text(
            json.dumps(pin, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(pin, ensure_ascii=False, indent=2) if args.json else g.render())
    return 0 if g.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
