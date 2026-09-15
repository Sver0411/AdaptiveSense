# Results

All files under `results/` are **simulation results** produced by
[`analysis/analyze.py`](../analysis/analyze.py) on the synthetic ground-truth
datasets in `dataset/raw/`.

## Status

- **Simulation results**: available (see below).
- **Hardware / on-device results**: `Not measured yet.` (TBD after the ESP32
  node is built and a real collection campaign is run).

Hardware results, when they exist, will be stored separately (e.g. under
`results/hardware/`) and will be clearly labelled so they are never confused
with simulation numbers.

## Contents

| path | contents |
|------|----------|
| `metrics_all.csv` | per-run (scenario x strategy) metric rows |
| `metrics_summary.csv` | metrics averaged across scenarios, per strategy |
| `sim/<scenario>/<strategy>.csv` | the samples each strategy actually took/uploaded |
| `plots/sampling_count.png` | samples per scenario/strategy |
| `plots/communication_reduction.png` | upload reduction vs full 1 Hz |
| `plots/event_detection.png` | detection rate + false positives |
| `plots/detection_latency.png` | average detection latency |
| `plots/accuracy_efficiency_tradeoff.png` | detection vs communication trade-off |

## Regenerate

```bash
python analysis/analyze.py
```

## Energy metric

The `energy_proxy_mj` column is an **estimated / proxy** figure, not a real
power measurement. It is `uploads * energy_per_upload_mj` with
`energy_per_upload_mj` a configurable constant (see
`experiments/experiment_config.yaml`). Re-measure energy on hardware with a
power monitor when real figures are needed.