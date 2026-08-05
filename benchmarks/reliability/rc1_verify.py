"""Everything RC1 claims, run in one pass and written down.

The point is not that each step passes — most were run individually while the
work was done. It is that they all pass *together*, on one tree, at one commit,
and that the record says which commit.

Steps that were not run are reported as `skipped`, never as passed. A release
that says "verified" because it did not look is worse than one that says
"partly verified".
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.reliability import environment  # noqa: E402

PYTHON = sys.executable
REPORTS = _REPO / "reports" / "v082"


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 3600,
         env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.time()
    import os as _os

    merged = {**_os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8",
              "PYTHONUTF8": "1"}
    out = subprocess.run(argv, cwd=str(cwd or _REPO), capture_output=True,
                         text=True, timeout=timeout, env=merged)
    return {
        "argv": argv,
        "exit_code": out.returncode,
        "seconds": round(time.time() - started, 2),
        "stdout_tail": out.stdout.strip().splitlines()[-6:],
        "stderr_tail": out.stderr.strip().splitlines()[-4:],
    }


def _pytest(args: list[str], label: str) -> dict[str, Any]:
    result = _run([PYTHON, "-m", "pytest", "-q", "--no-header", "--tb=line",
                   "-p", "no:cacheprovider", *args])
    summary = next(
        (line for line in reversed(result["stdout_tail"])
         if "passed" in line or "failed" in line or "error" in line),
        "",
    )
    result.update({"label": label, "summary": summary,
                   "passed": result["exit_code"] == 0})
    return result


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_and_install_wheel() -> dict[str, Any]:
    """Build a wheel, install it in a clean venv, and confirm legacy default.

    A wheel that imports is not the same as a wheel that behaves. The
    installed copy is asked what projection mode it defaults to, in a fresh
    interpreter with no environment variable set.
    """
    dist = Path(tempfile.mkdtemp(prefix="rc1_dist_"))
    build = _run([PYTHON, "-m", "build", "--wheel", "--outdir", str(dist)],
                 timeout=900)
    wheels = sorted(dist.glob("*.whl"))
    if build["exit_code"] != 0 or not wheels:
        return {"step": "wheel", "status": "failed", "build": build,
                "note": "wheel build failed; `pip install build` may be missing"}

    wheel = wheels[0]
    digest = sha256_of(wheel)
    venv = Path(tempfile.mkdtemp(prefix="rc1_venv_"))
    created = _run([PYTHON, "-m", "venv", str(venv)], timeout=600)
    py = venv / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python")
    install = _run([str(py), "-m", "pip", "install", "--quiet", str(wheel)],
                   timeout=1200)

    probe = (
        "import json, tempfile, os\n"
        "from bio_agent_os.cognitive.shadow import ProjectionMode, current_mode\n"
        "from bio_agent_os.cognitive.facade import MemoryOS\n"
        "import bio_agent_os\n"
        "d = tempfile.mkdtemp()\n"
        "r = MemoryOS(os.path.join(d, 'clean.db'))\n"
        "e = r.observe(tenant_id='t', actor='a', source='s', content='clean install')\n"
        "owed = len(r.events.outbox.by_event(e.event_id))\n"
        "print(json.dumps({'version': getattr(bio_agent_os, '__version__', '?'),\n"
        "                  'default_mode': current_mode().value,\n"
        "                  'is_legacy': current_mode() is ProjectionMode.LEGACY,\n"
        "                  'jobs_owed_by_default': owed}))\n"
    )
    probe_file = venv / "probe.py"
    probe_file.write_text(probe, encoding="utf-8")
    behaviour = _run([str(py), str(probe_file)], cwd=venv, timeout=600)

    parsed: dict[str, Any] = {}
    for line in behaviour["stdout_tail"]:
        try:
            parsed = json.loads(line)
            break
        except ValueError:
            continue

    ok = (
        install["exit_code"] == 0
        and behaviour["exit_code"] == 0
        and parsed.get("is_legacy") is True
        and parsed.get("jobs_owed_by_default") == 0
    )
    shutil.rmtree(venv, ignore_errors=True)
    return {
        "step": "wheel",
        "status": "passed" if ok else "failed",
        "wheel": wheel.name,
        "sha256": digest,
        "size_bytes": wheel.stat().st_size,
        "build_seconds": build["seconds"],
        "venv_created": created["exit_code"] == 0,
        "install_exit": install["exit_code"],
        "clean_install_behaviour": parsed,
        "legacy_is_default_in_clean_install": parsed.get("is_legacy"),
    }


def main() -> int:
    started = time.time()
    steps: list[dict[str, Any]] = []

    print("  1. Bio-Agent OS default suite", flush=True)
    steps.append(_pytest([], "bio_agent_os_default"))

    print("  2. fault matrix", flush=True)
    steps.append(_pytest(["tests/test_fault_matrix.py"], "fault_matrix"))

    print("  3. shadow mode", flush=True)
    steps.append(_pytest(["tests/test_shadow_mode.py"], "shadow_mode"))

    print("  4. doctor, incremental, control, WAL", flush=True)
    steps.append(_pytest(
        ["tests/test_doctor_reconcile.py", "tests/test_incremental_doctor.py",
         "tests/test_projection_control.py", "tests/test_wal_management.py"],
        "operations",
    ))

    print("  5. benchmark-marked tests", flush=True)
    steps.append(_pytest(["-m", "benchmark"], "benchmark_marked"))

    print("  6. single-commit fault verification", flush=True)
    single = _run([PYTHON, "-m", "benchmarks.reliability.verify_single_commit"],
                  timeout=1800)
    single["label"] = "single_commit_invariants"
    single["passed"] = single["exit_code"] == 0
    steps.append(single)

    print("  7. wheel build and clean install", flush=True)
    wheel = build_and_install_wheel()
    wheel["label"] = "wheel_clean_install"
    wheel["passed"] = wheel["status"] == "passed"
    steps.append(wheel)

    payload = {
        "rc": "0.8.2rc1",
        "generated_at": started,
        "environment": environment.capture(repo=_REPO),
        "steps": steps,
        "passed": sum(1 for s in steps if s.get("passed")),
        "total": len(steps),
        "all_passed": all(s.get("passed") for s in steps),
        "seconds": round(time.time() - started, 1),
        "note": (
            "Locaith OS tests, the reliability benchmark, the soak and the "
            "doctor scaling runs are recorded separately in this directory; "
            "they are not re-run here because each takes minutes to hours."
        ),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "rc1_verification.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    for step in steps:
        mark = "PASS" if step.get("passed") else "FAIL"
        label = step.get("label", "?")
        detail = step.get("summary") or step.get("status") or f"exit {step.get('exit_code')}"
        print(f"  [{mark}] {label:<28} {detail}")
    print(f"\n  {payload['passed']}/{payload['total']} steps passed in "
          f"{payload['seconds']}s")
    print(f"  written: {out}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
