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

## Cost accounting

| column | meaning |
|--------|---------|
| `number_of_samples` | readings the node would take |
| `number_of_uploads` | packets the policy requested |
| `estimated_payload_bytes` | uploads × configured payload size |
| `communication_energy_proxy` | uploads × a configured dimensionless constant |

No physical energy unit is reported. `communication_energy_proxy` is an invented
constant and is only meaningful for comparing strategies within one run.

## `upload_requested` is not `publish_success`

The policy decides whether a sample *should* be transmitted
(`upload_requested`). The transport layer decides whether it *was*
(`publish_success`). On device these are logged and counted separately:

```
cycle=12 t=220.0 state=1 interval=15.0s score=6.71 event=0 \
upload_requested=1 publish_success=1 temp=25.02 hum=44.88
```

```
I (…) comm: publish stats: requested=152 ok=148 failed=4 mqtt_connected=1
```

The scheduler's upload baseline follows the *decision*, not the transport
outcome, so that the offline simulator and the device run the same policy; this
is what the Python/C parity test checks. A non-zero `publish_failed` with the
counters visible in the log is what makes a dead broker diagnosable — in v0.1 a
disconnected broker was indistinguishable from "nothing to send".

## MQTT payload schema

```json
{
  "device_id": "node-01",
  "timestamp": 123456,
  "temperature": 24.10,
  "humidity": 45.20,
  "pressure": 1012.20,
  "light": 320.0,
  "sampling_interval": 20.0,
  "state": "STABLE",
  "event": false
}
```

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
