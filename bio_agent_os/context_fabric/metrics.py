from __future__ import annotations

from dataclasses import dataclass, field
import statistics
from time import perf_counter
from typing import Any


@dataclass
class ContextMetrics:
    compile_calls: int = 0
    cache_hits: int = 0
    total_naive_tokens: int = 0
    total_compiled_tokens: int = 0
    restore_calls: int = 0
    prefetch_calls: int = 0
    compile_latencies_ms: list[float] = field(default_factory=list)

    def record_compile(self, metrics: dict[str, Any], latency_ms: float) -> None:
        self.compile_calls += 1
        self.cache_hits += int(bool(metrics.get("cache_hit")))
        if not metrics.get("cache_hit"):
            self.total_naive_tokens += int(metrics.get("naive_tokens", metrics.get("compiled_tokens", 0)))
            self.total_compiled_tokens += int(metrics.get("compiled_tokens", 0))
        self.compile_latencies_ms.append(latency_ms)

    def snapshot(self) -> dict[str, Any]:
        values = sorted(self.compile_latencies_ms)
        p95_index = max(0, min(len(values) - 1, int((len(values) - 1) * 0.95 + 0.999999))) if values else 0
        return {
            "compile_calls": self.compile_calls,
            "cache_hit_rate": self.cache_hits / max(self.compile_calls, 1),
            "token_reduction_ratio": 1.0 - self.total_compiled_tokens / max(self.total_naive_tokens, 1),
            "compile_p50_ms": statistics.median(values) if values else 0.0,
            "compile_p95_ms": values[p95_index] if values else 0.0,
            "restore_calls": self.restore_calls,
            "prefetch_calls": self.prefetch_calls,
        }


class TimedOperation:
    def __enter__(self):
        self.started = perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.elapsed_ms = (perf_counter() - self.started) * 1000.0
