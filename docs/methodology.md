# Methodology

The normative definition of the algorithm is
[`change_score_spec.md`](change_score_spec.md). That file is the specification;
this one explains how the experiment around it is run and how the numbers in
`results/` are produced.

## Hypothesis

- When the environment is stable, the node can sample and transmit much less.
- When change increases, the sampling rate should increase — promptly.
- Sustained events should still be detected.
- Therefore: comparable detection at lower cost.

The simulation results in the README show this holds *partially*: the cost
reduction is large (97.1 % fewer uploads) but detection is not preserved in every
scenario, and the policy lands on the fixed-rate trade-off curve rather than above
it.

## Where the algorithm is defined

| run-time | files |
|----------|-------|
| offline scheduler | `simulator/scoring.py` (score), `simulator/adaptive.py` (policy) |
| on-device policy | `firmware/main/change_detector.c`, `firmware/main/adaptive_scheduler.c` |
| host-side reference build | the two C files above, compiled for the host |

`simulator/scoring.py` is the **only** Python implementation of the score, and
both the live scheduler and the offline detector call it. In v0.1 the score
existed in three places with three different definitions of the rate-of-change
term; that is now structurally impossible.

## Baselines

Five fixed-period strategies — Fixed-5s, Fixed-10s, Fixed-20s, Fixed-40s,
Fixed-60s. Each samples and uploads at a constant interval, so it represents the
"sample less" answer with no change awareness at all.

## Ground truth

Ground truth comes from `dataset/generate_dataset.py`, not from the scoring code.
The generator knows which interval it drove which channel over, so it can label
events from its own **noise-free** driven signal with an absolute rule:

```
an interval is an event on channel c iff
    |driven_offset_c(t)| >= gt_label_min_deviation[c]  continuously for at least
    gt_label_min_duration_s seconds
```

`gt_label_min_deviation` is in physical units (°C, %RH, hPa, lux) and is
deliberately *not* the policy's noise floor, so AdaptiveSense cannot influence
what counts as an event. Labels are committed under `dataset/labels/`.

v0.1 instead ran the AdaptiveSense score over the full-resolution signal to
produce the "ground truth". A strategy was then evaluated on whether it could
reproduce the output of its own scoring function, which is circular and is why
that revision could report perfect detection.

## Matching and aggregation

Channel-aware, one-to-one matching
([`change_score_spec.md` §11](change_score_spec.md)), with the tolerance in
`evaluation.event_match_tolerance_s`. Per-scenario rates are reported as `N/A`
when the scenario contains no labelled event. Overall numbers are micro
aggregates: `sum(TP)/sum(GT)`, pooled counts, and latency recomputed over the
pooled matched-pair list.

### What the detection numbers measure — and what they do not

There are two different things in this project that are both called "an event",
and the results table only ever reports the second one:

| | Online event | Evaluation event |
|---|---|---|
| where | firmware / live scheduler | offline, `simulator/events.py` |
| input | one global score, max over all channels | each channel's own sampled stream |
| output | a single `event_active` boolean | one `(channel, start_s, end_s)` interval per channel |
| used for | scheduling, the upload decision, the device log | detection rate and latency versus the labels |

Consequently:

- The detection rate is a **sampling-quality** metric: how much of the
  environmental event structure survives in the samples a strategy chose to take.
- It is **not** an accuracy measurement of the firmware's `event_active` flag, and
  the two can legitimately disagree — a humidity-only disturbance raises the
  online flag exactly as a temperature disturbance does, while the offline
  detector attributes it to the humidity channel.

This is deliberate: the research question is about *adaptive sampling*, not about
classifier accuracy, and scoring every strategy with one shared offline detector
means a difference between strategies comes from the sampling and not from the
extraction.

### Two units: disturbances and labels

The benchmark contains **24 channel-level labels derived from 13 injected physical
disturbances**. A single physical disturbance may create labels on more than one
channel, because the generator couples humidity to temperature. Both counts are
printed by `dataset/generate_dataset.py` and asserted in `tests/test_events.py`, so
neither can drift silently. Do not quote one as the other.

## Cost accounting

| column | meaning |
|--------|---------|
| `number_of_samples` | readings the node would take |
| `number_of_uploads` | packets the policy requested |
| `estimated_payload_bytes` | uploads × the measured size of one payload |
| `upload_energy_proxy` | uploads × a configured dimensionless constant |

