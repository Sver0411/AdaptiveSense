# Research question and scope

## Working title

**Trust-Gated Event-Aware Adaptive Sampling under Sensor Faults for Resource-Constrained Agricultural IoT**

Short project name: **TAIS** (Trust-Aware Importance Scheduler). The name is provisional.

## Main research question

When a resource-constrained IoT node monitors several environmental channels, how should sensor-health state constrain an event-aware sampling and upload policy so that faults on one channel do not unnecessarily suppress important observations from healthy channels, while fault-induced false reports and resource costs remain bounded?

This deliberately builds on the existing AdaptiveSense importance signal rather than claiming that event-aware sampling itself is new.

## Research questions

- **RQ1 — False reports:** Compared with AdaptiveSense without trust gating, does the proposed policy reduce event-like false detections caused by injected sensor faults at a comparable application-upload budget?
- **RQ2 — Channel isolation:** When one channel is faulty but a different healthy channel experiences a real labelled event, how much event recall and detection latency are preserved?
- **RQ3 — Same-channel ambiguity:** When a true environmental event overlaps a fault on the same sensor, what is the trade-off between avoiding false reports and retaining the real event? Which cases are not identifiable from a single channel?
- **RQ4 — Resource cost:** What are the effects on sample count, application upload count, estimated payload bytes, and event-retention metrics?
- **RQ5 — Optional threshold cue:** On a greenhouse trace with documented fan-control thresholds, does a configurable proximity-to-control-threshold cue add value beyond the existing change score? This is exploratory and must not be part of the main claim unless the experiment supports it.

## Working hypotheses (to test, not assumed true)

- **H1:** Trust gating reduces fault-induced false event reports compared with the same scheduler without trust information.
- **H2:** Channel-local health gating preserves events on healthy channels better than a node-wide rule that lets any faulty channel dominate the global score.
- **H3:** Same-channel event-plus-fault cases expose an unavoidable ambiguity; confirmation sampling may improve some cases but cannot guarantee correct separation.
- **H4:** Any reduction in false reports must be evaluated together with event recall, latency, and upload cost. A policy that suppresses every alert is not a successful policy.

## Candidate contributions

These are candidates for evaluation, not novelty claims:

1. A clearly specified trust-gated adaptive scheduler that reuses the current change score and keeps channel-health decisions separate from environmental-event decisions.
2. A reproducible benchmark with independent ground truth for environmental events and independently injected sensor faults, including co-occurring event/fault cases.
3. An evaluation of channel isolation: whether faults on one channel suppress events on other healthy channels.
4. A public real-greenhouse trace as external signal validation, with explicit limits on its 5-minute resolution and actuator-state proxy labels.

A literature review must determine how these differ from existing fault-tolerant sampling, critical-event sampling, and anomaly-aware WSN methods. Do not write “first”, “novel”, or “state of the art” without a defensible comparison.

## Scope decisions

**In scope**
- Python simulation and reproducible experiment runner.
- Existing AdaptiveSense score and scheduler as a baseline.
- SensorTrust's actual C11 core, pinned to a commit or referenced as a submodule.
- Fault injection: range, stuck, spike, drift, missing; stable offset is a documented blind-spot probe.
- Channel-aware event metrics, false alarms, latency, sample/upload counts, and reconstruction error.
- Public greenhouse dataset evaluation without requiring hardware purchase.

**Out of scope for v1**
- Reinforcement learning or deep learning.
- Claiming measured energy savings without a current monitor.
- Firmware integration before the simulation results are understood.
- Treating a heuristic health score as a calibrated probability.
- Treating a single sensor as able to distinguish all real environmental trends from sensor drift.

## Decision gate before code

Proceed to implementation only after:
1. baseline commands and current results are confirmed;
2. the exact SensorTrust version is pinned;
3. the event/fault injection protocol is independent of policy outputs;
4. all comparison methods and primary metrics are fixed;
5. the optional threshold-proximity experiment is kept separate from the primary experiment.
