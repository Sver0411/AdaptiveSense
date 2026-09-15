"""Offline experiment metrics for AdaptiveSense.

All metric definitions live here so tests can exercise them in isolation. The
definitions follow `docs/change_score_spec.md` sections 11-13:

* event matching is channel-aware and one-to-one,
* a scenario with no ground-truth events yields ``N/A`` (``None``) for the
  detection rate, never ``0``,
* cross-scenario numbers use **micro** aggregation (pooled counts), never the
  mean of per-scenario rates,
* detection latency is clamped at 0 and reported as mean / median / p95,
* the average sampling interval is the mean of the real sample time differences,
* no physical energy unit is reported: only a dimensionless communication proxy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, median
from typing import Dict, List, Optional, Sequence

from .events import Event, MatchResult, match_events

__all__ = ["RunMetrics", "compute_run_metrics", "aggregate_micro", "percentile"]

NA = ""  # CSV representation of "not applicable"


def _rate(total: float, ref: float) -> float:
    if ref <= 0:
        return 0.0
    return 1.0 - total / ref


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Linear-interpolation percentile. ``None`` for an empty input."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


@dataclass
class RunMetrics:
    """Metrics for one (scenario, strategy) run."""

    scenario: str = ""
    scenario_label: str = ""
    strategy: str = ""
    duration_s: float = 0.0

    n_ground_truth: int = 0
    number_of_samples: int = 0
    sampling_reduction: float = 0.0
    number_of_uploads: int = 0
    application_upload_reduction: float = 0.0
    estimated_payload_bytes: float = 0.0
    average_sampling_interval_s: Optional[float] = None

    n_gt_events: int = 0
    true_positives: int = 0
    matched_events: int = 0
    missed_events: int = 0
    false_positive_events: int = 0
    # Of `false_positive_events`, how many fall inside a same-channel
    # ground-truth event: those are repeat triggers for a physical event the
    # policy had already reported, not alarms with no counterpart at all.
    redundant_detections: int = 0
    # None means "not applicable" (a scenario with no ground-truth events).
    event_detection_rate: Optional[float] = None
    false_positive_per_hour: float = 0.0

    avg_detection_latency_s: Optional[float] = None
    median_detection_latency_s: Optional[float] = None
    p95_detection_latency_s: Optional[float] = None

    upload_energy_proxy: float = 0.0

    # kept out of the CSV: the pooled latency list is needed for micro aggregation
    latencies: List[float] = field(default_factory=list)

    def csv_row(self) -> Dict[str, object]:
        return {
            "scenario": self.scenario,
            "scenario_label": self.scenario_label,
            "strategy": self.strategy,
            "duration_s": round(self.duration_s, 3),
            "n_ground_truth": self.n_ground_truth,
            "number_of_samples": self.number_of_samples,
            "sampling_reduction": round(self.sampling_reduction, 6),
            "number_of_uploads": self.number_of_uploads,
            "application_upload_reduction": round(self.application_upload_reduction, 6),
            "estimated_payload_bytes": self.estimated_payload_bytes,
            "average_sampling_interval_s": _round_or_na(self.average_sampling_interval_s),
            "n_gt_events": self.n_gt_events,
            "true_positives": self.true_positives,
            "missed_events": self.missed_events,
            "false_positive_events": self.false_positive_events,
            "redundant_detections": self.redundant_detections,
            "false_alarm_events": self.false_positive_events - self.redundant_detections,
            "event_detection_rate": (
                NA if self.event_detection_rate is None
                else round(self.event_detection_rate, 6)
            ),
            "false_positive_per_hour": round(self.false_positive_per_hour, 6),
            "avg_detection_latency_s": _round_or_na(self.avg_detection_latency_s),
            "median_detection_latency_s": _round_or_na(self.median_detection_latency_s),
            "p95_detection_latency_s": _round_or_na(self.p95_detection_latency_s),
            "upload_energy_proxy": round(self.upload_energy_proxy, 6),
        }


def _round_or_na(value: Optional[float]) -> object:
    return NA if value is None else round(float(value), 6)


def compute_run_metrics(
    *,
    strategy_name: str,
    scenario: str,
    scenario_label: str,
    sample_timestamps: Sequence[float],
    n_ground_truth: int,
    uploads: int,
    gt_events: Sequence[Event],
    detected: Sequence[Event],
    duration_s: float,
    payload_bytes: float,
    energy_units_per_upload: float,
    match_tolerance_s: float,
) -> RunMetrics:
    """Compute the metric set for a single strategy run."""
    n_samples = len(sample_timestamps)

    # --- event matching (channel-aware, one-to-one) -----------------------
    match: MatchResult = match_events(gt_events, detected, match_tolerance_s)

    n_gt = len(gt_events)
    detection_rate: Optional[float]
    if n_gt == 0:
        detection_rate = None  # N/A: no ground-truth event in this scenario
    else:
        detection_rate = match.true_positives / n_gt

    latencies = match.latencies

    fp_per_hour = (
        match.false_positives / duration_s * 3600.0 if duration_s > 0 else 0.0
    )

    # --- average sampling interval: mean of the REAL sample differences ----
    avg_interval: Optional[float] = None
    if n_samples >= 2:
        diffs = [
            float(b) - float(a)
            for a, b in zip(sample_timestamps, list(sample_timestamps)[1:])
        ]
        diffs = [d for d in diffs if d > 0]
        if diffs:
            avg_interval = mean(diffs)

    return RunMetrics(
        scenario=scenario,
        scenario_label=scenario_label,
        strategy=strategy_name,
        duration_s=float(duration_s),
        n_ground_truth=int(n_ground_truth),
        number_of_samples=n_samples,
        sampling_reduction=_rate(n_samples, n_ground_truth),
        number_of_uploads=int(uploads),
        application_upload_reduction=_rate(uploads, n_ground_truth),
        estimated_payload_bytes=float(uploads) * float(payload_bytes),
        average_sampling_interval_s=avg_interval,
        n_gt_events=n_gt,
        true_positives=match.true_positives,
        matched_events=match.true_positives,
        missed_events=match.false_negatives,
        false_positive_events=match.false_positives,
        redundant_detections=len(match.redundant_detections),
        event_detection_rate=detection_rate,
        false_positive_per_hour=fp_per_hour,
        avg_detection_latency_s=mean(latencies) if latencies else None,
        median_detection_latency_s=median(latencies) if latencies else None,
        p95_detection_latency_s=percentile(latencies, 0.95),
        upload_energy_proxy=float(uploads) * float(energy_units_per_upload),
        latencies=list(latencies),
    )


def aggregate_micro(runs: Sequence[RunMetrics]) -> Dict[str, object]:
    """Micro-aggregate several scenario runs of the same strategy (spec §12).

    Counts are pooled; rates are recomputed from the pooled counts; latencies
    are recomputed over the pooled matched-pair list. Averaging per-scenario
    rates would let a scenario with zero ground-truth events contribute a
    spurious ``0 %`` — the v0.1 defect.
    """
    if not runs:
        raise ValueError("aggregate_micro requires at least one run")

    strategy = runs[0].strategy
    n_gt_samples = sum(r.n_ground_truth for r in runs)
    samples = sum(r.number_of_samples for r in runs)
    uploads = sum(r.number_of_uploads for r in runs)
    duration = sum(r.duration_s for r in runs)

    n_gt_events = sum(r.n_gt_events for r in runs)
    tp = sum(r.true_positives for r in runs)
    fn = sum(r.missed_events for r in runs)
    fp = sum(r.false_positive_events for r in runs)
    redundant = sum(r.redundant_detections for r in runs)

    latencies: List[float] = [x for r in runs for x in r.latencies]

    # pooled mean interval: weight each scenario's mean by its interval count
    total_intervals = 0
    weighted = 0.0
    for r in runs:
        if r.average_sampling_interval_s is not None and r.number_of_samples >= 2:
            cnt = r.number_of_samples - 1
            weighted += r.average_sampling_interval_s * cnt
            total_intervals += cnt
    avg_interval = (weighted / total_intervals) if total_intervals else None

    return {
        "strategy": strategy,
        "scenarios": len(runs),
        "duration_s": round(duration, 3),
        "n_ground_truth": n_gt_samples,
        "number_of_samples": samples,
        "sampling_reduction": round(_rate(samples, n_gt_samples), 6),
        "number_of_uploads": uploads,
        "application_upload_reduction": round(_rate(uploads, n_gt_samples), 6),
        "estimated_payload_bytes": sum(r.estimated_payload_bytes for r in runs),
        "average_sampling_interval_s": _round_or_na(avg_interval),
        "n_gt_events": n_gt_events,
        "true_positives": tp,
        "missed_events": fn,
        "false_positive_events": fp,
        "redundant_detections": redundant,
        "false_alarm_events": fp - redundant,
        "event_detection_rate": (
            NA if n_gt_events == 0 else round(tp / n_gt_events, 6)
        ),
        "false_positive_per_hour": (
            round(fp / duration * 3600.0, 6) if duration > 0 else NA
        ),
        "avg_detection_latency_s": _round_or_na(mean(latencies) if latencies else None),
        "median_detection_latency_s": _round_or_na(
            median(latencies) if latencies else None
        ),
        "p95_detection_latency_s": _round_or_na(percentile(latencies, 0.95)),
        "matched_latency_samples": len(latencies),
        "upload_energy_proxy": round(
            sum(r.upload_energy_proxy for r in runs), 6
        ),
    }
