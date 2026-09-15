# Change-score and policy specification

**Normative.** This document is the single definition of the AdaptiveSense
decision algorithm. The three run-times must implement exactly what is written
here, and nothing else:

| run-time | file |
|----------|------|
| offline scheduler (Python) | `simulator/adaptive.py` |
| on-device policy (C, ESP-IDF) | `firmware/main/change_detector.c` + `firmware/main/adaptive_scheduler.c` |
| host-side C reference build (tests) | same two C files, compiled for the host |

Every symbol used below is a configuration value. **No window, threshold or
interval may be a literal in code.** The values live in
`experiments/experiment_config.yaml` and are mirrored, value for value, into
`firmware/main/config.example.h`. `scripts/check_config_parity.py` fails the
build if the two files disagree.

Notation. For channel `c`, sample `i` is a pair `(t_i, x_i)` with strictly
increasing `t_i`. Only channels with `use = true` and a valid reading take part.

---

## 1. Baseline (EMA deviation)

The baseline is an exponential moving average of the channel, remembered for
the lifetime of the node. The *deviation* used for scoring is measured against
the baseline **before** the current sample is folded in.

```
i = 0:   ref_0 = x_0 ,  ema_0 = x_0
i > 0:   dt    = t_i - t_(i-1)
         ref_i = ema_(i-1)
         alpha = dt > 0 ? dt / (dt + baseline_tau_s) : 0
         ema_i = ema_(i-1) + alpha * (x_i - ema_(i-1))

dev_c(i) = |x_i - ref_i|
```

Rationale for the time-based coefficient: the baseline must not be forgotten
when the sampling interval is long, and it must not be updated as if the
samples were equally spaced. `ref_i = ema_(i-1)` makes `dev` a *change*
measure — with `ref_i = ema_i` a large jump would partly cancel itself.

`baseline_tau_s` is `adaptive.analyzer.baseline_tau_s`.

## 2. Standard deviation (STD)

Population (not sample) standard deviation over the samples inside the
**variety window**, i.e. all stored samples `j` with
`t_i - variety_window_s <= t_j <= t_i` (the current sample is included).

```
m      = mean(x_j)
std_c(i) = sqrt( sum_j (x_j - m)^2 / N )      if N >= 2
         = 0                                  if N < 2
```

`variety_window_s` is `adaptive.analyzer.variety_window_s`. Because the window
is measured in **seconds**, the number of samples inside it depends on the
current sampling interval — this is intentional: a fast-sampling node sees the
same physical window the offline full-rate analysis sees.

## 3. Rate of change (ROC)

**Mean** absolute rate of change over the samples inside the **ROC window**:

```
candidates = { j : t_j - t_(j-1) > 0 ,  t_i - roc_window_s <= t_j <= t_i }
roc_c(i)   = mean( |x_j - x_(j-1)| / (t_j - t_(j-1))  for j in candidates )
           = 0    if candidates is empty
```

Three decisions are recorded here because they were inconsistent in v0.1:

1. **Mean, not max.** A single noisy adjacent pair must not dominate the score.
   Mean is smoothed over the window; the window length, not the reducer, is what
   makes the indicator responsive.
2. **`roc_window_s`, not the variety window.** ROC and STD answer different
   questions — "how fast" vs "how spread" — and must use their own windows.
   `roc_window_s <= variety_window_s` is required by the config validator.
3. Each adjacent pair is counted **exactly once**. The current sample contributes
   one pair, `(i-1, i)`; it is not additionally added as a separate term.

## 4. Per-channel and overall score

```
score_c(i) = max( dev_c(i), std_c(i), roc_c(i) ) / noise_floor_c
score(i)   = max over participating channels c of score_c(i)
```

`noise_floor_c` is `adaptive.channels.<c>.noise_floor`. Dividing by the noise
floor is what makes channels comparable on one scale; `score` is therefore
dimensionless, in units of "one noise floor".

## 5. Event debounce

`event_threshold` is `adaptive.event_threshold`,
`event_min_duration_s` is `adaptive.event_min_duration_s`.

```
if score(i) > event_threshold:
        if potential_start is unset:  potential_start = t_i
        event_active = (t_i - potential_start) >= event_min_duration_s
else:
        potential_start = unset
        event_active = false
```

`detected_event(i)` is `event_active` at sample `i`. The **first** sample at
which `event_active` becomes true is an event onset (rising edge) and is used by
the upload policy (§7) and by the offline event extraction.

### Two different things are both called "an event"

They are computed from the same score by the same rules, but they are not the
same quantity, and the results table only ever reports the second one.

