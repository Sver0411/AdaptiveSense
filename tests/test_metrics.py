"""Metric definitions and aggregation (spec sections 11-13).

v0.1 averaged per-scenario detection rates with `pandas.mean()`, so a scenario
containing no ground-truth events contributed a literal `0 %`. It also reported
an invented constant in millijoules and computed the average sampling interval as
`duration / n_samples`. All three are pinned here.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.events import Event  # noqa: E402
from simulator.metrics import (  # noqa: E402
    RunMetrics,
    aggregate_micro,
    compute_run_metrics,
    percentile,
)

TOL = 5.0


def ev(channel: str, start: float, end: float, kind: str = "detected") -> Event:
    return Event(channel=channel, start_s=start, end_s=end, event_type=kind)


def run(
    *,
    strategy="S",
    scenario="x",
    timestamps=(0.0, 10.0, 20.0, 30.0),
    n_ground_truth=40,
    uploads=2,
    gt_events=(),
    detected=(),
    duration_s=30.0,
) -> RunMetrics:
    return compute_run_metrics(
        strategy_name=strategy,
        scenario=scenario,
        scenario_label=scenario,
        sample_timestamps=list(timestamps),
        n_ground_truth=n_ground_truth,
        uploads=uploads,
        gt_events=list(gt_events),
        detected=list(detected),
        duration_s=duration_s,
        payload_bytes=96.0,
        energy_units_per_upload=1.0,
        match_tolerance_s=TOL,
    )


# ---------------------------------------------------------------------- #
# reductions
# ---------------------------------------------------------------------- #
def test_reduction_fractions():
    m = run(n_ground_truth=3600, timestamps=tuple(float(i) for i in range(0, 3600, 10)),
            uploads=120, duration_s=3590.0)
    assert m.number_of_samples == 360
    assert m.sampling_reduction == pytest.approx(1 - 360 / 3600)
    assert m.application_upload_reduction == pytest.approx(1 - 120 / 3600)
    assert m.estimated_payload_bytes == 120 * 96


# ---------------------------------------------------------------------- #
# detection rate and the "no events" case
# ---------------------------------------------------------------------- #
def test_detection_rate_is_none_when_the_scenario_has_no_events():
    """N/A, never 0: this is the v0.1 bug that produced the README's 66.7 %."""
    m = run(gt_events=(), detected=())
    assert m.event_detection_rate is None
    assert m.csv_row()["event_detection_rate"] == ""
    assert m.n_gt_events == 0
    assert m.missed_events == 0
    assert m.false_positive_events == 0


def test_detection_rate_perfect():
    m = run(gt_events=[ev("temperature", 10, 20)], detected=[ev("temperature", 10, 20)])
    assert m.event_detection_rate == 1.0
    assert m.missed_events == 0
    assert m.false_positive_events == 0


def test_detection_rate_counts_misses():
    gt = [ev("temperature", 10, 20), ev("temperature", 100, 150)]
    m = run(gt_events=gt, detected=[ev("temperature", 10, 20)])
    assert m.event_detection_rate == pytest.approx(0.5)
    assert m.missed_events == 1


def test_false_positive_rate_is_per_hour():
    m = run(gt_events=[ev("temperature", 600, 760)],
            detected=[ev("temperature", 10, 50)],
            duration_s=1800.0)
    assert m.false_positive_events == 1
    assert m.event_detection_rate == 0.0
    assert m.false_positive_per_hour == pytest.approx(2.0)


# ---------------------------------------------------------------------- #
# latency
# ---------------------------------------------------------------------- #
def test_latency_is_reported_mean_median_and_p95():
    gt = [ev("temperature", 100, 200), ev("temperature", 400, 500), ev("temperature", 800, 900)]
    det = [ev("temperature", 103, 190), ev("temperature", 409, 490), ev("temperature", 802, 890)]
    m = run(gt_events=gt, detected=det)
    assert m.avg_detection_latency_s == pytest.approx((3 + 9 + 2) / 3)
    assert m.median_detection_latency_s == pytest.approx(3.0)
    assert 3.0 <= m.p95_detection_latency_s <= 9.0
    assert len(m.latencies) == 3


def test_latency_is_clamped_at_zero():
    m = run(gt_events=[ev("temperature", 100, 200)], detected=[ev("temperature", 97, 150)])
    assert m.avg_detection_latency_s == 0.0


def test_latency_is_none_when_nothing_was_detected():
    m = run(gt_events=[ev("temperature", 100, 200)], detected=[])
    assert m.avg_detection_latency_s is None
    assert m.median_detection_latency_s is None
    assert m.p95_detection_latency_s is None


def test_percentile_interpolates():
    assert percentile([1.0], 0.95) == 1.0
    assert percentile([0.0, 10.0], 0.5) == pytest.approx(5.0)
    assert percentile([], 0.95) is None
    assert percentile([0.0, 1.0, 2.0, 3.0], 0.5) == pytest.approx(1.5)


# ---------------------------------------------------------------------- #
# average sampling interval
# ---------------------------------------------------------------------- #
def test_average_interval_uses_real_sample_differences():
    """N samples define N-1 intervals; `duration / N` is not the same thing."""
    m = run(timestamps=(0.0, 10.0, 25.0, 45.0), duration_s=45.0)
    assert m.number_of_samples == 4
    # real diffs are 10, 15, 20 -> mean 15
    assert m.average_sampling_interval_s == pytest.approx(15.0)
    # the v0.1 formula would have said 45 / 4 = 11.25
    assert m.average_sampling_interval_s != pytest.approx(45 / 4)


