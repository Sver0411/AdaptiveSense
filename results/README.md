# Results

Everything under `results/` is a **synthetic simulation result** produced by
[`analysis/analyze.py`](../analysis/analyze.py) from the benchmark in
`dataset/`. Nothing here is a hardware measurement.

| status | |
|--------|--|
| Simulation results | available (below) |
| Hardware / on-device results | **`Not measured yet.`** |
| Energy measurements | **`Not measured yet.`** |

Hardware results, when they exist, go under `results/hardware/` and are labelled
as measurements, so they can never be confused with these numbers.

## Contents

| path | contents |
|------|----------|
| `metrics_all.csv` | one row per (scenario × strategy) |
| `metrics_summary.csv` | micro-aggregated overall numbers per strategy |
| `sim/<scenario>/<strategy>.csv` | the samples each strategy took — one row per sample |
| `plots/*.png` | five figures |

## Columns

| column | meaning |
|--------|---------|
| `duration_s` | scenario length |
| `n_ground_truth` | number of 1 Hz samples in the full-resolution signal |
| `number_of_samples` | readings the strategy took |
| `sampling_reduction` | `1 − samples / n_ground_truth` |
| `number_of_uploads` | packets the policy requested |
| `application_upload_reduction` | `1 − uploads / n_ground_truth` — **application payloads only**, see below |
| `estimated_payload_bytes` | uploads × the measured size of one payload (246 bytes) |
| `average_sampling_interval_s` | `mean(diff(sample_timestamps))`; `N/A` for a single sample |
| `n_gt_events` | labelled events in the scenario |
| `true_positives` | one-to-one matched events |
| `missed_events` | labelled events with no match (FN) |
| `false_positive_events` | detections with no match (strict definition) |
| `redundant_detections` | of those FPs, the ones inside a same-channel labelled event — repeat triggers, not spurious alarms |
| `false_alarm_events` | `false_positive_events − redundant_detections` |
| `event_detection_rate` | `TP / GT`, **empty (N/A) when `n_gt_events == 0`** |
| `false_positive_per_hour` | `FP / duration × 3600` |
| `avg_detection_latency_s` | mean onset-to-onset latency of matched pairs, clamped at 0 |
| `median_detection_latency_s` | median of the same list |
| `p95_detection_latency_s` | 95th percentile of the same list |
| `upload_energy_proxy` | uploads × a configured dimensionless constant |

### `application_upload_reduction` is not a radio-traffic reduction

The metric counts **application payload uploads** only. It excludes everything the
radio does on its own:

- Wi-Fi beacon reception
- TCP ACKs
- MQTT keepalive (PINGREQ / PINGRESP)
- MQTT protocol overhead
- reassociation and DHCP traffic

A node whose uploads drop by 97 % has **not** reduced total radio traffic by 97 %;
the MQTT keepalive alone guarantees background traffic. The column is named after
what it measures for exactly this reason, and `upload_energy_proxy` carries the
same qualification.

### What the detection columns measure

The detection columns describe **how much event information survived in the sampled
stream**, scored by one shared offline per-channel detector. They are not an
accuracy measurement of the firmware's global `event_active` flag — that flag drives
scheduling and the upload decision, while the offline detector exists to score every
strategy the same way. See
[`../docs/change_score_spec.md`](../docs/change_score_spec.md) for both quantities.

`metrics_summary.csv` uses **micro** aggregation: `sum(TP)/sum(GT)`, pooled
counts, and latency recomputed over the pooled matched-pair list — never the mean
of per-scenario rates. `scenarios` reports how many scenarios were pooled and
`matched_latency_samples` how many latencies the p95 is based on (with 18 matched
pairs in total, the p95 is indicative rather than statistically strong).

The sample logs contain exactly one row per sample:

```
timestamp,temperature,humidity,pressure,light,state,interval_s,score,upload_requested,detected_event
```

Fixed-rate strategies fill `state` with `FIXED`, put their period in `interval_s`
and leave `score` empty (they have no change score).

## Energy

There is no energy column. `upload_energy_proxy` is
`uploads × upload_energy_units_per_upload`, an invented dimensionless
constant. It is `Not a measurement`, and an earlier revision of this project
printed the same quantity in millijoules, which implied a measurement that had
never been taken. Real energy requires a current monitor on hardware; see
[`../docs/hardware.md`](../docs/hardware.md).

## Regenerate

```bash
python dataset/generate_dataset.py
python analysis/analyze.py
```

## What "reproducible" means here

The guarantee covers the **numeric artefacts**: the raw signals, the ground-truth
labels and every metrics/sample CSV. Regenerating them must not change a single
byte, and CI enforces that with
`git diff --exit-code -- 'dataset/**/*.csv' 'results/**/*.csv'`.

The **figures are checked differently on purpose.** The byte content of a PNG
depends on the matplotlib / freetype / harfbuzz builds, not on the experiment, so
demanding byte-identity would pin the plotting library rather than the science
(and would make the committed bytes churn on every dependency bump). What CI does
verify is that the pipeline still renders all five figures and that none of them
collapses to an empty image. The version string matplotlib would otherwise embed
in a `tEXt` chunk is suppressed (`analysis/plots.py::_save`), so a dependency
change cannot masquerade as a change in the results — `tests/test_plots.py` pins
that.

