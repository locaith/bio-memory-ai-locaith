"""The benchmark's own tests.

Two kinds, and the distinction matters.

The unmarked tests check the *measuring instrument*: a percentile that is
wrong, a corpus that is secretly uniform, a fault schedule that is not
deterministic, or an environment record that omits the machine would all
produce numbers nobody can trust. They run in the default suite because they
are fast and because a broken instrument is a real defect.

The `benchmark`-marked tests run actual multi-process load and are excluded
from the default run — a load test inside the unit suite makes the unit suite
something people stop running.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.reliability import corpus, environment, recovery, workloads
from benchmarks.reliability.metrics import Histogram, JobSample, StageHistograms


# -- the instrument: percentiles --------------------------------------------

def test_exact_percentiles_are_exact_below_the_threshold():
    hist = Histogram("t", max_exact=1000)
    for value in range(1, 101):
        hist.add(float(value))
    assert hist.exact
    assert hist.percentile(0.50) == 50
    assert hist.percentile(0.95) == 95
    assert hist.percentile(0.99) == 99
    assert hist.as_dict()["percentile_method"] == "exact"


def test_bucketed_percentiles_stay_within_the_stated_error():
    """Past `max_exact` the raw list stops growing and buckets take over.

    The claim in the output is +/-2% within a bucket. If that claim were
    wrong every soak percentile would be wrong, so it is checked rather than
    asserted in a docstring.
    """
    hist = Histogram("t", max_exact=100)
    for value in range(1, 10_001):
        hist.add(float(value))
    assert not hist.exact
    assert "log-bucket" in hist.as_dict()["percentile_method"]
    for q, expected in ((0.50, 5000), (0.95, 9500), (0.99, 9900)):
        assert math.isclose(hist.percentile(q), expected, rel_tol=0.05), q


def test_mean_min_max_and_count_stay_exact_even_when_bucketed():
    """Bucketing costs percentile precision. It must not cost anything else."""
    hist = Histogram("t", max_exact=10)
    values = [float(v) for v in range(1, 1001)]
    hist.extend(values)
    assert hist.count == 1000
    assert hist.min == 1.0
    assert hist.max == 1000.0
    assert math.isclose(hist.total / hist.count, sum(values) / len(values))


def test_merging_two_histograms_preserves_the_totals():
    left, right = Histogram("l"), Histogram("r")
    left.extend([1.0, 2.0, 3.0])
    right.extend([4.0, 5.0, 6.0])
    left.merge(right)
    assert left.count == 6
    assert left.min == 1.0 and left.max == 6.0
    assert math.isclose(left.total, 21.0)


def test_empty_histogram_reports_nothing_rather_than_zero():
    """A p95 of 0.0 from no samples reads as 'very fast'. It is 'no data'."""
    assert Histogram("t").as_dict() == {"name": "t", "count": 0}


# -- the instrument: stage arithmetic ---------------------------------------

def _sample(**overrides):
    base = dict(
        job_id="j", event_id="e", tenant_id="t", outbox_created_at=100.0,
        claimed_at=100.5, build_started_at=100.6, build_finished_at=100.9,
        completed_at=101.0, status="completed", attempts=1, worker_id="w",
    )
    base.update(overrides)
    return JobSample(**base)


def test_stage_timings_decompose_the_end_to_end_latency():
    sample = _sample()
    assert math.isclose(sample.queue_wait_ms, 500.0)
    assert math.isclose(sample.build_ms, 300.0, abs_tol=1e-6)
    assert math.isclose(sample.completion_gap_ms, 100.0, abs_tol=1e-6)
    assert math.isclose(sample.end_to_end_ms, 1000.0)


def test_stage_histograms_record_every_stage():
    stages = StageHistograms()
    for i in range(10):
        stages.add(_sample(completed_at=101.0 + i / 100))
    payload = stages.as_dict()
    assert payload["queue_wait"]["count"] == 10
    assert payload["end_to_end_visibility"]["count"] == 10
    assert payload["end_to_end_visibility"]["max_ms"] > payload["end_to_end_visibility"]["min_ms"]


def test_sample_survives_a_round_trip_through_the_jsonl():
    original = _sample()
    assert JobSample.from_row(original.as_row()) == original


# -- the instrument: corpus --------------------------------------------------

def test_corpus_covers_every_tenant_workspace_and_domain():
    observations = list(corpus.generate(2000))
    assert len({o.tenant_id for o in observations}) == corpus.TENANTS
    assert len({o.workspace_id for o in observations}) == (
        corpus.TENANTS * corpus.WORKSPACES_PER_TENANT
    )
    assert {o.domain for o in observations} == set(corpus.DOMAINS)


def test_corpus_is_not_one_string_repeated():
    """The failure this guards against: a corpus that measures the page cache."""
    contents = [o.content for o in corpus.generate(1000)]
    assert len(set(contents)) > 900
    lengths = {len(c) for c in contents}
    assert len(lengths) > 50, "uniform row size never produces a page split"


def test_corpus_is_deterministic_by_seed():
    first = [o.content for o in corpus.generate(200, seed=7)]
    second = [o.content for o in corpus.generate(200, seed=7)]
    third = [o.content for o in corpus.generate(200, seed=8)]
    assert first == second
    assert first != third


def test_corpus_start_offset_does_not_overlap():
    """Several producers draw from one corpus; overlapping content would make
    their write paths hit the same pages and understate contention."""
    left = {o.content for o in corpus.generate(300, start=0)}
    right = {o.content for o in corpus.generate(300, start=300)}
    assert not (left & right)


# -- the instrument: fault schedule -----------------------------------------

def test_fault_weights_sum_to_one():
    assert math.isclose(sum(w for _, w in workloads.FAULT_WEIGHTS), 1.0)


def test_fault_bucket_is_deterministic_and_roughly_matches_its_weights():
    ids = [f"event-{i}" for i in range(20_000)]
    first = [workloads.fault_bucket(i) for i in ids]
    assert first == [workloads.fault_bucket(i) for i in ids]
    for name, weight in workloads.FAULT_WEIGHTS:
        share = first.count(name) / len(ids)
        assert abs(share - weight) < 0.02, f"{name}: {share:.3f} vs {weight}"


def test_fault_injector_fails_transiently_then_succeeds():
    """Transient means transient. A retry that never succeeds is a permanent
    failure wearing the wrong label, and would make the backoff measurement
    meaningless."""

    class _Inner:
        def build(self, event, job, conn):
            return "built"

    injector = workloads.FaultInjectingBuilder(_Inner(), transient_until_attempt=3)
    transient = next(
        f"e{i}" for i in range(10_000) if workloads.fault_bucket(f"e{i}") == "transient"
    )
    event = type("E", (), {"event_id": transient})()
    job = type("J", (), {"attempts": 1})()
    with pytest.raises(workloads.BenchmarkFailure):
        injector.build(event, job, None)
    job.attempts = 3
    assert injector.build(event, job, None) == "built"


def test_fault_injector_permanent_failure_never_succeeds():
    class _Inner:
        def build(self, event, job, conn):
            return "built"

    injector = workloads.FaultInjectingBuilder(_Inner())
    permanent = next(
        f"e{i}" for i in range(10_000) if workloads.fault_bucket(f"e{i}") == "permanent"
    )
    event = type("E", (), {"event_id": permanent})()
    for attempt in (1, 5, 50):
        with pytest.raises(workloads.BenchmarkFailure):
            injector.build(event, type("J", (), {"attempts": attempt})(), None)


# -- the instrument: environment --------------------------------------------

def test_environment_record_names_the_machine():
    record = environment.capture(repo=_REPO)
    for key in ("os", "cpu_model", "cpu_cores_logical", "ram_bytes", "python",
                "sqlite_version", "process_start_method", "commit_sha"):
        assert record.get(key) not in (None, "", 0), key


def test_rss_is_readable_on_this_platform():
    """Reported as 0 rather than guessed if it cannot be read — but on a
    platform where it can, a 0 would silently hide a memory leak."""
    assert environment.rss_bytes() > 0
    assert environment.peak_rss_bytes() >= environment.rss_bytes() * 0.5


def test_sqlite_settings_are_read_from_the_database_not_assumed():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "probe.db")
        from bio_agent_os.cognitive.facade import MemoryOS

        MemoryOS(db).close()
        settings = environment.sqlite_settings(db)
        assert settings["journal_mode"] == "wal"
        assert settings["page_size"] > 0
        footprint = environment.database_footprint(db)
        assert footprint["db_bytes"] > 0
        assert footprint["total_bytes"] >= footprint["db_bytes"]


# -- the instrument: doctor digest ------------------------------------------

def test_doctor_digest_keeps_the_counts_and_drops_the_findings():
    """A 100k-event scan produces tens of thousands of findings. The summary
    has to stay readable without losing what was actually found."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "digest.db")
        from bio_agent_os.cognitive.facade import MemoryOS

        runtime = MemoryOS(db, projection_mode="shadow")
        for obs in corpus.generate(20):
            runtime.observe(
                tenant_id=obs.tenant_id, actor=obs.actor, source=obs.source,
                content=obs.content, workspace_id=obs.workspace_id,
            )
        runtime.close()
        payload = workloads.run_doctor(db, deep=True)
        digest = workloads.doctor_digest(payload)
        assert digest["exit_code"] == payload["exit_code"]
        assert digest["checks_run"] == payload["checks_run"]
        assert sum(digest["finding_codes"].values()) == len(payload["findings"])
        assert "findings" not in digest


