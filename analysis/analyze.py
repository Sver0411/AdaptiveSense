"""Automated AdaptiveSense analysis and experiment runner.

    python analysis/analyze.py

Pipeline:

1. load the central experiment config,
2. for every scenario in ``dataset/raw/`` load the raw signal **and its
   independent ground-truth labels** from ``dataset/labels/``,
3. replay every strategy (Fixed-5/10/20/40/60 and AdaptiveSense),
4. write one row per sample per strategy under ``results/sim/<scenario>/``,
5. compute per-scenario metrics (``results/metrics_all.csv``) and
   micro-aggregated overall metrics (``results/metrics_summary.csv``),
6. render the figures under ``results/plots/``.

Everything produced here is a **synthetic simulation result**. No hardware
measurement is involved. See `docs/change_score_spec.md` for the metric
definitions and `docs/audit_v0.2.md` for what changed relative to v0.1.
"""

from __future__ import annotations

import csv
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from analysis import plots as P
from simulator.config import ROOT, load_config
from simulator.metrics import RunMetrics, aggregate_micro, compute_run_metrics
from simulator.replay import RunResult, load_scenario, run_all

RESULTS = ROOT / "results"

# One row per sample. The same schema is used for every strategy so the logs
# are directly comparable; a fixed-rate strategy fills `state` with its own
# name and leaves `score` empty (it has no change score).
SAMPLE_FIELDS = [
    "timestamp",
    "temperature",
    "humidity",
    "pressure",
    "light",
    "state",
    "interval_s",
    "score",
    "upload_requested",
    "detected_event",
]


def parse_scenario_name(path: Path) -> Tuple[str, str, str]:
    """`scenario_b_sudden.csv` -> ("b", "sudden", "B: sudden")."""
    parts = path.stem.split("_")
    if len(parts) >= 3 and parts[0] == "scenario":
        letter = parts[1]
        name = " ".join(parts[2:])
        return letter, name, f"{letter.upper()}: {name}"
    return path.stem, path.stem, path.stem


def write_sample_log(path: Path, res: RunResult, strategy_interval: float | None) -> None:
    """Persist the sampled stream of one strategy — exactly one row per sample.

    v0.1 wrote the decision stream *and* the sample stream for AdaptiveSense,
    which duplicated every timestamp; the decision fields are now merged into
    the sampled row instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SAMPLE_FIELDS)
        w.writeheader()
        for i, sample in enumerate(res.samples):
            decision = res.decisions[i] if i < len(res.decisions) else None
            row: Dict[str, object] = {
                "timestamp": round(float(sample["timestamp"]), 3),
                "temperature": sample["temperature"],
                "humidity": sample["humidity"],
                "pressure": sample["pressure"],
                "light": sample["light"],
            }
            if decision is not None:
                row.update(
                    state=decision.state,
                    interval_s=decision.interval_s,
                    score=round(decision.score, 4),
                    upload_requested=int(decision.upload_requested),
                    detected_event=int(decision.detected_event),
                )
            else:  # fixed-rate baseline: samples == uploads, no change score
                row.update(
                    state="FIXED",
                    interval_s=strategy_interval if strategy_interval else "",
                    score="",
                    upload_requested=1,
                    detected_event=0,
                )
            w.writerow(row)


def main() -> None:
    cfg = load_config()
    dataset_dir = ROOT / cfg["dataset"]["raw_dir"]
    scenarios = sorted(dataset_dir.glob("scenario_*.csv"))
    if not scenarios:
        print(
            f"No datasets under {dataset_dir}. Generate them first:\n"
            f"  python dataset/generate_dataset.py"
        )
        return

    eval_cfg = cfg["evaluation"]
    tolerance = float(eval_cfg["event_match_tolerance_s"])
    payload_bytes = float(cfg["adaptive"]["energy"]["payload_bytes_per_upload"])
    energy_units = float(cfg["adaptive"]["energy"]["upload_energy_units_per_upload"])

    runs_by_strategy: "OrderedDict[str, List[RunMetrics]]" = OrderedDict()
    all_rows: List[Dict[str, object]] = []
    scenarios_no_events: List[str] = []

    print(f"Event matching tolerance: {tolerance} s (channel-aware, one-to-one)")
    print()

    for path in scenarios:
        letter, name, label = parse_scenario_name(path)
        times, rows, gt_events = load_scenario(path, cfg)
        duration_s = times[-1] - times[0] if times else 0.0
        runs = run_all(times, rows, cfg)

        if not gt_events:
            scenarios_no_events.append(label)
        print(
            f"{label:22s} {len(rows):6d} rows  {len(gt_events):2d} GT events  "
            f"({duration_s / 60.0:.0f} min)"
        )

        for res in runs:
            write_sample_log(
                RESULTS / "sim" / path.stem / f"{res.strategy}.csv",
                res,
                res.fixed_interval_s,
            )

            m = compute_run_metrics(
                strategy_name=res.strategy,
                scenario=letter,
                scenario_label=label,
                sample_timestamps=res.sample_timestamps,
                n_ground_truth=len(rows),
                uploads=len(res.uploads),
                gt_events=gt_events,
                detected=res.detected,
                duration_s=duration_s,
                payload_bytes=payload_bytes,
                energy_units_per_upload=energy_units,
                match_tolerance_s=tolerance,
            )
            runs_by_strategy.setdefault(res.strategy, []).append(m)
            all_rows.append(m.csv_row())

    # ---- per-scenario metrics -------------------------------------------
    RESULTS.mkdir(parents=True, exist_ok=True)
    metrics_path = RESULTS / "metrics_all.csv"
    with open(metrics_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote per-scenario metrics -> {metrics_path}")

    # ---- micro-aggregated overall metrics --------------------------------
    summary_rows = [aggregate_micro(runs) for runs in runs_by_strategy.values()]
    summary_path = RESULTS / "metrics_summary.csv"
    summary_fields = list(
        dict.fromkeys(k for row in summary_rows for k in row.keys())
    )
    with open(summary_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=summary_fields)
        w.writeheader()
        w.writerows(summary_rows)
    print(f"Wrote micro-aggregated summary -> {summary_path}")

    if scenarios_no_events:
        print(
            "Scenarios with zero ground-truth events (reported as N/A, never 0%): "
            + ", ".join(scenarios_no_events)
        )

    df = pd.DataFrame(all_rows)
    summary_df = pd.DataFrame(summary_rows)
    P.render_all(df, summary_df)
    print(f"Wrote plots -> {RESULTS / 'plots'}")


if __name__ == "__main__":
    main()
