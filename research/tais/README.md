# TAIS — Trust-Aware Importance Scheduler

**Status: research specification only. No TAIS implementation or new performance result is claimed yet.**

TAIS is a proposed research prototype for studying how sensor-health information should constrain event-aware adaptive sampling in resource-constrained agricultural IoT.

The project is staged under research/tais/ on the research/tais-design branch of AdaptiveSense so it can reuse the existing, version-controlled simulator and baselines without copying or silently changing them. This is a research workspace, not yet a separate GitHub repository.

## Research question

When one or more sensor channels become unreliable, can a scheduler use the existing AdaptiveSense change score to preserve important events from healthy channels, reduce fault-induced false event reports, and keep sampling/upload costs bounded?

We will explicitly test both:
- an environmental event occurring on a healthy channel while another channel is faulty;
- an environmental event and a sensor fault occurring on the same channel.

The second case is intrinsically ambiguous for a single sensor. The project must measure that limitation, not claim to solve it universally.

## Reuse of existing work

- [AdaptiveSense](https://github.com/Sver0411/AdaptiveSense): change scoring, event labels, replay simulator, fixed-rate baselines, and metric definitions.
- [SensorTrust](https://github.com/Sver0411/SensorTrust): the actual C11 sensor-health detector. The implementation should pin a specific commit or use a submodule; do not silently rewrite its semantics.
- [Smart-Agriculture-Edge-AI](https://github.com/Sver0411/Smart-Agriculture-Edge-AI): eventual integration target only after the standalone research results are understood.

## Planned evidence

1. Existing synthetic AdaptiveSense traces with independently generated event labels.
2. Controlled injection of sensor faults into the observed stream while preserving separate event and fault ground truth.
3. An external check using the public greenhouse dataset by Hull et al. (2023). Its 5-minute resolution and actuator-state labels make it suitable for longer-interval robustness/reconstruction checks, not for claims about sub-minute events.

## Non-claims

- No novelty claim is made before a fuller literature review.
- Simulation outcomes are not hardware measurements.
- Upload-count reduction is not the same as radio-traffic or measured-energy reduction.
- The SensorTrust health score is heuristic, not a calibrated fault probability.
- The greenhouse actuator state is an evaluation reference/proxy, not a complete ground truth for every possible environmental event.

## Documents

- [Research question and scope](docs/RESEARCH_QUESTION.md)
- [Related work](docs/RELATED_WORK.md)
- [Algorithm specification](docs/ALGORITHM_SPEC.md)
- [Experiment protocol](docs/EXPERIMENT_PROTOCOL.md)
- [Codex implementation handoff](CODEX_HANDOFF.md)

## Current baseline snapshot

At the current AdaptiveSense main snapshot, the README reports 18/24 labelled events detected by AdaptiveSense (75.0%) and 22/24 by Fixed-5s / Fixed-10s (91.7%) on the existing synthetic benchmark. These are existing synthetic replay results, not TAIS results. Always regenerate and verify the current baseline before making a comparison.
