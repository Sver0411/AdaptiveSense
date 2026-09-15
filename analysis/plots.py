"""Plotting helpers for the AdaptiveSense analysis.

Charts are intentionally minimal: clear axes with units, readable labels and a
legend, no decorative effects. Every figure is saved under ``results/plots/``
and is directly reusable in the README.

All figures are built from **synthetic simulation results**. Scenarios without
ground-truth events carry ``NaN`` for the detection rate (never ``0``) and are
excluded from the event-performance panels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

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
ADAPTIVE = "AdaptiveSense"


def ensure_dir() -> Path:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    return PLOTS_DIR


def _save(fig, filename: str) -> None:
    """Save a figure under ``results/plots/``.

    The PNG text chunk that records the matplotlib version is suppressed, so the
    byte content of a figure does not depend on which build of the plotting
    library happens to be installed. Even so, figure bytes are **not** required to
    be reproducible across machines (rasterisation depends on the freetype/harfbuzz
    build); the reproducibility guarantee covers the numeric artefacts. See
    `results/README.md`.
    """
    fig.savefig(ensure_dir() / filename, dpi=150, metadata={"Software": None})


def _order_strategies(names) -> List[str]:
    ordered = [s for s in STRATEGIES if s in list(names)]
    ordered += [s for s in names if s not in STRATEGIES]
    return ordered


def _scenario_labels(df) -> List[str]:
    """Scenario labels in file order (a, b, c, ...)."""
    pairs = list(dict.fromkeys(zip(df["scenario"], df["scenario_label"])))
    pairs.sort(key=lambda p: p[0])
    return [label for _, label in pairs]


def _as_number(value) -> float:
    """Coerce a metric cell to a float; ``""``/``None``/NaN all become NaN."""
    if value is None:
        return float("nan")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return float("nan")
        try:
            return float(value)
        except ValueError:
            return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _value(df, strategy: str, scenario: str, column: str):
    subset = df[(df["strategy"] == strategy) & (df["scenario"] == scenario)]
    if subset.empty:
        return np.nan
    return subset[column].iloc[0]


def _grouped_bars(df, column: str, ylabel: str, title: str, filename: str) -> None:
    scenarios = sorted(df["scenario"].unique())
    labels = _scenario_labels(df)
    strategies = _order_strategies(df["strategy"].unique())

    x = np.arange(len(scenarios))
    width = 0.13
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    for i, s in enumerate(strategies):
        vals = [float(_value(df, s, sc, column)) for sc in scenarios]
        ax.bar(x + (i - len(strategies) / 2) * width, vals, width,
               label=s, color=COLORS.get(s))
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_xlabel("Scenario")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(title="Strategy", ncol=3, fontsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    _save(fig, filename)
    plt.close(fig)


def plot_sampling_count(df) -> None:
    """Grouped bar: number of samples per scenario and strategy."""
    _grouped_bars(
        df,
        "number_of_samples",
        "Number of samples",
        "Samples taken per scenario (fewer is cheaper)",
        "sampling_count.png",
    )


def plot_application_upload_reduction(df) -> None:
    """Grouped bar: upload reduction vs full 1 Hz reporting."""
    reduction = df.copy()
    reduction["application_upload_reduction_pct"] = reduction["application_upload_reduction"] * 100.0
    _grouped_bars(
        reduction,
        "application_upload_reduction_pct",
        "Communication reduction (%)",
        "Upload reduction relative to reporting every 1 Hz sample",
        "application_upload_reduction.png",
    )


def plot_event_detection(df) -> None:
    """Detection rate, missed events and false positives (event scenarios only)."""
    ev = df[df["n_gt_events"] > 0]
    if ev.empty:
        return
    scenarios = sorted(ev["scenario"].unique())
    labels = _scenario_labels(ev)
    strategies = _order_strategies(ev["strategy"].unique())

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
    panels = [
        ("event_detection_rate", "Event detection rate", (0, 1.08)),
        ("false_positive_events", "False-positive events (count)", None),
    ]
    x = np.arange(len(scenarios))
    width = 0.13
    for ax, (column, ylabel, ylim) in zip(axes, panels):
        for i, s in enumerate(strategies):
            vals = []
            for sc in scenarios:
                v = _as_number(_value(ev, s, sc, column))
                vals.append(0.0 if np.isnan(v) else v)
            ax.bar(x + (i - len(strategies) / 2) * width, vals, width,
                   label=s, color=COLORS.get(s))
        ax.set_xticks(x, labels, rotation=20, ha="right")
        ax.set_ylabel(ylabel)
        if ylim:
            ax.set_ylim(*ylim)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
    axes[0].set_title("Detection rate over labelled ground-truth events")
    axes[1].set_title("Detected events matching no labelled event")
    axes[0].legend(title="Strategy", fontsize=7, ncol=2)
    fig.suptitle("Event performance, scenarios that contain ground-truth events")
    fig.tight_layout()
    _save(fig, "event_detection.png")
    plt.close(fig)


def plot_detection_latency(df) -> None:
    """Mean detection latency per scenario, with p95 marked."""
    ev = df[df["n_gt_events"] > 0]
    if ev.empty:
        return
    scenarios = sorted(ev["scenario"].unique())
    labels = _scenario_labels(ev)
    strategies = _order_strategies(ev["strategy"].unique())

    x = np.arange(len(scenarios))
    width = 0.13
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    for i, s in enumerate(strategies):
        means, p95 = [], []
        for sc in scenarios:
            m = _as_number(_value(ev, s, sc, "avg_detection_latency_s"))
            p = _as_number(_value(ev, s, sc, "p95_detection_latency_s"))
            means.append(0.0 if np.isnan(m) else m)
            p95.append(p)
        pos = x + (i - len(strategies) / 2) * width
        ax.bar(pos, means, width, label=s, color=COLORS.get(s))
        ax.scatter(pos, p95, marker="_", s=42, color="#222222", zorder=4,
                   label="p95" if i == 0 else None)
    ax.set_xticks(x, labels, rotation=20, ha="right")
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Detection latency (s)")
    ax.set_title("Onset-to-detection delay (clamped at 0); bar = mean, dash = p95")
    ax.legend(title="Strategy", ncol=3, fontsize=8)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    _save(fig, "detection_latency.png")
    plt.close(fig)


def plot_tradeoff(summary) -> None:
    """Detection rate vs communication reduction, using micro-aggregated values.

    One point per strategy: ``x`` is the pooled communication reduction over all
    scenarios, ``y`` is ``sum(TP)/sum(GT)`` over the pooled labelled events.
    """
    if summary is None or summary.empty:
        return
    fig, ax = plt.subplots(figsize=(7.6, 5.2))
    for _, row in summary.iterrows():
        rate = _as_number(row.get("event_detection_rate"))
        if np.isnan(rate):
            continue
        x = _as_number(row["application_upload_reduction"]) * 100.0
        y = rate
        name = str(row["strategy"])
        ax.scatter([x], [y], color=COLORS.get(name), s=70, zorder=3,
                   edgecolors="white", linewidths=0.8)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(7, -4),
                    fontsize=8.5, color=COLORS.get(name))
    gt = int(summary["n_gt_events"].max())
    ax.set_xlabel("Communication reduction vs 1 Hz reporting (%)")
    ax.set_ylabel("Overall event detection rate  (sum TP / sum GT)")
    ax.set_ylim(-0.05, 1.08)
    ax.set_title(
        f"Accuracy / efficiency trade-off\nmicro-aggregated over the scenarios "
        f"({gt} labelled events in the largest scenario set)"
    )
    ax.grid(linestyle=":", alpha=0.5)
    fig.tight_layout()
    _save(fig, "accuracy_efficiency_tradeoff.png")
    plt.close(fig)


def render_all(df, summary=None) -> None:
    """Render every required plot for the metric tables."""
    ensure_dir()
    plot_sampling_count(df)
    plot_application_upload_reduction(df)
    plot_event_detection(df)
    plot_detection_latency(df)
    plot_tradeoff(summary)
