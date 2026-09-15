"""Automated AdaptiveSense analysis and experiment runner.

One command turns the ground-truth datasets into a metrics table and the
five required figures:

    python analysis/analyze.py

Pipeline:
    1. load the central experiment config,
    2. replay every strategy (Fixed-5/10/20/40/60 and AdaptiveSense) over each
       ground-truth scenario from ``dataset/raw/``,
    3. compute the full metric set per run,
    4. write CSV results under ``results/``,
    5. render the figures under ``results/plots/``.

All figures and numbers produced here are **simulation results** on synthetic
ground truth. They are stored separately from any future hardware results.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulator.config import load_config, ROOT
from simulator.events import ground_truth_events
from simulator.metrics import compute_metrics
from simulator.replay import load_dataset, run_all
from analysis import plots as P

RESULTS = ROOT / "results"


def _mark_scenario(path: Path) -> str:
    # scenario_a_stable.csv -> "A"
    return path.stem.split("_")[-2].lower() if path.stem.startswith("scenario_") else path.stem


def write_sample_logs(name: str, runs, out_root: Path) -> None:
    """Persist sampled (and, for adaptive, full decision) streams as CSV."""
    scenario_dir = out_root / "sim" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    for res in runs:
        path = scenario_dir / f"{res.strategy}.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if res.strategy == "AdaptiveSense":
                fieldnames = ["timestamp", "temperature", "humidity", "pressure",
                              "light", "state", "interval_s", "score", "upload", "detected_event"]
            else:
                fieldnames = ["timestamp", "temperature", "humidity", "pressure", "light", "upload"]
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            for d in res.decisions:
                w.writerow({
                    "timestamp": round(d.timestamp, 3), "temperature": d.values["temperature"],
                    "humidity": d.values["humidity"], "pressure": d.values["pressure"],
                    "light": d.values["light"], "state": d.state,
                    "interval_s": d.interval_s, "score": round(d.score, 4),
                    "upload": int(d.upload), "detected_event": int(d.detected_event),
                })
            for s in res.samples:
                w.writerow({**{"timestamp": round(s["timestamp"], 3), **{k: s[k] for k in fieldnames if k in s}}})


def main() -> None:
    cfg = load_config()
    dataset_dir = ROOT / cfg["dataset"]["raw_dir"]
    scenarios = sorted(dataset_dir.glob("scenario_*.csv"))
    if not scenarios:
        print(f"No datasets under {dataset_dir}. Generate them first:\n"
              f"  python dataset/generate_dataset.py")
        return

    all_rows = []
    agg = defaultdict(list)

    for path in scenarios:
        times, rows = load_dataset(path)
        name = _mark_scenario(path)
        scenario_label = f"Scenario {name.upper()}-{path.stem.split('_')[-1]}"
        gt_events = ground_truth_events(rows, times, cfg)
        runs = run_all(times, rows, cfg)
        write_sample_logs(path.stem, runs, RESULTS)

        duration_s = times[-1] - times[0]
        n_gt = len(rows)
        for res in runs:
            m = compute_metrics(
                strategy_name=res.strategy,
                n_ground_truth=n_gt,
                samples_taken=len(res.samples),
                uploads=len(res.uploads),
                n_gt_events=len(gt_events),
                gt_events=gt_events,
                detected=res.detected,
                duration_s=duration_s,
                payload_bytes=cfg["adaptive"]["energy"]["payload_bytes_per_upload"],
                energy_per_upload_mj=cfg["adaptive"]["energy"]["energy_per_upload_mj"],
            )
            row = {
                "scenario": name,
                "scenario_label": scenario_label,
                **m,
            }
            all_rows.append(row)
            agg[res.strategy].append(m)

    # ---- write metrics to CSV --------------------------------------------
    RESULTS.mkdir(parents=True, exist_ok=True)
    metrics_path = RESULTS / "metrics_all.csv"
    fieldnames = list(all_rows[0].keys())
    with open(metrics_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Wrote per-run metrics -> {metrics_path}")

    # ---- averaged summary table ------------------------------------------
    import pandas as pd

    df = pd.DataFrame(all_rows)
    summary = df.groupby("strategy", sort=False).mean(numeric_only=True).reindex(list(agg))
    summary_path = RESULTS / "metrics_summary.csv"
    summary.to_csv(summary_path)
    print(f"Wrote strategy-averaged summary -> {summary_path}")

    P.render_all(df)
    print(f"Wrote plots -> {RESULTS / 'plots'}")


if __name__ == "__main__":
    main()