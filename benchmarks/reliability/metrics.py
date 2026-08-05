"""Latency measured per operation, not by dividing a wall clock.

A single timer around the whole run divided by the item count gives a mean and
hides everything worth knowing: the mean is fine while the p99 is a timeout.
Every stage here records its own sample.

Exactness is stated rather than assumed. Below `max_exact` samples the
percentiles are computed from the raw list and reported as `exact`. Above it —
a soak run produces millions — the raw list stops growing and percentiles come
from log-spaced buckets, reported as `bucketed` with the bucket resolution
attached. A percentile with no stated method is a percentile nobody can check.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: 50 buckets per decade → any bucket spans a 4.7% range, so a bucketed
#: percentile is within about 5% of the exact one. Stated in the output.
_BUCKETS_PER_DECADE = 50
_MIN_MS = 1e-3          # 1 microsecond
_MAX_MS = 3.6e6         # one hour
_LOG_MIN = math.log10(_MIN_MS)
_N_BUCKETS = int((math.log10(_MAX_MS) - _LOG_MIN) * _BUCKETS_PER_DECADE) + 2


class Histogram:
    """Latency samples in milliseconds."""

    __slots__ = ("name", "max_exact", "_exact", "_buckets", "count", "total", "min", "max")

    def __init__(self, name: str, *, max_exact: int = 250_000) -> None:
        self.name = name
        self.max_exact = max_exact
        self._exact: list[float] = []
        self._buckets = [0] * _N_BUCKETS
        self.count = 0
        self.total = 0.0
        self.min = math.inf
        self.max = -math.inf

    def add(self, value_ms: float) -> None:
        if value_ms < 0:
            return
        self.count += 1
        self.total += value_ms
        if value_ms < self.min:
            self.min = value_ms
        if value_ms > self.max:
            self.max = value_ms
        if len(self._exact) < self.max_exact:
            self._exact.append(value_ms)
        self._buckets[self._bucket_of(value_ms)] += 1

    def extend(self, values: Iterable[float]) -> None:
        for value in values:
            self.add(value)

    def merge(self, other: "Histogram") -> None:
        """Fold another histogram in — used to combine worker processes.

        The exact list is merged only while it still fits; past that the
        buckets carry the distribution and `percentile_method` says so.
        """
        if other.count == 0:
            return
        self.count += other.count
        self.total += other.total
        self.min = min(self.min, other.min)
        self.max = max(self.max, other.max)
        room = self.max_exact - len(self._exact)
        if room > 0:
            self._exact.extend(other._exact[:room])
        for i, n in enumerate(other._buckets):
            if n:
                self._buckets[i] += n

    @staticmethod
    def _bucket_of(value_ms: float) -> int:
        if value_ms <= _MIN_MS:
            return 0
        idx = int((math.log10(value_ms) - _LOG_MIN) * _BUCKETS_PER_DECADE) + 1
        return min(max(idx, 0), _N_BUCKETS - 1)

    @staticmethod
    def _bucket_value(index: int) -> float:
        if index == 0:
            return _MIN_MS
        return 10 ** (_LOG_MIN + (index - 0.5) / _BUCKETS_PER_DECADE)

    @property
    def exact(self) -> bool:
        return self.count <= self.max_exact and len(self._exact) == self.count

    def percentile(self, q: float) -> float:
        if self.count == 0:
            return 0.0
        if self.exact:
            ordered = sorted(self._exact)
            k = min(len(ordered) - 1, max(0, int(math.ceil(q * len(ordered))) - 1))
            return ordered[k]
        target = q * self.count
        seen = 0
        for i, n in enumerate(self._buckets):
            seen += n
            if seen >= target:
                return self._bucket_value(i)
        return self.max

    def as_dict(self) -> dict[str, Any]:
        if self.count == 0:
            return {"name": self.name, "count": 0}
        return {
            "name": self.name,
            "count": self.count,
            "min_ms": round(self.min, 4),
            "mean_ms": round(self.total / self.count, 4),
            "p50_ms": round(self.percentile(0.50), 4),
            "p95_ms": round(self.percentile(0.95), 4),
            "p99_ms": round(self.percentile(0.99), 4),
            "max_ms": round(self.max, 4),
            "percentile_method": (
                "exact" if self.exact else f"log-bucket (+/-{100 / _BUCKETS_PER_DECADE:.1f}% within bucket)"
            ),
        }


@dataclass(slots=True)
class JobSample:
    """One projection, timed at every stage it passes through.

    Absolute epoch seconds, because the stages happen in different processes
    and a per-process monotonic clock cannot be subtracted across them.
    """

    job_id: str
    event_id: str
    tenant_id: str
    outbox_created_at: float
    claimed_at: float
    build_started_at: float
    build_finished_at: float
    completed_at: float
    status: str
    attempts: int
    worker_id: str

    @property
    def queue_wait_ms(self) -> float:
        return (self.claimed_at - self.outbox_created_at) * 1000

    @property
    def build_ms(self) -> float:
        return (self.build_finished_at - self.build_started_at) * 1000

    @property
    def completion_gap_ms(self) -> float:
        return (self.completed_at - self.build_finished_at) * 1000

    @property
    def end_to_end_ms(self) -> float:
        return (self.completed_at - self.outbox_created_at) * 1000

    def as_row(self) -> list[Any]:
        return [
            self.job_id, self.event_id, self.tenant_id, self.outbox_created_at,
            self.claimed_at, self.build_started_at, self.build_finished_at,
            self.completed_at, self.status, self.attempts, self.worker_id,
        ]

    @classmethod
    def from_row(cls, row: list[Any]) -> "JobSample":
        return cls(
            job_id=row[0], event_id=row[1], tenant_id=row[2],
            outbox_created_at=float(row[3]), claimed_at=float(row[4]),
            build_started_at=float(row[5]), build_finished_at=float(row[6]),
            completed_at=float(row[7]), status=row[8], attempts=int(row[9]),
            worker_id=row[10],
        )


@dataclass
class StageHistograms:
    """The four stages a projection passes through, plus its total."""

    queue_wait: Histogram = field(default_factory=lambda: Histogram("queue_wait"))
    build: Histogram = field(default_factory=lambda: Histogram("build"))
    completion_gap: Histogram = field(default_factory=lambda: Histogram("completion_gap"))
    end_to_end: Histogram = field(default_factory=lambda: Histogram("end_to_end_visibility"))

    def add(self, sample: JobSample) -> None:
        self.queue_wait.add(sample.queue_wait_ms)
        self.build.add(sample.build_ms)
        self.completion_gap.add(sample.completion_gap_ms)
        self.end_to_end.add(sample.end_to_end_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "queue_wait": self.queue_wait.as_dict(),
            "build": self.build.as_dict(),
            "completion_gap": self.completion_gap.as_dict(),
            "end_to_end_visibility": self.end_to_end.as_dict(),
        }


def write_samples(samples: Iterable[JobSample], path: str | Path, *,
                  append: bool = False) -> Path:
    """Write samples as JSONL.

    `append` exists for long runs: a worker that only writes on exit gives a
    soak no latency data until it stops, which is the opposite of what a soak
    is for. Appending in batches keeps the cost off the per-job path.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a" if append else "w", encoding="utf-8") as fh:
        for sample in samples:
            fh.write(json.dumps(sample.as_row()) + "\n")
    return out


def read_samples(path: str | Path, *, tail: int | None = None) -> list[JobSample]:
    """Parse a sample file, optionally only its last `tail` lines.

    Reading and parsing hundreds of thousands of samples every sampling
    window would make the observer the slowest thing in the run.
    """
    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if tail is not None:
        lines = lines[-tail:]
    out = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                out.append(JobSample.from_row(json.loads(line)))
            except (ValueError, IndexError, TypeError):
                continue  # a partially written last line during a live read
    return out


__all__ = [
    "Histogram",
    "JobSample",
    "StageHistograms",
    "read_samples",
    "write_samples",
]