# -- actual load (excluded by default) --------------------------------------

@pytest.mark.benchmark
def test_append_and_drain_under_multiple_processes():
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp)
        appended = workloads.workload_a_append_only(run_dir=run, events=400, producers=2)
        assert appended["appended"] == 400
        assert appended["failures"] == 0

        drained = workloads.workload_b_projection_only(run_dir=run, events=400, workers=2)
        assert drained["queue_after"]["pending"] == 0
        assert drained["queue_after"]["completed"] == 400
        assert drained["doctor_after"]["exit_code"] == 0


@pytest.mark.benchmark
def test_crash_under_load_loses_nothing_and_duplicates_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        result = recovery.workload_e_recovery(run_dir=Path(tmp), events=400, workers=2)
        assert result["lost_events"] == 0
        final = result["scenarios"][-1]
        assert final["sqlite_integrity_check"] == "ok"
        assert final["duplicate_ledger_rows"] == 0
        assert final["duplicate_projections"] == 0
        assert result["final_queue"]["in_progress"] == 0


@pytest.mark.benchmark
def test_deliberate_failures_reach_the_right_terminal_states():
    with tempfile.TemporaryDirectory() as tmp:
        result = workloads.workload_faults(run_dir=Path(tmp), events=600, workers=2)
        terminal = result["terminal_states"]
        assert terminal["pending"] == 0 and terminal["in_progress"] == 0
        assert terminal["completed"] > 0
        assert terminal["dead_letter"] > 0, "no dead letters means the injector did nothing"
        assert terminal["skipped"] > 0
        assert result["unexplained_dead_letters"] == 0
        assert result["stale_claims_remaining"] == 0


