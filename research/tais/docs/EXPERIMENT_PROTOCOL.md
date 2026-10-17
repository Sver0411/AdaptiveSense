# Experiment protocol

## Phase 0 — freeze and reproduce the baseline

From the repository root, run the existing commands:

- python -m pytest tests/ -v
- python scripts/check_config_parity.py
- python dataset/generate_dataset.py
- python analysis/analyze.py

Record the commit SHA, Python version, dependency versions, test summary, and checksums of the baseline result CSVs. Do not modify the existing AdaptiveSense baseline results to make TAIS look better.

## Phase 1 — controlled synthetic benchmark

### Signal and independent labels

Start from the existing 1 Hz synthetic scenarios and generator. Extend the generator in a separate TAIS directory or config; do not silently change the seven existing scenarios.

For each run, preserve separate artifacts:
1. latent environmental signal before measurement noise/fault injection;
2. observed signal after noise;
3. event labels generated from the latent signal using a predeclared physical-unit deviation and duration rule;
4. fault labels generated from the fault-injection schedule;
5. sampled observations and scheduler/upload decisions.

Never derive ground truth from AdaptiveSense or TAIS detector output.

### Fault cases

At minimum:
- clean channel / no fault;
- out-of-range value;
- isolated spike and spike recovery;
- stuck/flatline;
- gradual drift;
- missing/invalid readings;
- stable in-range offset (documented SensorTrust blind-spot probe).

Test three separate relationships:
- fault without a true environmental event;
- true event on a healthy channel while another channel is faulty;
- true event and fault overlapping on the same channel.

Include fault recovery and more than one fault intensity/duration. The single-channel ambiguity case must be reported separately, not hidden in pooled averages.

### Seeds and split

Use deterministic seeds. A proposed initial split is seeds 0–9 for development, 10–19 for validation/parameter selection, and 20–49 as held-out test seeds. Fix this split before examining held-out results. If the implementation makes the 50-seed run too expensive, justify any reduction before final testing.

### Strategies

1. Fixed-rate sampling at all existing intervals.
2. AdaptiveSense as currently implemented.
3. AdaptiveSense with SensorTrust in diagnostic-only mode (health is logged but does not affect decisions).
4. Trust-gated policy without bounded confirmation sampling.
5. Full TAIS.
6. Optional threshold-proximity cue ablation, evaluated separately.

Tune only on development/validation runs. Use identical signal seeds and injection schedules for paired comparisons. Do not let one strategy see future readings when making its next-sample decision.

## Phase 2 — public greenhouse trace

Dataset: Hull et al. (2023), “Dataset of temperature, humidity, and actuator states of an east-facing South African Greenhouse Tunnel.” DOI: [10.17632/54htxm94bv.1](https://doi.org/10.17632/54htxm94bv.1). Data are available from [Mendeley Data](https://data.mendeley.com/datasets/54htxm94bv/1), CC BY 4.0. The full dataset contains 162 days, three spatial positions, temperature/humidity, and fan/wet-wall state at 5-minute resolution.

- Provide a downloader/instructions, not a silently altered copy. Store raw data outside version control by default and record the source/version/checksum.
- Preserve the original time ordering and missingness. Document any filtering/resampling.
- Evaluate only intervals that the 5-minute resolution can support (e.g. 5, 10, 15, 30, 60 minutes). Do not make sub-minute claims from this dataset.
- Use actual fan/wet-wall state transitions only as a **control-transition reference/proxy**. They are not labels for every environmental event.
- Inject faults into a copy of one channel using a separate seed while retaining the original trace as the latent environmental signal.
- Report reconstruction MAE/RMSE against the full trace, sampled-point count, upload count, and retention/detection around control-state transitions. Clearly distinguish these results from the synthetic ground-truth event experiment.

## Metrics

Primary:
- event recall = TP / number of independently labelled events;
- fault-induced false environmental detections and false alarms per hour;
- event detection latency;
- application uploads at a matched upload budget / the event-recall-versus-upload trade-off.

Secondary:
- missed-event count, precision, redundant detections;
- samples taken, application upload count, estimated payload bytes;
- reconstruction MAE/RMSE on the full trace;
- fault episode detection recall and wall-clock detection latency;
- health-state transitions and diagnostic sample overhead.

Use channel-aware one-to-one event matching and the existing metric definitions where appropriate. For scenarios with no true events, event recall is N/A, not 0%. Aggregate counts across scenarios (micro aggregation) rather than averaging percentages.

## Statistical reporting

- Report each scenario and the pooled result; do not hide poor short-event or same-channel fault cases in one average.
- Use paired comparisons on the same seed/trace and report 95% bootstrap confidence intervals over held-out runs.
- Plot the Pareto trade-off between event recall and application uploads; a single arbitrarily chosen operating point is insufficient.
- Tune only on development/validation runs and lock the method before final held-out testing.
- Do not claim statistical significance unless the analysis supports it.

## Energy and communication terminology

- Report sample count, application upload count, and estimated application payload bytes.
- A dimensionless upload proxy is not energy in joules/mJ.
- Application payload counts exclude Wi-Fi beacons, TCP acknowledgements, MQTT keepalive, retransmissions, and radio overhead.
- Physical energy claims require a current monitor and a separately documented hardware experiment.

## Minimum completion gate

Before writing a performance claim, the project must have:
- passing tests and baseline reproduction evidence;
- an independent event/fault ground-truth pipeline;
- all required baselines and ablations;
- held-out test results and confidence intervals;
- a results README stating what was simulated, what used real data, and what remains unmeasured;
- a limitations section covering SensorTrust's stable-offset blind spot and same-channel event/fault ambiguity.