| | **Online event** | **Evaluation event** |
|---|---|---|
| where | firmware / live scheduler | offline, in `simulator/events.py` |
| input | one global score, `max` over all participating channels | each channel's own sampled stream, analysed separately |
| output | a single boolean `event_active` | one `Event(channel, start_s, end_s)` interval per channel |
| consumers | sampling control, the upload decision, the on-device log | event matching against the ground-truth labels, detection rate, latency |
| why | the node needs one actionable "am I in a disturbance?" bit, and debouncing it once avoids channel-by-channel chatter in the control loop | comparing strategies requires the same per-channel extraction applied to every sampled stream, so a difference in the numbers comes from the sampling, not from the extraction |

The two can legitimately disagree: a disturbance that moves only humidity raises
the online flag exactly as a temperature disturbance does, while the offline
detector attributes it to the humidity channel. A strategy's *detection rate* is
therefore a statement about information retained in the sampled stream (§10), not
an accuracy score for the firmware's flag. `docs/methodology.md` says the same
thing in the reader-facing wording.

## 6. State machine

Thresholds: `stable_threshold` (`S`), `active_threshold` (`A`),
`hysteresis_fraction` (`h`). Define the upper (entry) and lower (retention)
bounds:

```
S_hi = S * (1 + h)      S_lo = S * (1 - h)
A_hi = A * (1 + h)      A_lo = A * (1 - h)
```

Transition rule — **escalation is immediate, de-escalation is one level at a
time and hysteretic**:

```
if score >= A_hi:                                    state = ALERT
elif state == ALERT:                                 # retain, or step down one
        state = (score >= A_lo) ? ALERT : ACTIVE
elif score >= S_hi:                                  state = ACTIVE
elif state == ACTIVE:                                # retain, or step down one
        state = (score >= S_lo) ? ACTIVE : STABLE
else:                                                state = STABLE
```

Properties that the tests assert:

* a single sample with a very large score takes **STABLE → ALERT directly**
  (no intermediate ACTIVE step). v0.1 could not do this.
* the state never oscillates on a score that sits inside a hysteresis band,
  because retention uses the lower bound and entry uses the upper bound.
* **de-escalation never skips a level.** A score inside
  `[S_hi, A_hi)` always lands on ACTIVE, whether the previous state was STABLE
  or ALERT, so an ALERT cannot fall straight back to STABLE — and to the longest
  interval — while the environment is still moving. Reaching STABLE from ALERT
  therefore takes two consecutive low samples.
* escalation costs one sample per level *up* only in the sense that ALERT is
  reachable in one; there is no damping on the way up.

## 7. Interval ladder

Each state has an ordered ladder of intervals. The rung index starts at 0 on
entering a state and advances after `ladder_confirmations[state]` consecutive
evaluations spent on the current rung; the index is clamped to the last rung.

```
ALERT    : [5]                 confirmations 1  (only one rung)
ACTIVE   : [15, 10, 5]         confirmations 1  -> speeds up immediately
STABLE   : [20, 40, 60]        confirmations 2  -> backs off only when confirmed stable
```

The interval returned by `update()` is the interval **for the next sample**.
The ladder is therefore monotone in the intended direction:

* **STABLE** grows: `20 → 20 → 40 → 40 → 60 → 60 …`
* **ACTIVE** shrinks: `15 → 10 → 5 → 5 …`
* **ALERT** is constant at the minimum.

v0.1 used `active: [60, 30, 15, 5]`, so entering ACTIVE *after observing change*
returned the maximum interval and the node slowed down precisely when it should
have sped up. That is the defect this ladder removes.

State changes reset the rung index to 0 (and the confirmation counter to 0).

All ladder values are validated against `[min_interval, max_interval]`.

## 8. Upload policy

`upload_requested(i)` is the logical decision, i.e. "this sample should be
transmitted". It is true if **any** of the following holds:

| condition | config |
|-----------|--------|
| it is the **first valid sample** | `upload.upload_first_sample` |
| event onset (score just crossed into a debounced event) | `upload.on_event` |
| the state changed at this sample | `upload.on_state_change` |
| the interval changed at this sample | `upload.on_interval_change` |
| `now - last_upload_request >= heartbeat_s` | `upload.heartbeat_s` |
| `normalized_delta_c >= delta_threshold` for a participating channel `c` | `upload.delta_threshold` |

The heartbeat is evaluated **at a sample**, not on a timer, so it bounds the
reporting *gap* rather than the report period exactly: the worst-case gap is
`heartbeat_s + interval_s` during transient states, and exactly `heartbeat_s`
once the interval has settled (with the documented configuration
`heartbeat_s == max_interval == 60 s`, a settled node therefore reports every
sample).

where

```
normalized_delta_c = |x_c(i) - x_c(last_upload_request)| / noise_floor_c
```

The normalisation by `noise_floor_c` is mandatory: v0.1 compared a *raw* absolute
delta against `delta_threshold` in the firmware while the simulator divided by
the noise floor, so the same constant meant "2 noise floors" offline and
"2 °C / 2 lux / 2 hPa" on-device.

