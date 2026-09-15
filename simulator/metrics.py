"""Offline experiment metrics for AdaptiveSense.

All metric definitions live here so tests can exercise them in isolation.
Energy figures are explicitly **proxy / estimated** quantities and are never
presented as real measurements.
"""

from __future__ import annotations

from statistics import mean, median
from typing import Dict, List

from .events import Event, event_overlap


def _rate(total: float, ref: float) -> float:
    if ref <= 0:
        return 0.0
    return 1.0 - total / ref


def compute_metrics(
    *,
    strategy_name: str,
    n_ground_truth: int,
    samples_taken: int,
    uploads: int,
    n_gt_events: int,
    gt_events: List[Event],
    detected: List[Event],
    duration_s: float,
    payload_bytes: float,
    energy_per_upload_mj: float,
) -> Dict[str, float]:
    """Compute the full metric set for a single strategy run.

    All values are scalars; categorical labels (state histogram, etc.) are kept
    out so that this function stays trivially unit-testable.
    """
    samples_taken = max(0, samples_taken)
    uploads = max(0, uploads)

    # --- event detection --------------------------------------------------
    # A ground-truth event is DETECTED if it overlaps at least one detected
    # event. FPs are detected events that overlap no ground-truth event.
    detected_by_gt: List[Event] = []
    for g in gt_events:
        overlap = [d for d in detected if event_overlap(d, [g])]
        if overlap:
            detected_by_gt.append(g)

    n_true_pos = len(detected_by_gt)          # gt events that were detected
    n_false_neg = len(gt_events) - n_true_pos  # gt events missed
    n_false_pos = sum(1 for d in detected if not event_overlap(d, gt_events))

    detection_rate = n_true_pos / n_gt_events if n_gt_events > 0 else 0.0

    # false-positive rate expressed per hour of observation time
    fp_per_hour = (
        (n_false_pos / duration_s * 3600.0) if duration_s > 0 else 0.0
    )

    # --- latency ----------------------------------------------------------
    # For each correctly detected gt event, time from its start to the first
    # overlapping detection start.
    latencies: List[float] = []
    for g in gt_events:
        matches = [d for d in detected if event_overlap(d, [g])]
        if not matches:
            continue
        first = min(m.start_s for m in matches)
        latencies.append(max(0.0, first - g.start_s))

    avg_latency = mean(latencies) if latencies else 0.0
    median_latency = median(latencies) if latencies else 0.0

    # --- summary ----------------------------------------------------------
    avg_interval = (duration_s / samples_taken) if samples_taken > 0 else 0.0

    return {
        "strategy": strategy_name,
        "n_ground_truth": float(n_ground_truth),
        "number_of_samples": float(samples_taken),
        "sampling_reduction": _rate(samples_taken, n_ground_truth),
        "number_of_uploads": float(uploads),
        "communication_reduction": _rate(uploads, n_ground_truth),
        "data_volume_bytes": float(uploads * payload_bytes),
        "average_sampling_interval_s": avg_interval,
        "n_gt_events": float(n_gt_events),
        "event_detection_rate": detection_rate,
        "missed_events": float(n_false_neg),
        "false_positive_events": float(n_false_pos),
        "false_positive_per_hour": fp_per_hour,
        "avg_detection_latency_s": avg_latency,
        "median_detection_latency_s": median_latency,
        # ESTIMATED energy proxy – see docs/methodology.md
        "energy_proxy_mj": float(uploads * energy_per_upload_mj),
    }