"""Event matching and independent ground-truth labels (spec sections 10-11).

v0.1's matching ignored `channel` entirely and was many-to-many: one detection
could satisfy several ground-truth events, a ground-truth event could absorb
several detections, and latency could come out negative. Every clause of the
replacement rule is pinned below.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.events import (  # noqa: E402
    Event,
    detect_events,
    label_path_for,
    load_ground_truth_events,
    load_labels,
    match_events,
    write_labels,
)
from simulator.config import ROOT, analyzer_config, load_config  # noqa: E402

TOL = 5.0


def ev(channel: str, start: float, end: float, kind: str = "detected") -> Event:
    return Event(channel=channel, start_s=start, end_s=end, event_type=kind)


# ---------------------------------------------------------------------- #
# channel awareness
# ---------------------------------------------------------------------- #
def test_same_channel_event_matches():
    result = match_events([ev("temperature", 100, 200)], [ev("temperature", 104, 190)], TOL)
    assert result.true_positives == 1
    assert result.false_positives == 0
    assert result.false_negatives == 0


def test_different_channel_overlap_does_not_match():
    """A detection on another channel is a false positive, not a detection."""
    result = match_events([ev("temperature", 100, 200)], [ev("humidity", 100, 200)], TOL)
    assert result.true_positives == 0
    assert result.false_negatives == 1
    assert result.false_positives == 1
    assert result.redundant_detections == ()


# ---------------------------------------------------------------------- #
# one-to-one
# ---------------------------------------------------------------------- #
def test_one_detection_cannot_match_two_ground_truth_events():
    gt = [ev("temperature", 100, 110), ev("temperature", 112, 120)]
    det = [ev("temperature", 105, 118)]  # overlaps both
    result = match_events(gt, det, TOL)
    assert result.true_positives == 1
    assert result.false_negatives == 1
    assert result.false_positives == 0


def test_one_ground_truth_event_absorbs_only_one_detection():
    gt = [ev("temperature", 100, 300)]
    det = [ev("temperature", 105, 130), ev("temperature", 200, 260)]
    result = match_events(gt, det, TOL)
    assert result.true_positives == 1
    assert result.false_negatives == 0
    assert result.false_positives == 1
    # the leftover detection sits inside a real event, so it is a *repeat
    # trigger* for that event rather than an alarm with no counterpart
    assert len(result.redundant_detections) == 1


def test_best_match_is_the_closest_onset():
    gt = [ev("temperature", 100, 400)]
    close = ev("temperature", 102, 150)
    far = ev("temperature", 380, 420)
    result = match_events(gt, [far, close], TOL)
    assert result.matches[0].detected is close


def test_two_ground_truth_events_two_detections_match_one_to_one():
    gt = [ev("temperature", 100, 150), ev("temperature", 300, 350)]
    det = [ev("temperature", 101, 149), ev("temperature", 305, 348)]
    result = match_events(gt, det, TOL)
    assert result.true_positives == 2
    assert result.false_positives == 0
    assert result.false_negatives == 0


def test_identical_events_do_not_double_count():
    """Two identical detections must not both be credited to one GT event."""
    gt = [ev("temperature", 100, 150)]
    det = [ev("temperature", 100, 150), ev("temperature", 100, 150)]
    result = match_events(gt, det, TOL)
    assert result.true_positives == 1
    assert result.false_positives == 1


# ---------------------------------------------------------------------- #
# unmatched events
# ---------------------------------------------------------------------- #
def test_unmatched_ground_truth_is_a_false_negative():
    result = match_events([ev("temperature", 100, 150), ev("temperature", 900, 950)],
                          [ev("temperature", 101, 149)], TOL)
    assert result.false_negatives == 1


def test_unmatched_detection_is_a_false_positive():
    result = match_events([ev("temperature", 100, 150)],
                          [ev("temperature", 101, 149), ev("temperature", 800, 860)],
                          TOL)
    assert result.false_positives == 1
    assert result.redundant_detections == ()


# ---------------------------------------------------------------------- #
# tolerance
# ---------------------------------------------------------------------- #
def test_detection_within_tolerance_before_the_onset_matches():
    gt = [ev("temperature", 100, 200)]
    det = [ev("temperature", 96, 130)]      # 4 s early, tol = 5 s
    result = match_events(gt, det, TOL)
    assert result.true_positives == 1
    assert result.matches[0].latency_s == 0.0   # clamped, never negative


def test_detection_just_outside_tolerance_does_not_match():
    gt = [ev("temperature", 100, 200)]
    det = [ev("temperature", 94, 130)]      # 6 s early, tol = 5 s
    result = match_events(gt, det, TOL)
    assert result.true_positives == 0
    assert result.false_positives == 1


def test_tolerance_applies_after_the_event_end():
    gt = [ev("temperature", 100, 200)]
    assert match_events(gt, [ev("temperature", 205, 260)], TOL).true_positives == 1
    assert match_events(gt, [ev("temperature", 206, 260)], TOL).true_positives == 0


def test_latency_is_the_onset_difference():
    gt = [ev("temperature", 100, 300)]
    det = [ev("temperature", 118, 260)]
    result = match_events(gt, det, TOL)
    assert result.matches[0].latency_s == pytest.approx(18.0)


# ---------------------------------------------------------------------- #
# ground-truth labels are independent of AdaptiveSense
# ---------------------------------------------------------------------- #
def test_labels_round_trip(tmp_path):
    events = [
        ev("temperature", 634, 1413, "sudden_change"),
        ev("humidity", 635, 1396, "sudden_change"),
    ]
    path = tmp_path / "labels.csv"
    write_labels(path, events)
    loaded = load_labels(path)
    assert loaded == sorted(events, key=lambda e: (e.start_s, e.channel))


def test_header_only_label_file_means_no_events(tmp_path):
    path = tmp_path / "empty.csv"
    write_labels(path, [])
    assert load_labels(path) == []


def test_label_path_convention():
    assert label_path_for("/x/dataset/raw/scenario_b_sudden.csv", "/x/dataset/labels") \
        == __import__("pathlib").Path("/x/dataset/labels/scenario_b_sudden_events.csv")


def test_missing_label_file_is_an_error_not_a_silent_synthesis():
    with pytest.raises(FileNotFoundError):
        load_ground_truth_events("/tmp/does-not-exist-raw.csv", "/tmp/does-not-exist-labels")


def test_shipped_scenarios_have_labels():
    cfg = load_config()
    raw_dir = ROOT / cfg["dataset"]["raw_dir"]
    label_dir = ROOT / cfg["dataset"]["label_dir"]
    total = 0
    for raw in sorted(raw_dir.glob("scenario_*.csv")):
        events = load_ground_truth_events(raw, label_dir)
        for e in events:
            assert e.channel in cfg["adaptive"]["channels"]
            assert e.end_s > e.start_s, f"{raw.name}: {e}"
            assert e.event_type in ("sudden_change", "sustained_change"), e
        total += len(events)
    # The v0.1 benchmark rested on four events. The replacement must be
    # materially larger or the headline numbers stay anecdotal.
    assert total >= 20, f"only {total} ground-truth events in the benchmark"


def test_scenarios_without_injected_disturbance_have_empty_labels():
    cfg = load_config()
    raw_dir = ROOT / cfg["dataset"]["raw_dir"]
    label_dir = ROOT / cfg["dataset"]["label_dir"]
    for name in ("scenario_a_stable", "scenario_f_noisy_stable"):
        assert load_ground_truth_events(raw_dir / f"{name}.csv", label_dir) == []


# ---------------------------------------------------------------------- #
# the benchmark is described in two units, and they are not interchangeable
# ---------------------------------------------------------------------- #
def _load_generator():
    """Import dataset/generate_dataset.py by path (it is a script, not a module).

    The module has to be registered in `sys.modules` before it is executed:
    it uses `from __future__ import annotations`, so `@dataclass` resolves the
    annotations as strings by looking the defining module up in `sys.modules`.
    """
    import importlib.util

    path = ROOT / "dataset" / "generate_dataset.py"
    spec = importlib.util.spec_from_file_location("_as_generate_dataset", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def test_channel_labels_come_from_fewer_physical_disturbances():
    """One injected disturbance can label several channels.

    Humidity is coupled to temperature by the generator, so most physical
    disturbances produce two labels. Reporting "24 events" without saying so
    invites reading it as 24 independent phenomena, so the relationship is
    asserted here rather than described in prose.
    """
    generator = _load_generator()
    cfg = load_config()

    total_labels = 0
    total_disturbances = 0
    for factory in generator.SCENARIOS:
        spec = factory()
        _, driven = generator.build(spec)
        events = generator.label_events(driven, cfg)
        total_labels += len(events)
        total_disturbances += generator.count_disturbances(driven, cfg)

        # Every physical disturbance must produce at least one label, and a
        # disturbance can never produce fewer labels than 1.
        assert generator.count_disturbances(driven, cfg) <= len(events)

    assert total_labels == 24, (
        f"{total_labels} channel-level labels; update the README if this changed"
    )
    assert total_disturbances == 13, (
        f"{total_disturbances} injected physical disturbances; the README quotes "
        f"this number, so update both together"
    )


def test_a_disturbance_that_moves_two_channels_counts_once():
    """Direct check of the counting rule on a synthetic driven signal."""
    generator = _load_generator()
    cfg = load_config()
    driven = {
        "temperature": [0.0] * 100,
        "humidity": [0.0] * 100,
        "pressure": [0.0] * 100,
        "light": [0.0] * 100,
    }
    # One disturbance that moves temperature and humidity together...
    for i in range(20, 60):
        driven["temperature"][i] = 5.0
        driven["humidity"][i] = -20.0
    # ...and one that moves only light.
    for i in range(70, 95):
        driven["light"][i] = 400.0

    assert generator.count_disturbances(driven, cfg) == 2
    assert len(generator.label_events(driven, cfg)) == 3


# ---------------------------------------------------------------------- #
# detection over an observed sample stream
# ---------------------------------------------------------------------- #
def test_detector_finds_a_sustained_step():
    cfg = load_config()
    samples = []
    for t in range(0, 400):
        temperature = 24.0 if t < 200 else 30.0
        samples.append({"timestamp": float(t), "temperature": temperature,
                        "humidity": 45.0, "pressure": 1012.0, "light": 320.0})
    detected = detect_events(samples, analyzer_config(cfg))
    temperature_events = [e for e in detected if e.channel == "temperature"]
    assert temperature_events, "the detector missed a 200 s / 6 degC step"
    assert temperature_events[0].start_s >= 200
    assert temperature_events[0].start_s <= 215


def test_detector_reports_nothing_on_a_flat_signal():
    cfg = load_config()
    samples = [
        {"timestamp": float(t), "temperature": 24.0, "humidity": 45.0,
         "pressure": 1012.0, "light": 320.0}
        for t in range(600)
    ]
    assert detect_events(samples, analyzer_config(cfg)) == []


def test_detector_is_deterministic():
    cfg = load_config()
    samples = [
        {"timestamp": float(t), "temperature": 24.0 + (t % 7) * 0.05,
         "humidity": 45.0, "pressure": 1012.0, "light": 320.0}
        for t in range(300)
    ]
    first = detect_events(samples, analyzer_config(cfg))
    second = detect_events(samples, analyzer_config(cfg))
    assert first == second