`last_upload_request` and `last_upload_values[channel]` are updated **when
`upload_requested` is true**. This keeps Python and firmware bit-comparable.

> **`upload_requested` is not `publish_call_ok`.** The firmware publishes the
> packet and records the outcome separately (`communication_publish()` return
> value). The scheduler baseline deliberately follows the *decision*, so that
> the offline simulator and the device run the same policy; the transport
> outcome is reported as its own counter and log field. Note that at QoS 0
> `publish_call_ok` means *the MQTT client accepted the request*, not that the
> broker received or delivered the packet. See `docs/methodology.md`.

## 9. Offline event extraction (not ground truth)

Detected events are extracted from the **sampled** stream by replaying the same
§1–§5 computations over the samples the node actually observed, then taking the
intervals where `event_active` is true:

```
for each participating channel c:
    run §1-§5 over the samples of channel c
    interval [t_a, t_b] where t_a is the sample that made event_active true and
    t_b is the last sample for which it was still true
    emit Event(channel=c, start_s=t_a, end_s=t_b, event_type="detected")
```

This is the **detector**. It is applied to the same sample stream for every
strategy, so all strategies are scored by the same extraction code.

## 10. Ground truth (independent of AdaptiveSense)

Ground truth does **not** come from §1–§10. It comes from the dataset generator,
which knows exactly which interval it drove which channel over. A scenario is
described by a noise-free driven signal `d_c(t)` (the deterministic offset from
the channel baseline, before measurement noise is added).

```
an interval is labelled an event on channel c iff
    |d_c(t)| >= gt_label_min_deviation[c]   continuously for at least
    gt_label_min_duration_s seconds
```

`gt_label_min_deviation` is expressed in **absolute physical units per channel**
(°C, %RH, hPa, lux) and is deliberately *not* the policy's `noise_floor`.
AdaptiveSense therefore has no influence on what counts as an event, and the
detector can genuinely fail.

Labels are written to `dataset/labels/<scenario>_events.csv`:

```csv
channel,start_s,end_s,event_type
temperature,634,1413,sudden_change
```

`event_type` is `sudden_change` (the driven deviation reaches its peak within
30 s) or `sustained_change` (anything slower), decided mechanically from the
driven signal. A scenario with no injected disturbance (e.g. a stable or a
noisy-stable scenario) produces a header-only file, which means "zero
ground-truth events" and yields `N/A` — never `0 %`.

## 11. Event matching (evaluation only)

Matching is **channel-aware** and **one-to-one**. Let `tol = event_match_tolerance_s`.

```
sort ground truth by start_s
used = {}
for g in ground_truth:
    candidates = [ d for d in detected if d not in used
                   and d.channel == g.channel
                   and g.start_s - tol <= d.start_s <= g.end_s + tol ]
    if candidates:
        pick the candidate with the smallest |d.start_s - g.start_s|
        used.add(pick)

TP = number of matched ground-truth events
FN = len(ground_truth) - TP
FP = len(detected) - TP
latency(g, d) = max(0, d.start_s - g.start_s)
```

* A detection that overlaps a ground-truth event on a **different channel** is
  not a match, and counts as a false positive.
* One detection can match at most one ground-truth event, and vice versa.
* Latency is clamped at 0 because a detection within `tol` *before* the onset is
  still a correct detection; v0.1 could report a negative latency.

## 12. Metric aggregation

Per scenario, `event_detection_rate = TP / n_gt_events`, or **N/A** when
`n_gt_events == 0`. Never 0.

Across scenarios (the headline numbers) use **micro** aggregation, i.e. pool the
raw counts — never average per-scenario rates:

```
event_detection_rate = sum(TP) / sum(GT)
missed_events        = sum(FN)
false_positive_events= sum(FP)
false_positive_per_hour = sum(FP) / sum(duration_s) * 3600
latency mean/median/p95 = computed over the pooled list of matched-pair latencies
```

`average_sampling_interval_s` is `mean(diff(sample_timestamps))`, i.e. the mean of
the `N-1` real intervals; it is `N/A` for a single sample. v0.1 used
`duration / N`, which silently assumes uniform sampling.

## 13. Energy

No physical energy unit is reported. The available quantities are

* `number_of_uploads` — packets requested/transmitted,
* `estimated_payload_bytes` = uploads × `payload_bytes_per_upload`,
* `upload_energy_proxy` = uploads × `upload_energy_units_per_upload`
  (dimensionless proxy units).

`upload_energy_units_per_upload` is an invented constant. It must never be
reported in joules or mJ until a hardware measurement exists; v0.1 named the
column `energy_proxy_mj`, which implied a measurement that was never made.