`payload_bytes_per_upload` is a **representative application payload size**, not an
exact wire length: 244 bytes is what the firmware's own payload builder produces
for the shipped build (SHT30 + BH1750, normal indoor readings, a 10-digit
millisecond timestamp), and `tests/test_communication_payload.py` re-measures it
from the same source. Real payloads move in a narrow band around it — 242-247
bytes across dark, indoor, bright and large-lux readings, and a wider timestamp —
because the JSON is free-form and its length follows the digits in the values. The
constant is good enough to compare strategies against each other; it is not good
enough for a byte-exact radio-energy claim, which is not made.

No physical energy unit is reported.

### `application upload reduction` is not a radio-traffic metric

The reduction metric is

```
1 − number_of_application_uploads / number_of_ground_truth_samples
```

and it excludes everything the radio does on its own: Wi-Fi beacon reception, TCP
ACKs, MQTT keepalive PINGREQ/PINGRESP, MQTT protocol overhead, reassociation and
DHCP traffic. A node whose uploads drop by 97 % has **not** reduced total radio
traffic by 97 %; the keepalive alone guarantees background traffic, and in this
architecture that traffic can wake the chip out of light sleep.

The name is therefore `application_upload_reduction`, not "communication
reduction", and the same qualification applies to `upload_energy_proxy`.

## `upload_requested` is not `publish_call_ok`

The policy decides whether a sample *should* be transmitted
(`upload_requested`). The transport layer reports whether the MQTT client accepted
the request (`publish_call_ok`). On device these are logged and counted separately:

```
cycle=12 t=220.0 state=1 interval=15.0s score=6.71 event=0 \
upload_requested=1 publish_call_ok=1 temp=25.02 hum=44.88
```

```
I (…) comm: publish stats: requested=152 call_ok=148 call_failed=4 mqtt_connected=1
```

**`publish_call_ok` is not delivery confirmation.** At QoS 0
`esp_mqtt_client_publish()` returns once the client has accepted the request into
its outbound queue. It says nothing about whether the broker received the packet,
and certainly nothing about whether an application processed it. Confirming
delivery would need QoS 1, `MQTT_EVENT_PUBLISHED` and server-side receipt
validation — all future work, none of it implemented here.

The scheduler's upload baseline follows the *decision*, not the transport outcome,
so that the offline simulator and the device run the same policy; that is what the
Python/C parity test checks. A non-zero `call_failed` in the log is what makes a
dead broker diagnosable.

## MQTT payload schema

```json
{
  "device_id": "node-01",
  "timestamp": 1234567890,
  "temperature": 24.10,
  "humidity": 45.20,
  "pressure": null,
  "light": 152.50,
  "sampling_interval": 60.0,
  "state": "STABLE",
  "event": false,
  "valid": {
    "temperature": true,
    "humidity": true,
    "pressure": false,
    "light": true
  }
}
```

That example is the physical build: an SHT30 for temperature and humidity, a
BH1750 for light, and no pressure sensor on the board. Two details carry meaning:

- **`null` for an unavailable channel.** `pressure` has no device behind it and is
  `null` rather than `0`; a `0` would be indistinguishable from a genuine reading
  of zero. `light` is a number here because a BH1750 is fitted — if it is absent
  or fails, `light` goes back to `null` while temperature and humidity keep being
  reported, because the light channel is optional.
- **The `valid` map is always present**, so a consumer never has to infer which
  channels the running configuration can actually measure.

`tests/test_communication_payload.py` pins both, and re-measures the payload size
that `payload_bytes_per_upload` claims.

## Known measurement weaknesses

Recorded here rather than buried in the results:

1. 24 labelled events over 26 400 s is a small benchmark.
2. Scenario F deliberately uses a noise amplitude ~7× the configured temperature
   noise floor, which is a mis-parameterisation the policy cannot absorb. It is
   the source of most false alarms for *every* strategy.
3. The detected-event rule is threshold-and-debounce, so a "detection" means the
   score crossed a threshold for long enough — not that a change point was
   located.
4. Parameters are hand-selected and known not to be jointly optimal; see
   limitation 6 in the README.
