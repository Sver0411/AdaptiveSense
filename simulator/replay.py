"""Offline replay simulator.

Given a 1 Hz ground-truth dataset, replays *all* sampling strategies
(Fixed-5/10/20/40/60 and AdaptiveSense) over the *same* ground-truth readings
so results are directly comparable. Each strategy is treated as an isolated
node that only ever observes the subset of readings it decided to sample.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .adaptive import AdaptiveScheduler, Decision
from .config import load_config, ROOT
from .events import ground_truth_events
from .fixed_sampling import run_fixed_strategy, strategy_name


@dataclass
class RunResult:
    """Everything one strategy produced on one scenario."""

    strategy: str
    samples: List[Dict[str, float]] = field(default_factory=list)  # taken samples
    uploads: List[Dict[str, float]] = field(default_factory=list)  # uploaded samples
    decisions: List[Decision] = field(default_factory=list)
    detected: List = field(default_factory=list)


def load_dataset(path: Path) -> Tuple[List[float], List[Dict[str, float]]]:
    """Load a ground-truth CSV -> (timestamps, rows)."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for r in reader]
    times = [float(r["timestamp"]) for r in rows]
    for r in rows:
        for k in list(r.keys()):
            if k == "timestamp":
                continue
            r[k] = float(r[k])
    return times, rows


def _detect(samples: List[Dict[str, float]], cfg: dict):
    """Detect sustained events from a sampled stream using the SAME function
    and thresholds as ground-truth event extraction, but fed only the readings
    the node actually observed."""
    if not samples:
        return []
    times = [s["timestamp"] for s in samples]
    rows = [{"timestamp": s["timestamp"], **{k: float(s[k]) for k in s if k != "timestamp"}}
            for s in samples]
    return ground_truth_events(rows, times, cfg)


def run_adaptive(times: List[float], rows: List[Dict[str, float]], cfg: dict) -> RunResult:
    """Replay AdaptiveSense on the ground-truth series."""
    res = RunResult(strategy="AdaptiveSense")
    scheduler = AdaptiveScheduler(cfg, time=times[0] if times else 0.0)

    next_sample = times[0] if times else 0.0
    n = len(times)
    i = 0
    while i < n:
        t = times[i]
        if t < next_sample - 1e-9:
            i += 1
            continue
        values = {k: rows[i][k] for k in rows[i] if k != "timestamp"}
        d = scheduler.update(t, values)

        sample = {"timestamp": t, **values}
        res.samples.append(sample)
        res.decisions.append(d)
        if d.upload:
            res.uploads.append(sample)
        next_sample = t + d.interval_s
        i += 1

    res.detected = _detect(res.samples, cfg)
    return res


def run_fixed(times: List[float], rows: List[Dict[str, float]], interval: float, cfg: dict) -> RunResult:
    """Replay a fixed-rate baseline."""
    name = strategy_name(interval)
    res = RunResult(strategy=name)
    fixed = run_fixed_strategy(times, rows, interval)
    for f in fixed:
        sample = {"timestamp": f.timestamp, **f.values}
        res.samples.append(sample)
        if f.upload:
            res.uploads.append(sample)
    res.detected = _detect(res.samples, cfg)
    return res


def run_all(
    times: List[float],
    rows: List[Dict[str, float]],
    cfg: dict,
) -> List[RunResult]:
    """Run every strategy in the config over the given ground-truth series."""
    results: List[RunResult] = []
    for interval in cfg["fixed_strategies"]:
        results.append(run_fixed(times, rows, float(interval), cfg))
    results.append(run_adaptive(times, rows, cfg))
    return results


if __name__ == "__main__":  # pragma: no cover - simple CLI
    import sys

    cfg = load_config()
    dataset_dir = ROOT / cfg["dataset"]["raw_dir"]
    if len(sys.argv) > 1:
        targets = [Path(sys.argv[1])]
    else:
        targets = sorted(dataset_dir.glob("*.csv"))
    for path in targets:
        times, rows = load_dataset(path)
        print(f"== {path.name}: {len(rows)} rows ==")
        for res in run_all(times, rows, cfg):
            gt = ground_truth_events(rows, times, cfg)
            det = res.detected if res.detected else []
            print(f"  {res.strategy:16s}: samples={len(res.samples):6d} "
                  f"uploads={len(res.uploads):6d} detected={len(det)}")
    print("Replay complete. Use analysis/analyze.py to compute full metrics.")