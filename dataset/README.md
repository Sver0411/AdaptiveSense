# Dataset

Two things live here:

- `raw/` — the synthetic 1 Hz sensor signals, one CSV per scenario.
- `labels/` — the **independent** ground-truth event labels for those signals,
  one CSV per scenario.

Both are produced by [`generate_dataset.py`](generate_dataset.py).

> These are **synthetic** datasets. They are not real sensor measurements, and no
> result derived from them is a measurement.

## Format

`raw/<scenario>.csv` — one row per second:

```csv
timestamp,temperature,humidity,pressure,light
0,24.07,44.77,1012.35,321.2
```

| column | unit | channel |
|--------|------|---------|
| timestamp | s | time index, 0-based at 1 Hz |
| temperature | °C | BME280 temperature |
| humidity | %RH | BME280 humidity |
| pressure | hPa | BME280 pressure |
| light | lux | BH1750 (not implemented in the driver) |

`labels/<scenario>_events.csv`:

```csv
channel,start_s,end_s,event_type
temperature,634,1413,sudden_change
```

`event_type` is `sudden_change` (the driven deviation reaches its peak within
30 s) or `sustained_change` (anything slower), decided mechanically.

## How the labels are produced — and why they are independent

The generator drives each scenario from an explicit, **noise-free** signal
(`driven_temp`, `driven_light`, plus a humidity coupling). Measurement noise is
added afterwards. Labels are computed from that driven signal with an absolute
rule taken from the configuration:

```
an interval is an event on channel c iff
    |driven_offset_c(t)| >= evaluation.gt_label_min_deviation[c]
    continuously for at least evaluation.gt_label_min_duration_s seconds
```

Defaults: `temperature 1.5 °C`, `humidity 8 %RH`, `pressure 3 hPa`,
`light 250 lux`, duration `10 s`.

Because this rule uses **physical units** and the generator's own signal, the
AdaptiveSense score cannot influence what counts as an event. An earlier revision
of this project derived the ground truth by running the AdaptiveSense score over
the full-resolution signal, which made the evaluation circular — see
[`../docs/audit_v0.2.md`](../docs/audit_v0.2.md), issues #6 and #8.

A scenario with nothing injected produces a **header-only** label file. That means
"zero ground-truth events" and yields `N/A` in the metrics, never `0 %`.

Scenarios deliberately inject a coupled humidity response, so most temperature
events come with a humidity label. That is honest — the generator really does
move both channels — but it means the labelled events are not 24 independent
physical phenomena. Per-channel results are reported separately so the coupling
is visible.

## The seven scenarios

| file | duration | rows | labelled | injected content |
|------|----------|-----:|---------:|------------------|
| `scenario_a_stable.csv` | 2 h | 7200 | 0 | nothing (only a 0.002 °C/slow sinusoidal drift) |
| `scenario_b_sudden.csv` | 30 min | 1800 | 2 | one abrupt +5 °C step at t = 632 s, held to 1210 s, recovered by 1500 s |
| `scenario_c_mixed.csv` | 2 h | 7200 | 4 | ±0.9 °C wobble (sub-threshold), +4.5 °C step at 3300 s, −2.5 °C step at 5940 s |
| `scenario_d_repeated.csv` | 1 h | 3600 | 14 | 6 irregular temperature steps + 2 light bursts |
| `scenario_e_short_event.csv` | 30 min | 1800 | 2 | one 26 s spike at t = 1560 s, after 26 min of stability |
| `scenario_f_noisy_stable.csv` | 20 min | 1200 | 0 | nothing, but noise ≈ 7× the configured temperature noise floor |
| `scenario_g_slow_drift.csv` | 1 h | 3600 | 2 | +3 °C over 30 min, hold, return |

Scenarios E, F and G are the ones the policy is expected to do badly on: a short
event that can fall between two samples, a mis-parameterised noise floor that
provokes false alarms, and a drift slower than the EMA baseline. They are there so
the benchmark can show failure — see the README's *Limitations*.

## Two units: 13 disturbances, 24 labels

The benchmark is described in two different units, and they are not
interchangeable:

| unit | count | what it is |
|------|------:|------------|
| **injected physical disturbances** | **13** | a maximal interval during which the generator drives *any* channel beyond its labelling threshold |
| **channel-level labels** | **24** | one per `(channel, interval)` pair — what the evaluation matches against |

Humidity is coupled to temperature by the generator, so most disturbances produce
two labels. Per-scenario: A 0/0, B 1/2, C 2/4, D 8/14, E 1/2, F 0/0, G 1/2.

Both counts are printed by `python dataset/generate_dataset.py` and asserted in
`tests/test_events.py::test_channel_labels_come_from_fewer_physical_disturbances`,
so neither can drift silently. Do not quote one as the other.

## Regenerating

```bash
python dataset/generate_dataset.py
```

The generator is seeded per scenario (using `zlib.crc32` of the scenario name, not
Python's randomised `hash()`), and the labelling rule is absolute, so the outputs
— raw signals **and** labels — are reproducible byte for byte. CI enforces this
with `git diff --exit-code`.

## Size note

Each raw file is roughly 0.1–0.3 MB, so no large binary artifacts are committed.
Larger collections made on real hardware should live outside the repository or on
a data server.
