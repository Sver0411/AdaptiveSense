# Methodology

## Research question

> Can change-aware adaptive sampling reduce sensing and communication overhead on
> resource-constrained IoT devices while preserving event-detection performance?

The hypothesis:

  - when the environment is stable, the node can sample and transmit *much* less
  - when change increases, the sampling rate increases dynamically
  - sustained events are detected reliably
  - thus: same event detection → less work → less energy use.

## Algorithm

### Change-awareness is built on two components:

1. **Per-channel instability scoring**. For each sensor reading, the code
   calculates a score that is the maximum over three different change
   indicators, all normalised by that channel's typical noise:

   - `dev = |current - EMA baseline| / noise_floor`
   - `std = rolling_window_standard_deviation / noise_floor`
   - `roc = recent_mean_abs_delta / noise_floor`
   - `score = max(dev, std, roc)`

   The EMA baseline is kept forever (i.e. `alpha = dt / (dt + tau)`), so a
   change after a long stable interval is still detected properly (the baseline
   doesn't get forgotten, unlike a strict fixed window).

2. **Three-state machine with hysteresis**. The total score (max over channels)
   is used to update state:

   - **STABLE**: score < `stable_threshold` (relative hysteresis). The
     gradually increases the interval: 20 → 40 → 60 s.
   - **ACTIVE**: score between stable and active thresholds. Interval decreases
     steadily from 60 → 30 → 15 → 5 s.
   - **ALERT**: score > `active_threshold`. Interval is always 5 s (maximum
     sampling rate).

   Relative hysteresis is applied: the lower/upper bands for transitions are
   shifted by the hysteresis fraction to prevent rapid oscillation near the
   thresholds.

3. **Upload policy**: A sample is transmitted if:

   - it is the first sample after an event is detected,
   - the state just changed,
   - the interval just changed,
   - it has been `heartbeat_s` seconds since the last upload (periodic heartbeat),
   - the signal has changed more than `delta_threshold` from the last upload
     even if no state change.

4. **Event detection**: an event is flagged when the score stays above
   `event_threshold` for at least `event_min_duration_s` (debouncing).

All parameters are collected in `experiments/experiment_config.yaml` for Python
and mirrored in the firmware via `firmware/main/config.h`. Changing the
configuration does not require touching any code.

## Baselines compared

Five fixed-interval strategies:

- Fixed-5s
- Fixed-10s
- Fixed-20s
- Fixed-40s
- Fixed-60s

## Metrics

Every strategy is evaluated on the same ground-truth data. Metrics:

| metric | definition |
|--------|------------|
| `number_of_samples` | how many readings the node would take |
| `sampling_reduction` | `1 - samples / full_ground_truth_samples` |
| `number_of_uploads` | how many packets transmitted |
| `communication_reduction` | `1 - uploads / full_ground_truth_samples` |
| `average_sampling_interval_s` | mean interval between samples |
| `event_detection_rate` | fraction of ground-truth events that were detected |
| `missed_events` | count of ground-truth events not detected |
| `false_positive_events` | count of detected events that do not overlap any ground-truth event |
| `false_positive_per_hour` | false positives per hour of observation |
| `avg_detection_latency_s` | average time from gt event onset to first overlapping detected event |
| `median_detection_latency_s` | median detection latency |
| `energy_proxy_mj` | number of uploads × configurable constant per upload (**proxy only**) |

All energy estimates currently rely on this proxy measurement. Real hardware
energy measurements should be measured and reported separately as per
docs/hardware.md.

## MQTT payload schema

The JSON payload transmitted over MQTT matches:

```json
{
  "device_id": "node-01",
  "timestamp": 123456,
  "temperature": 24.1,
  "humidity": 45.2,
  "pressure": 1012.2,
  "light": 320.0,
  "sampling_interval": 20,
  "state": "STABLE",
  "event": false
}
```
