"""Plotting helpers for the AdaptiveSense analysis.

Charts are intentionally minimal: clear axes with units, readable labels and a
legend, no decorative effects. Every figure is saved under
``results/plots/`` and is directly reusable in the README / report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PLOTS_DIR = Path(__file__).resolve().parent.parent / "results" / "plots"

# stable ordering so every figure uses the same colour per strategy
STRATEGIES = ["Fixed-5s", "Fixed-10s", "Fixed-20s", "Fixed-40s", "Fixed-60s", "AdaptiveSense"]
COLORS = {
    "Fixed-5s": "#a4c3f0",
    "Fixed-10s": "#a9d6a9",
    "Fixed-20s": "#f2d57a",
    "Fixed-40s": "#e6986e",
    "Fixed-60s": "#c9a6dd",
    "AdaptiveSense": "#e05d4f",
}
SCENARIO_ORDER: List[str] = []


def ensure_dir() -> Path:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    return PLOTS_DIR


def _order_strategies(names) -> List[str]:
    ordered = [s for s in STRATEGIES if s in names]
    ordered += [s for s in names if s not in STRATEGIES]
    return ordered


def plot_sampling_count(df, out_dir: Path) -> None:
    """Grouped bar: number of samples per scenario and strategy."""
    scenarios = sorted(df["scenario"].unique())
    strategies = _order_strategies(df["strategy"].unique())
    import numpy as np

    x = np.arange(len(scenarios))
    width = 0.13
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, s in enumerate(strategies):
        subset = df[df["strategy"] == s]
        vals = [subset.loc[subset["scenario"] == sc, "number_of_samples"].iloc[0]
                for sc in scenarios]
        ax.bar(x + (i - len(strategies) / 2) * width, vals, width,
               label=s, color=COLORS.get(s))
    ax.set_xticks(x, [f"Scenario {s[-1].upper() if s[-1].isalpha() else s}" for s in scenarios])
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Number of samples")
    ax.set_title("Samples taken (less is better for resource use)")
    ax.legend(title="Strategy", ncol=2, fontsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "sampling_count.png", dpi=150)
    plt.close(fig)


def plot_communication_reduction(df, out_dir: Path) -> None:
    """Grouped bar: communication (upload) reduction vs full 1 Hz sampling."""
    scenarios = sorted(df["scenario"].unique())
    strategies = _order_strategies(df["strategy"].unique())
    import numpy as np

    x = np.arange(len(scenarios))
    width = 0.13
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, s in enumerate(strategies):
        subset = df[df["strategy"] == s]
        vals = [subset.loc[subset["scenario"] == sc, "communication_reduction"].iloc[0]
                for sc in scenarios]
        ax.bar(x + (i - len(strategies) / 2) * width,
               [v * 100 for v in vals], width, label=s, color=COLORS.get(s))
    ax.set_xticks(x, [f"Scenario {s[-1].upper() if s[-1].isalpha() else s}" for s in scenarios])
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Communication reduction (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Upload reduction relative to full 1 Hz reporting")
    ax.legend(title="Strategy", ncol=2, fontsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "communication_reduction.png", dpi=150)
    plt.close(fig)


def plot_event_detection(df, out_dir: Path) -> None:
    """Two panels: detection rate and false positives, for event-bearing scenarios."""
    ev = df[df["n_gt_events"] > 0]
    if ev.empty:
        return
    scenarios = list(dict.fromkeys(ev["scenario"]))
    strategies = _order_strategies(ev["strategy"].unique())
    import numpy as np

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for idx, metric in enumerate(["event_detection_rate", "false_positive_events"]):
        ax = axes[idx]
        x = np.arange(len(scenarios))
        width = 0.13
        for i, s in enumerate(strategies):
            subset = ev[ev["strategy"] == s]
            vals = [subset.loc[subset["scenario"] == sc, metric].iloc[0]
                    if not subset.loc[subset["scenario"] == sc].empty else 0.0
                    for sc in scenarios]
            ax.bar(x + (i - len(strategies) / 2) * width, vals, width,
                   label=s, color=COLORS.get(s))
        ax.set_xticks(x, [f"Scenario {s[-1].upper() if s[-1].isalpha() else s}" for s in scenarios])
        ax.set_title("Detection rate" if idx == 0 else "False-positive events")
        if idx == 0:
            ax.set_ylabel("Event detection rate")
            ax.set_ylim(0, 1.05)
        else:
            ax.set_ylabel("False positives (count)")
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.legend(title="Strategy", fontsize=7, ncol=1)
    fig.suptitle("Event detection performance (event-bearing scenarios)")
    fig.tight_layout()
    fig.savefig(out_dir / "event_detection.png", dpi=150)
    plt.close(fig)


def plot_detection_latency(df, out_dir: Path) -> None:
    """Bar: average detection latency for event-bearing scenarios."""
    ev = df[df["n_gt_events"] > 0]
    if ev.empty:
        return
    scenarios = list(dict.fromkeys(ev["scenario"]))
    strategies = _order_strategies(ev["strategy"].unique())
    import numpy as np

    x = np.arange(len(scenarios))
    width = 0.13
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, s in enumerate(strategies):
        subset = ev[ev["strategy"] == s]
        vals = [subset.loc[subset["scenario"] == sc, "avg_detection_latency_s"].iloc[0]
                if not subset.loc[subset["scenario"] == sc].empty else 0.0
                for sc in scenarios]
        ax.bar(x + (i - len(strategies) / 2) * width, vals, width,
               label=s, color=COLORS.get(s))
    ax.set_xticks(x, [f"Scenario {s[-1].upper() if s[-1].isalpha() else s}" for s in scenarios])
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Average detection latency (s)")
    ax.set_title("Time from event onset to first detection (lower is better)")
    ax.legend(title="Strategy", ncol=2, fontsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_dir / "detection_latency.png", dpi=150)
    plt.close(fig)


def plot_tradeoff(df, out_dir: Path) -> None:
    """Scatter: communication reduction vs detection rate (accuracy/efficiency)."""
    ev = df[df["n_gt_events"] > 0]
    if ev.empty:
        return
    strategies = _order_strategies(ev["strategy"].unique())
    fig, ax = plt.subplots(figsize=(7, 5))
    for s in strategies:
        subset = ev[ev["strategy"] == s]
        if subset.empty:
            continue
        x = subset["communication_reduction"].mean() * 100
        y = subset["event_detection_rate"].mean()
        n = len(subset)
        ax.scatter([x] * n, subset["event_detection_rate"],
                   color=COLORS.get(s), label=s, s=42,
                   edgecolors="white", linewidths=0.5, zorder=3)
        ax.scatter([x], [y], color=COLORS.get(s), marker="X", s=90, zorder=4,
                   edgecolors="black", linewidths=0.4)
        ax.annotate(s, (x, y), textcoords="offset points",
                    xytext=(-6, 10 - strategies.index(s) * 3),
                    fontsize=8, color=COLORS.get(s))
    ax.set_xlabel("Mean communication reduction (%)")
    ax.set_ylabel("Mean event detection rate")
    ax.set_ylim(-0.02, 1.08)
    ax.set_title("Accuracy vs efficiency trade-off (event scenarios)")
    ax.grid(linestyle=":", alpha=0.5)
    ax.legend(title="Strategy", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_dir / "accuracy_efficiency_tradeoff.png", dpi=150)
    plt.close(fig)


def render_all(df) -> None:
    """Render every required plot for the metric table `df`."""
    out = ensure_dir()
    plot_sampling_count(df, out)
    plot_communication_reduction(df, out)
    plot_event_detection(df, out)
    plot_detection_latency(df, out)
    plot_tradeoff(df, out)