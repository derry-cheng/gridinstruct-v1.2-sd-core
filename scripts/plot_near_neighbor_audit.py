#!/usr/bin/env python3
"""Render the near-neighbor-free split audit as a compact publication figure."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    report = json.loads((ROOT / "reports/near_neighbor_free_split_v1.2_sd_core.json").read_text(encoding="utf-8"))
    sensitivity = report["threshold_sensitivity"]
    thresholds = [0.8, 0.85, 0.9]
    rates = [100.0 * sensitivity[str(value)]["neighbour_rate"] for value in thresholds]
    split_names = ["train", "validation", "test", "OOD test"]
    split_values = [
        report["splits"]["train"]["records"],
        report["splits"]["validation"]["records"],
        report["splits"]["test"]["records"],
        report["splits"]["ood_test"]["records"],
    ]

    plt.rcParams.update({"font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9})
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.45), constrained_layout=True)
    bars = axes[0].bar(split_names, split_values, color=["#2b6cb0", "#63b3ed", "#90cdf4", "#dd6b20"], width=0.68)
    axes[0].set_title("Component-isolated split sizes")
    axes[0].set_ylabel("records")
    axes[0].tick_params(axis="x", rotation=25)
    axes[0].set_ylim(0, max(split_values) * 1.17)
    for bar, value in zip(bars, split_values):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value + max(split_values) * 0.02, f"{value:,}", ha="center", va="bottom", fontsize=8)
    axes[0].grid(axis="y", alpha=0.2)

    axes[1].plot(thresholds, rates, marker="o", color="#805ad5", linewidth=2)
    axes[1].set_title("Sensitivity to MinHash threshold")
    axes[1].set_xlabel("Jaccard threshold")
    axes[1].set_ylabel("sampled records with a neighbor (%)")
    axes[1].set_xticks(thresholds)
    axes[1].set_ylim(0, max(rates) * 1.18)
    axes[1].grid(alpha=0.2)
    for x, y in zip(thresholds, rates):
        axes[1].annotate(f"{y:.1f}%", (x, y), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)

    for axis in axes:
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)
    output_paths = [
        ROOT / "figures/sd_core_publication/fig_near_neighbor_free_audit.png",
        ROOT / "paper/scientific_data_latex/figures/generated/article/fig_near_neighbor_free_audit.png",
    ]
    for output in output_paths:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=240, bbox_inches="tight")
    print(json.dumps({"outputs": [str(path.relative_to(ROOT)) for path in output_paths], "thresholds": thresholds, "neighbour_rates_percent": rates}, ensure_ascii=False))


if __name__ == "__main__":
    main()
