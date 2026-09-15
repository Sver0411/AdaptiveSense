"""Offline replay simulator.

Given a 1 Hz dataset, replays *all* sampling strategies (Fixed-5/10/20/40/60 and
AdaptiveSense) over the *same* ground-truth readings so results are directly
comparable. Each strategy is treated as an isolated node that only ever observes
the subset of readings it decided to sample.

Ground-truth events are read from the independent label files produced by
`dataset/generate_dataset.py`; they are never derived from the AdaptiveSense
score (see `docs/change_score_spec.md` section 10).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .adaptive import AdaptiveScheduler, Decision
from .config import ROOT, analyzer_config, load_config
from .events import Event, detect_events, load_ground_truth_events
from .fixed_sampling import run_fixed_strategy, strategy_name

__all__ = [
    "RunResult",
    "load_dataset",
    "load_scenario",
    "run_adaptive",
    "run_fixed",
    "run_all",
]


@dataclass
class RunResult:
    """Everything one strategy produced on one scenario."""

    strategy: str
    samples: List[Dict[str, float]] = field(default_factory=list)
    uploads: List[Dict[str, float]] = field(default_factory=list)
    decisions: List[Optional[Decision]] = field(default_factory=list)
    detected: List[Event] = field(default_factory=list)
    # For a fixed-rate baseline: its constant interval. None for AdaptiveSense.
    fixed_interval_s: Optional[float] = None

    @property
    def sample_timestamps(self) -> List[float]:
        return [float(s["timestamp"]) for s in self.samples]


def load_dataset(path: Path | str) -> Tuple[List[float], List[Dict[str, float]]]:
    """Load a dataset CSV -> (timestamps, rows)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    times = [float(r["timestamp"]) for r in rows]
    for r in rows:
        for k in list(r.keys()):
            if k == "timestamp":
                continue
            r[k] = float(r[k])
    return times, rows


def load_scenario(
    raw_path: Path | str, cfg: dict
) -> Tuple[List[float], List[Dict[str, float]], List[Event]]:
    """Load a raw scenario together with its independent ground-truth labels."""
    label_dir = ROOT / cfg["dataset"]["label_dir"]
    times, rows = load_dataset(raw_path)
    labels = load_ground_truth_events(raw_path, label_dir)
    return times, rows, labels


def _detect(samples: Sequence[Dict[str, float]], cfg: dict) -> List[Event]:
    """Detect events from the samples a node actually observed.

    Uses the documented change score only — the ground truth is not involved.
    """
    if not samples:
        return []
    return detect_events(list(samples), analyzer_config(cfg))


def run_adaptive(
    times: List[float], rows: List[Dict[str, float]], cfg: dict
) -> RunResult:
    """Replay AdaptiveSense on the dataset.

    The sampling loop mirrors `firmware/main/main.c`: a sample is taken when the
    clock reaches the scheduled time, and the interval returned by the policy
    schedules the *next* sample.
    """
    res = RunResult(strategy="AdaptiveSense")
    if not times:
        return res

    scheduler = AdaptiveScheduler(cfg, time=times[0])
    next_sample = times[0]
    n = len(times)

    for i in range(n):
        t = times[i]
        if t < next_sample - 1e-9:
            continue
        values = {k: rows[i][k] for k in rows[i] if k != "timestamp"}
        d = scheduler.update(t, values)

        sample = {"timestamp": t, **values}
        res.samples.append(sample)
        res.decisions.append(d)
        if d.upload_requested:
            res.uploads.append(sample)
        next_sample = t + d.interval_s

    res.detected = _detect(res.samples, cfg)
    return res


def run_fixed(
    times: List[float], rows: List[Dict[str, float]], interval: float, cfg: dict
) -> RunResult:
    """Replay a fixed-rate baseline (samples == uploads; no change awareness)."""
    res = RunResult(strategy=strategy_name(interval), fixed_interval_s=float(interval))
    for f in run_fixed_strategy(times, rows, interval):
        sample = {"timestamp": f.timestamp, **f.values}
        res.samples.append(sample)
        res.decisions.append(None)
        if f.upload:
            res.uploads.append(sample)
    res.detected = _detect(res.samples, cfg)
    return res


def run_all(
    times: List[float], rows: List[Dict[str, float]], cfg: dict
) -> List[RunResult]:
    """Run every strategy in the config over the given series."""
    results: List[RunResult] = []
    for interval in cfg["fixed_strategies"]:
        results.append(run_fixed(times, rows, float(interval), cfg))
    results.append(run_adaptive(times, rows, cfg))
    return results


if __name__ == "__main__":  # pragma: no cover - simple CLI
    import sys

    cfg = load_config()
    dataset_dir = ROOT / cfg["dataset"]["raw_dir"]
    targets = (
        [Path(sys.argv[1])]
        if len(sys.argv) > 1
        else sorted(dataset_dir.glob("*.csv"))
    )
    for path in targets:
        times, rows, labels = load_scenario(path, cfg)
        print(f"== {path.name}: {len(rows)} rows, {len(labels)} ground-truth events ==")
        for res in run_all(times, rows, cfg):
            print(
                f"  {res.strategy:16s}: samples={len(res.samples):6d} "
                f"uploads={len(res.uploads):6d} detected={len(res.detected)}"
            )
    print("Replay complete. Use analysis/analyze.py to compute full metrics.")
