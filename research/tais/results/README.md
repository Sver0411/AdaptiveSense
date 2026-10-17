# TAIS results

**No TAIS experiment has been run or validated yet.** This directory is reserved for generated reports.

## Reporting rules

- Never present the current AdaptiveSense results as TAIS results.
- Preserve existing baseline CSVs and figures.
- Separate development, validation, and held-out test results.
- Report per-scenario outcomes as well as pooled counts.
- Use paired comparisons on the same latent trace and fault-injection schedule.
- Include event recall, missed events, fault-induced false reports, false alarms/hour, latency, samples, application uploads, estimated payload bytes, and reconstruction error where applicable.
- Use confidence intervals over held-out runs; do not claim significance without analysis.
- A no-event scenario has event recall N/A, not zero.
- Application upload reduction is not radio traffic reduction.
- No physical energy measurement exists unless a documented current-monitor experiment is added.
- Clearly distinguish synthetic fault injection from results on the public greenhouse trace.
- The greenhouse actuator state is a control-transition proxy, not complete environmental event ground truth.

Generated CSVs, figures, exact commands, environment versions, source commit, config hash, and test results should be recorded together for each final evaluation.
