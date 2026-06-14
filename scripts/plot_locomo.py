"""
Generate the LoCoMo benchmark chart from committed report JSON.

This replaces the old hand-drawn, un-reproducible performance graphic:
every bar here is read directly from a committed
``benchmark_reports/locomo_*.json`` file, so the picture in the README
can always be regenerated and audited.

Usage:
    python scripts/plot_locomo.py
    python scripts/plot_locomo.py --headline locomo_overnight_qwen7b_v3 \
        --out docs/images/locomo_benchmark.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPORTS_DIR = "benchmark_reports"
DEFAULT_HEADLINE = "locomo_overnight_qwen7b_v3"
DEFAULT_SLICE = {
    "gemma4:e2b (5B)": "locomo_modelcmp_gemma4_e2b",
    "qwen2.5:7b (7B)": "locomo_modelcmp_qwen7b",
    "gemma4:12b (12B)": "locomo_modelcmp_gemma4_12b",
}

SYSTEM_ORDER = ["no-memory", "naive-rag", "bio-memory"]
SYSTEM_LABELS = {
    "no-memory": "No-memory",
    "naive-rag": "Naive-RAG",
    "bio-memory": "Bio-Agent OS",
}
SYSTEM_COLORS = {
    "no-memory": "#9aa0a6",
    "naive-rag": "#e8710a",
    "bio-memory": "#1a73e8",
}


def _load(tag: str) -> dict:
    path = os.path.join(REPORTS_DIR, f"{tag}.json")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _f1(report: dict, system: str) -> float:
    return float(report["systems"][system]["summary"]["f1_answerable"])


def _temporal(report: dict, system: str) -> float:
    cats = report["systems"][system]["summary"]["per_category"]
    return float(cats.get("temporal", {}).get("f1", 0.0))


def build_chart(headline_tag: str, slice_tags: dict, out_path: str) -> str:
    headline = _load(headline_tag)
    model = headline.get("metadata", {}).get("model", "?")

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(12, 4.6))

    # Left: headline F1 across the three systems.
    systems = [s for s in SYSTEM_ORDER if s in headline["systems"]]
    values = [_f1(headline, s) for s in systems]
    colors = [SYSTEM_COLORS[s] for s in systems]
    bars = ax_left.bar([SYSTEM_LABELS[s] for s in systems], values, color=colors)
    for bar, value in zip(bars, values):
        ax_left.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.008,
            f"{value:.3f}",
            ha="center",
            va="bottom",
            fontsize=11,
            fontweight="bold",
        )
    ax_left.set_title(f"LoCoMo token-F1 — {model}\n(300 questions, deterministic, no LLM judge)", fontsize=11)
    ax_left.set_ylabel("token-F1 (higher is better)")
    ax_left.set_ylim(0, max(values) * 1.25)
    ax_left.grid(axis="y", alpha=0.3)

    # Right: bio-memory advantage grows with model size (temporal F1).
    model_labels = list(slice_tags.keys())
    bio_temporal = []
    rag_temporal = []
    for tag in slice_tags.values():
        report = _load(tag)
        bio_temporal.append(_temporal(report, "bio-memory"))
        rag_temporal.append(_temporal(report, "naive-rag"))
    x = range(len(model_labels))
    width = 0.38
    ax_right.bar([i - width / 2 for i in x], bio_temporal, width, label="Bio-Agent OS", color=SYSTEM_COLORS["bio-memory"])
    ax_right.bar([i + width / 2 for i in x], rag_temporal, width, label="Naive-RAG", color=SYSTEM_COLORS["naive-rag"])
    ax_right.set_xticks(list(x))
    ax_right.set_xticklabels(model_labels, fontsize=9)
    ax_right.set_title("Temporal reasoning F1 scales with model size", fontsize=11)
    ax_right.set_ylabel("temporal token-F1")
    ax_right.legend(fontsize=9)
    ax_right.grid(axis="y", alpha=0.3)

    fig.suptitle("Bio-Agent OS vs Naive-RAG on LoCoMo (reproducible from benchmark_reports/)", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def main() -> None:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    parser = argparse.ArgumentParser(description="Plot the LoCoMo benchmark from committed reports")
    parser.add_argument("--headline", default=DEFAULT_HEADLINE)
    parser.add_argument("--out", default=os.path.join("docs", "images", "locomo_benchmark.png"))
    args = parser.parse_args()

    out = build_chart(args.headline, DEFAULT_SLICE, args.out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
