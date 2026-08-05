"""Regression tests for the staging canary supervisor's argument plumbing.

The first 24-hour canary was started with ``--ramp-rate 0`` to hold a flat
rate, because the disk could not absorb the ramp. The flag was silently
dropped on the way to the detached child, which then parsed the 150.0
default and would have ramped at hour two. Caught six minutes in; these
tests exist so it cannot come back.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "canary_supervisor.py"


def _load():
    spec = importlib.util.spec_from_file_location("_canary_supervisor", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging guard
        pytest.skip("canary_supervisor.py is not importable here")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - optional staging dependency
        pytest.skip(f"canary_supervisor.py needs {type(exc).__name__}: {exc}")
    return module


@pytest.fixture(scope="module")
def supervisor():
    """Import the supervisor without leaking its environment into the suite.

    canary_supervisor.py does `os.environ.setdefault("BIO_AGENT_PROJECTION_MODE",
    "shadow")` at import, which is right for the staging process it owns and
    wrong for every other test in this run: it silently switched observe() onto
    the outbox and broke two unrelated tests that assert the legacy path is
    still primary. Importing a module must not reconfigure the process.
    """
    key = "BIO_AGENT_PROJECTION_MODE"
    had = key in os.environ
    previous = os.environ.get(key)
    try:
        yield _load()
    finally:
        if had:
            os.environ[key] = previous
        else:
            os.environ.pop(key, None)
        sys.modules.pop("_canary_supervisor", None)


@pytest.mark.parametrize("ramp_rate", [None, 0, 0.0, -1.0])
def test_non_positive_ramp_rate_means_no_ramp(supervisor, ramp_rate):
    """None and any non-positive number both mean "stay flat"."""
    assert supervisor._ramp_disabled(ramp_rate) is True


@pytest.mark.parametrize("ramp_rate", [1.0, 150.0, 390.0])
def test_positive_ramp_rate_arms_the_ramp(supervisor, ramp_rate):
    assert supervisor._ramp_disabled(ramp_rate) is False


def test_ramp_rate_survives_the_trip_to_the_detached_child(supervisor):
    """``--ramp-rate 0`` must reach the child, not be dropped as falsy.

    The child re-parses its own argv. If the flag is absent it takes the
    150.0 default, so a run explicitly asked to stay flat would ramp.
    """
    parser = supervisor.build_parser()
    parent = parser.parse_args(
        ["start", "--hours", "24", "--rate", "50", "--ramp-rate", "0"])
    assert parent.ramp_rate == 0.0

    argv = supervisor.detached_argv(parent)
    assert "--ramp-rate" in argv, "the flag was dropped on the way to the child"

    child = parser.parse_args(["start"] + argv[argv.index("start") + 1:])
    assert child.ramp_rate == parent.ramp_rate
    assert supervisor._ramp_disabled(child.ramp_rate) is True


def test_default_ramp_rate_is_still_forwarded_unchanged(supervisor):
    parser = supervisor.build_parser()
    parent = parser.parse_args(["start", "--hours", "24", "--rate", "100"])
    argv = supervisor.detached_argv(parent)
    child = parser.parse_args(["start"] + argv[argv.index("start") + 1:])
    assert child.ramp_rate == parent.ramp_rate == 150.0
    assert supervisor._ramp_disabled(child.ramp_rate) is False
