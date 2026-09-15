# Experiment Protocol

The protocol defines the procedure for collecting real data and reproducing the
results from the paper.

## Prerequisites

- ESP-IDF v5.0 or later, `idf.py` in the PATH.
- Supported hardware: ESP32-S3 N16R8 (or any ESP32 with I2C + Wi-Fi).
- BME280 temperature/humidity/pressure sensor connected to the I2C pins in
  `config.h`.
- MQTT broker running on the network reachable via Wi-Fi.
- The Python analysis environment (`.venv`, `requirements.txt`).

## Step 1: Ground-truth data collection

To collect a real ground-truth dataset for the study:

1. Flash the firmware with `CONFIG_AS_DEFAULT_INTERVAL 1` (1 s sample interval)
   and configure the Wi-Fi/MQTT broker in `firmware/main/config.h`.
2. Deploy the node in the location of interest.
3. Run `server/mqtt_collector.py` to capture the full-resolution ground truth.
4. Save the captured CSV to `dataset/raw/` with a descriptive name.

When the full-resolution dataset is collected, the offline simulator can replay
all strategies over the *exact same* sequence of real sensor readings to
ensure a fair comparison.

## Step 2: Offline replay

```bash
. .venv/bin/activate
python analysis/analyze.py
```

The `results/` directory will contain:

- `metrics_all.csv` (per-run statistics)
- `metrics_summary.csv` (aggregated by strategy)
- the five figures under `results/plots/`.

All results are reproducible because the experiment configuration lives in a
single file (`experiments/experiment_config.yaml`) and the random seed is fixed.

## Step 3: AdaptiveSense on-device evaluation

1. Re-flash the firmware with the adaptive sampling config (the default from
   `config.example.h` uses the same parameters as the simulator).
2. Let the node run for the same total duration as the ground truth data.
3. Collect the MQTT log via the collector.
4. Compare:

   - number of samples taken by the node (on-device)
   - number of MQTT packets transmitted
   - event detection latency (measured as the difference between the event
     start in the full-resolution ground truth and the first on-device
     detection)
   - detection rate (fraction of ground-truth events detected).

## Three scenarios designed for the study

Three scenarios are pre-generated (synthetic ground truth):

### Scenario A: Stable environment

Goal: check whether and how much sampling/communication reduction AdaptiveSense
achieves when nothing much changes.

- Expected outcome: large reduction (around 98% for a 2 h run), 0 missed events,
  0 false positives because there are no events.

### Scenario B: Sudden environmental change

Goal: check whether AdaptiveSense detects the event with acceptable latency
despite the low sampling rate during the preceding stable period.

- Expected outcome: detects the event, latency is still much lower than the
  coarsest fixed interval, communication reduction is similar to Fixed-20s but
  detection rate matches Fixed-5s.

### Scenario C: Mixed workload

Goal: realistic workload with all phases:

- stable baseline
- small-amplitude slow oscillations
- one large abrupt change
- recovery
- final slow drift

Expected outcome: overall communication reduction similar to Fixed-40s–Fixed-60s
but event detection rate matches fixed high-rate sampling (Fixed-5s/10s).

## Reproducibility notes

- All code is version controlled; the generator is seeded, config is explicit.
- The synthetic datasets provided in `dataset/raw/` are reproducible by running
  `dataset/generate_dataset.py`; the random seed is fixed at `42`.
- Changing any parameter only requires editing `experiments/experiment_config.yaml`
  and re-running `analysis/analyze.py`; no code changes are needed.
