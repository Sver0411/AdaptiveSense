"""Unit tests for the metrics module.

Run from the repository root with:

    python -m pytest tests/ -v
    # or, without pytest:
    python tests/test_metrics.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.metrics import compute_metrics
from simulator.events import Event


def _events(starts_ends):
    return [Event(channel="any", start_s=s, end_s=e) for s, e in starts_ends]


def test_reduction_fractions():
    m = compute_metrics(
        strategy_name="AdaptiveSense",
        n_ground_truth=3600,
        samples_taken=600,
        uploads=120,
        n_gt_events=1,
        gt_events=_events([(100, 200)]),
        detected=_events([(102, 200)]),
        duration_s=3600,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert m["number_of_samples"] == 600
    assert abs(m["sampling_reduction"] - (1 - 600 / 3600)) < 1e-9
    assert abs(m["communication_reduction"] - (1 - 120 / 3600)) < 1e-9
    assert m["data_volume_bytes"] == 120 * 96


def test_event_detection_rate_perfect():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=1000,
        samples_taken=1000,
        uploads=1000,
        n_gt_events=3,
        gt_events=_events([(10, 50), (100, 150), (200, 240)]),
        detected=_events([(10, 50), (100, 150), (200, 240)]),
        duration_s=1000,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert m["event_detection_rate"] == 1.0
    assert m["missed_events"] == 0
    assert m["false_positive_events"] == 0


def test_event_detection_rate_misses():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=1000,
        samples_taken=1000,
        uploads=1000,
        n_gt_events=2,
        gt_events=_events([(10, 50), (100, 150)]),
        detected=_events([(10, 50)]),  # second event missed
        duration_s=1000,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert abs(m["event_detection_rate"] - 0.5) < 1e-9
    assert m["missed_events"] == 1


def test_false_positive_counted():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=1000,
        samples_taken=1000,
        uploads=1000,
        n_gt_events=1,
        gt_events=_events([(600, 760)]),  # event far away in time
        detected=_events([(10, 50)]),     # detection far away -> false positive
        duration_s=1000,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert m["false_positive_events"] == 1
    assert m["event_detection_rate"] == 0.0


def test_detection_latency():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=1000,
        samples_taken=1000,
        uploads=1000,
        n_gt_events=1,
        gt_events=_events([(100, 200)]),
        detected=_events([(106, 200)]),  # 6 s late
        duration_s=1000,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert m["avg_detection_latency_s"] == 6.0
    assert m["median_detection_latency_s"] == 6.0


def test_energy_proxy_is_explicitly_an_estimate():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=100,
        samples_taken=100,
        uploads=25,
        n_gt_events=0,
        gt_events=[],
        detected=[],
        duration_s=100,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert m["energy_proxy_mj"] == 25.0


def test_average_sampling_interval():
    m = compute_metrics(
        strategy_name="S",
        n_ground_truth=3600,
        samples_taken=360,
        uploads=360,
        n_gt_events=0,
        gt_events=[],
        detected=[],
        duration_s=3600,
        payload_bytes=96,
        energy_per_upload_mj=1.0,
    )
    assert abs(m["average_sampling_interval_s"] - 10.0) < 1e-9


if __name__ == "__main__":
    import traceback

    fns = [
        test_reduction_fractions,
        test_event_detection_rate_perfect,
        test_event_detection_rate_misses,
        test_false_positive_counted,
        test_detection_latency,
        test_energy_proxy_is_explicitly_an_estimate,
        test_average_sampling_interval,
    ]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} metrics tests passed")
    sys.exit(1 if failed else 0)