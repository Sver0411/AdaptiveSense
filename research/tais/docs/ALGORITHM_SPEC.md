# Candidate algorithm specification — TAIS v0.1

This is a pre-implementation contract. It is a hypothesis to test, not a claim that the method improves performance.

## Design principle

Reuse AdaptiveSense's normalized per-channel change score as the observation-importance signal. Use SensorTrust's channel health state to decide whether that channel may influence environmental-event priority. Do not merge the health score into the change score or interpret health score as a probability.

## Inputs per sampled cycle

For each channel:
- timestamp and numeric value, or invalid/missing status;
- per-channel AdaptiveSense score;
- SensorTrust fault flags, heuristic health score, and state (HEALTHY, DEGRADED, FAULT);
- optional configured control thresholds for the exploratory greenhouse experiment.

All thresholds and intervals must live in a configuration file. No experiment-specific constants may be hidden in algorithm code.

## Change / importance level

Reuse AdaptiveSense's published score definition: the maximum of EMA deviation, rolling standard deviation, and mean absolute rate of change, normalized by the channel's noise floor.

Use the existing AdaptiveSense stable/active/alert entry thresholds and hysteresis to map scores into scheduler levels. A separate optional threshold-proximity cue may elevate priority when a valid, healthy observation approaches a configured control threshold. That cue must be independently switchable for ablation and is not part of the primary claim until results justify it.

## Trust-gated decision

1. **HEALTHY channel**
   - Feed the reading into its channel-local change analyzer.
   - Allow its change level (and optional threshold cue) to contribute to the node's event priority.
   - A healthy channel's event must not be suppressed merely because another channel is degraded or faulty.

2. **DEGRADED channel**
   - Do not allow a single suspicious reading to trigger the highest-priority environmental event immediately.
   - If its candidate importance reaches the alert level, enter bounded confirmation sampling at the configured minimum interval.
   - Require configurable consecutive evidence/persistence before confirming an environmental event from this channel.
   - If evidence disappears or trust becomes FAULT, cancel environmental confirmation and report the sensor-health change instead.
   - Bound the confirmation window and fall back to diagnostic sampling; never remain at the minimum interval forever solely because a channel is degraded.

3. **FAULT channel**
   - Exclude its raw value from environmental-event priority.
   - Report fault onset/recovery and use a bounded diagnostic interval.
   - Do not let this channel suppress or invalidate the contribution of other healthy channels.

4. **Node-level interval**
   - Because channels share a physical sampling cycle, choose the fastest interval required by any healthy high-priority channel or due confirmation/diagnostic action.
   - Otherwise use the existing AdaptiveSense interval ladders and hysteresis.
   - Clamp every interval to the configured minimum/maximum.
   - Keep a heartbeat so a quiet or faulty node remains observable.

5. **Uploads**
   - Request an application upload for the first sample, confirmed environmental event onset, health/fault-state change, scheduler-state/interval change if configured, significant change on a trusted channel, or heartbeat.
   - Keep upload_requested separate from transport success.
   - Do not describe upload-count reduction as measured radio traffic or energy reduction.

## State and history rules

- Maintain health and change-analysis history per channel; avoid a single global health flag.
- On transition from FAULT to a usable state, reinitialize or explicitly warm up that channel's change baseline so stale pre-fault history cannot create an artificial event. Document and test the chosen policy.
- Invalid/missing samples are not numerical observations.
- If no healthy channel remains, the scheduler still performs bounded diagnostics and reports its degraded state.
- The same raw trace and event/fault injection seed must be supplied to all strategies.
- Ground-truth event labels must be created from the latent, pre-fault signal or an independent control-transition reference, never from TAIS's own score.

## Required baselines and ablations

- Fixed-rate policies at the existing configured intervals.
- Existing AdaptiveSense unchanged.
- AdaptiveSense with SensorTrust run in diagnostic-only mode (health is logged but does not affect decisions).
- Trust-gated policy without confirmation sampling.
- Full TAIS.
- Optional threshold-proximity cue off/on, only in the exploratory threshold experiment.

## Invariants to test

- A faulty channel cannot alone create a trusted environmental event.
- A healthy channel can still escalate the node interval and trigger an event while another channel is faulty.
- A confirmed event is never reported before its configured confirmation evidence exists.
- Fault diagnostics are bounded and eventually report state changes.
- Scheduler intervals remain inside configured limits.
- No future values or ground-truth labels are used in online decisions.
- Existing AdaptiveSense baseline results remain unchanged.
