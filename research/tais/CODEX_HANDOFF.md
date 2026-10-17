# Codex implementation handoff

Use this file as the task brief when opening the research/tais-design branch in Codex.

## Task

Implement the TAIS research prototype in the existing AdaptiveSense repository under research/tais/. Do not modify existing baseline code/results unless a change is separately proposed and justified. This branch exists so the experiment can reuse the exact baseline implementations and metric definitions.

## First inspect; do not start coding blindly

Read:
- docs/change_score_spec.md
- simulator/adaptive.py
- simulator/scoring.py
- simulator/replay.py
- simulator/events.py
- experiments/experiment_config.yaml
- research/tais/docs/ALGORITHM_SPEC.md
- research/tais/docs/EXPERIMENT_PROTOCOL.md
- [SensorTrust repository](https://github.com/Sver0411/SensorTrust), pinning commit 2ce98dd043bb01ea99052eda218c68a172499a4c if using it as a submodule.

## Implementation constraints

1. Preserve AdaptiveSense as an unchanged baseline. Do not edit its existing config or committed baseline metrics to improve TAIS.
2. Use SensorTrust's actual C11 core where practical. If adding it as a git submodule, pin the commit above; do not track a moving main silently. If the C compiler is required, document it and fail with a clear setup error.
3. If an adapter or wrapper is needed, keep it small and test its output against the upstream C replay output. Do not silently create a Python approximation and call it the original SensorTrust detector.
4. Add code only under research/tais/ for the first implementation, plus narrowly scoped test/config changes if necessary.
5. Keep per-channel trust and change state. A faulty channel must not suppress a healthy channel's event.
6. Do not use future samples, event labels, fault-injection labels, or ground-truth state to make online scheduling decisions.
7. Use configurable values, deterministic seeds, typed data structures, clear docstrings, and no hidden magic numbers.
8. Use the same event-matching and upload-count definitions as the baseline where appropriate.
9. Do not download/commit the greenhouse raw data automatically. Provide explicit instructions and ignore the local data file by default.
10. Do not claim TAIS is better until the complete protocol has run successfully on held-out seeds.

## Expected initial structure

research/tais/
  README.md
  CODEX_HANDOFF.md
  docs/
    RESEARCH_QUESTION.md
    RELATED_WORK.md
    ALGORITHM_SPEC.md
    EXPERIMENT_PROTOCOL.md
  configs/
    experiment.yaml
  src/
    TAIS scheduler, trust adapter, data loading, runner, metrics
  tests/
    unit, integration, causality/no-leakage, baseline parity tests
  scripts/
    run_experiments.py
    summarize_results.py
  results/
    README.md

Adjust only when repository inspection shows a better fit; document the reason.

## Required tests

- scheduler interval always remains inside configured min/max;
- faulting one channel does not erase an event on another healthy channel;
- a single spike in a degraded channel does not immediately become a confirmed environmental event;
- a persistent true event on a healthy channel is still detected;
- no future data are read by the scheduler;
- event labels are independent of detector outputs;
- deterministic run with same config/seed produces identical CSV metrics;
- baseline strategy results reproduce the current checked-in baseline;
- C SensorTrust wrapper/adapter matches the upstream implementation on shared fixtures;
- all original tests continue to pass.

## Required outputs before considering the implementation complete

- runnable commands in README;
- config and deterministic experiment runner;
- required baselines/ablations and synthetic fault/event scenarios;
- metric CSVs and plots with held-out results;
- a results README that labels synthetic simulation and greenhouse-trace results correctly;
- passing tests and a summary of commands actually run;
- a clear list of limitations and unimplemented items.

## Git workflow

- Work only on branch research/tais-design.
- Commit in small, meaningful commits.
- Do not merge into main automatically.
- Before pushing, run all original tests plus TAIS tests and the baseline reproduction check.
- Summarize changed files, tests run, results, and any failures. Never invent successful test results.
