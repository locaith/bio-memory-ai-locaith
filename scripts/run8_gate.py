"""The gate Run 8 has to pass through, run as one command.

Run 7 started because I decided it was ready. That is the wrong kind of
authority for a twenty-four hour measurement: it makes "ready" a memory rather
than a check, and a memory does not notice a dirty working tree at 23:50.

So the decision moves into a script. It refuses on a dirty tree, refuses on a
red suite, refuses on a scope mismatch, and writes the pin — commit, runtime
tree, harness digest, config digest — before anything starts. Exit 0 means Run 8
may begin; anything else means it may not.

    python scripts/run8_gate.py            # check, print, exit 0/1
    python scripts/run8_gate.py --json     # same, machine readable
    python scripts/run8_gate.py --write-pin PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
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
    ("shadow", ["tests/test_shadow_projection.py"]),
    ("doctor", ["tests/test_doctor_reconcile.py"]),
)


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
        lines = ["=" * 74, "  RUN 8 GATE", "=" * 74]
        for c in self.checks:
            mark = "PASS" if c["ok"] else ("FAIL" if c["blocking"] else "warn")
            lines.append(f"  [{mark:>4}] {c['name']}")
            for line in c["detail"].splitlines():
                lines.append(f"         {line}")
        lines.append("=" * 74)
        lines.append("  RUN 8 MAY START" if self.passed else "  BLOCKED — Run 8 must not start")
        lines.append("=" * 74)
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run8_gate")
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
            f"RUN8_HEAD          {head}\n"
            f"RUN8_RUNTIME_TREE  {runtime_tree}\n"
            f"HARNESS_SHA        {harness_sha}\n"
            f"CONFIG_SHA         {config_sha}")

    # -- 2. the properties Run 8 is meant to hold ----------------------------
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

    # -- 4. nothing from the previous runs was destroyed ---------------------
    staging = REPO / ".staging"
    kept = sorted(p.name for p in staging.glob("v082-canary-run*")) if staging.exists() else []
    g.check("runs 1-7 retained", len(kept) >= 7,
            f"{len(kept)} archived run(s): {', '.join(kept) or 'none'}")

    pin = {
        "RUN8_HEAD": head,
        "RUN8_RUNTIME_TREE": runtime_tree,
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