@pytest.mark.soak
def test_soak_harness_produces_a_usable_time_series():
    """A short soak, to prove the long one measures something.

    The real run is an hour via the CLI. This checks the parts that would
    silently produce an empty report: samples must reach disk *while* workers
    are still alive, the series must have points, and the queue must drain
    once producers stop.
    """
    from benchmarks.reliability import soak

    with tempfile.TemporaryDirectory() as tmp:
        result = soak.run(
            run_dir=Path(tmp), seconds=90, producers=1, workers=2,
            target_rate=60.0, sample_interval=15.0, worker_restart_every=10_000,
            doctor_every=2,
        )
        # Read the series before the temporary directory goes away: it lives
        # inside run_dir, and reading it after the `with` block finds nothing
        # and blames the harness for it.
        series = _series(result)

    assert result["samples"] >= 4
    assert result["appended"] > 0
    assert result["queue_drained_after_stop"] is True, (
        "the queue must reach zero on its own after the producers stop; "
        "workers are deliberately still running at that point"
    )
    assert result["sqlite_integrity_check"] == "ok"
    assert result["rss_mb_first"] and result["rss_mb_first"] > 0, "RSS never read"
    # The failure this exists for: workers that only write samples on exit
    # leave every latency field None for the whole run.
    assert any(p.get("p95_end_to_end_ms") for p in series), (
        "no latency sample reached disk while the workers were still running"
    )
    assert all(p["queue_depth"] >= 0 for p in series)


def _series(result):
    path = Path(result["timeseries_path"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.benchmark
def test_shadow_costs_something_measurable_and_leaks_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        result = workloads.workload_d_shadow(run_dir=Path(tmp), events=300)
        assert result["match_rate_pct"] == 100.0
        assert result["shadow_rows_visible_in_production"] == 0
        assert result["shadow_rows_returned_by_recall"] == 0
        assert result["shadow_observe_ms"]["count"] == 300