def test_average_interval_is_none_for_a_single_sample():
    m = run(timestamps=(0.0,))
    assert m.average_sampling_interval_s is None


# ---------------------------------------------------------------------- #
# energy naming
# ---------------------------------------------------------------------- #
def test_no_physical_energy_unit_is_reported():
    m = run(uploads=25)
    row = m.csv_row()
    assert "upload_energy_proxy" in row
    assert row["upload_energy_proxy"] == 25.0
    assert not any("mj" in key.lower() for key in row)
    assert not any("joule" in key.lower() for key in row)


# ---------------------------------------------------------------------- #
# micro aggregation
# ---------------------------------------------------------------------- #
def test_micro_aggregation_pools_counts_instead_of_averaging_rates():
    """Scenario A (no events) must not contribute a 0 % detection rate."""
    scenario_a = run(scenario="a", gt_events=[], detected=[])
    scenario_b = run(scenario="b", gt_events=[ev("temperature", 100, 200)],
                     detected=[ev("temperature", 100, 200)])
    scenario_c = run(scenario="c", gt_events=[ev("temperature", 100, 200)],
                     detected=[ev("temperature", 100, 200)])

    overall = aggregate_micro([scenario_a, scenario_b, scenario_c])
    assert overall["n_gt_events"] == 2
    assert overall["true_positives"] == 2
    assert overall["event_detection_rate"] == 1.0
    # the naive mean over scenarios would have been (1 + 1) / 3 = 0.667
    assert overall["event_detection_rate"] != pytest.approx(2 / 3)


def test_micro_aggregation_sums_missed_and_false_positives():
    a = run(scenario="a", gt_events=[ev("temperature", 1, 2)], detected=[],
            duration_s=100.0)
    b = run(scenario="b", gt_events=[ev("temperature", 1, 2), ev("temperature", 50, 60)],
            detected=[ev("temperature", 1, 2), ev("temperature", 900, 950)],
            duration_s=100.0)
    overall = aggregate_micro([a, b])
    assert overall["n_gt_events"] == 3
    assert overall["true_positives"] == 1
    assert overall["missed_events"] == 2
    assert overall["false_positive_events"] == 1
    assert overall["event_detection_rate"] == pytest.approx(1 / 3)
    assert overall["false_positive_per_hour"] == pytest.approx(18.0)


def test_micro_aggregation_pools_latencies():
    a = run(scenario="a", gt_events=[ev("temperature", 100, 200)],
            detected=[ev("temperature", 110, 200)])
    b = run(scenario="b", gt_events=[ev("temperature", 100, 200)],
            detected=[ev("temperature", 130, 200)])
    overall = aggregate_micro([a, b])
    # pooled mean is (10 + 30)/2 = 20; the mean of the per-scenario means would
    # also be 20 here, so also check the pooled sample count and p95
    assert overall["avg_detection_latency_s"] == pytest.approx(20.0)
    assert overall["matched_latency_samples"] == 2
    assert 10.0 <= overall["p95_detection_latency_s"] <= 30.0


def test_micro_aggregation_reports_na_when_no_scenario_has_events():
    a = run(scenario="a", gt_events=[], detected=[])
    b = run(scenario="b", gt_events=[], detected=[ev("temperature", 5, 9)])
    overall = aggregate_micro([a, b])
    assert overall["event_detection_rate"] == ""
    assert overall["missed_events"] == 0
    assert overall["false_positive_events"] == 1


def test_redundant_detections_are_reported_separately_from_false_alarms():
    """`false_positive_events` stays strict, with the cause broken out.

    A policy can see one physical event as several rise/fall triggers. Those
    extra detections are unmatched (so they are false positives by the strict
    definition) but they are not alarms with no counterpart, and the difference
    is worth being able to read off the results table.
    """
    gt = [ev("temperature", 100, 300)]
    det = [
        ev("temperature", 105, 130),   # matched
        ev("temperature", 200, 260),   # repeat trigger inside the real event
        ev("temperature", 800, 850),   # no counterpart at all
    ]
    m = run(gt_events=gt, detected=det)
    assert m.true_positives == 1
    assert m.false_positive_events == 2
    assert m.redundant_detections == 1
    assert m.csv_row()["false_alarm_events"] == 1


def test_micro_aggregation_sums_redundant_detections():
    a = run(scenario="a", gt_events=[ev("temperature", 100, 300)],
            detected=[ev("temperature", 105, 130), ev("temperature", 200, 260)])
    b = run(scenario="b", gt_events=[ev("temperature", 100, 300)],
            detected=[ev("temperature", 101, 120)])
    overall = aggregate_micro([a, b])
    assert overall["false_positive_events"] == 1
    assert overall["redundant_detections"] == 1
    assert overall["false_alarm_events"] == 0


def test_micro_aggregation_requires_at_least_one_run():
    with pytest.raises(ValueError):
        aggregate_micro([])


def test_micro_reduction_uses_pooled_sample_counts():
    a = run(scenario="a", n_ground_truth=1000, timestamps=tuple(float(i) for i in range(0, 500, 10)),
            uploads=10)
    b = run(scenario="b", n_ground_truth=1000, timestamps=tuple(float(i) for i in range(0, 250, 10)),
            uploads=5)
    overall = aggregate_micro([a, b])
    assert overall["n_ground_truth"] == 2000
    assert overall["number_of_samples"] == 50 + 25
    assert overall["sampling_reduction"] == pytest.approx(1 - 75 / 2000)
    assert overall["number_of_uploads"] == 15
