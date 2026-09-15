"""Configuration validation.

The validator is a guard rail, not decoration: each rejection below corresponds
to a configuration that would make the experiment incoherent, and two of them are
literal v0.1 defects (the ACTIVE ladder starting at the maximum interval, and a
ROC window larger than the retained history).
"""

from __future__ import annotations

import copy
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.config import (  # noqa: E402
    CONFIG_PATH,
    ROOT,
    analyzer_config,
    load_config,
    noise_floors,
)


@pytest.fixture()
def cfg() -> dict:
    return copy.deepcopy(load_config())


def _expect_error(cfg: dict, needle: str) -> None:
    from simulator.config import _validate

    with pytest.raises(ValueError) as excinfo:
        _validate(cfg)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_shipped_config_is_valid():
    cfg = load_config()
    assert cfg["sampling"]["min_interval"] < cfg["sampling"]["default_interval"]
    assert cfg["sampling"]["default_interval"] < cfg["sampling"]["max_interval"]


# ---------------------------------------------------------------------- #
# sampling interval sanity
# ---------------------------------------------------------------------- #
def test_rejects_default_outside_bounds(cfg):
    cfg["sampling"]["default_interval"] = cfg["sampling"]["max_interval"] + 1
    _expect_error(cfg, "min <= default <= max")


def test_rejects_non_positive_min_interval(cfg):
    cfg["sampling"]["min_interval"] = 0
    _expect_error(cfg, "must be positive")


# ---------------------------------------------------------------------- #
# analyzer windows
# ---------------------------------------------------------------------- #
def test_rejects_roc_window_larger_than_variety_window(cfg):
    cfg["adaptive"]["analyzer"]["roc_window_s"] = 120
    cfg["adaptive"]["analyzer"]["variety_window_s"] = 30
    _expect_error(cfg, "roc_window_s")


def test_rejects_non_positive_windows(cfg):
    cfg["adaptive"]["analyzer"]["variety_window_s"] = 0
    _expect_error(cfg, "positive")


def test_rejects_non_positive_baseline_tau(cfg):
    cfg["adaptive"]["analyzer"]["baseline_tau_s"] = 0
    _expect_error(cfg, "baseline_tau_s")


# ---------------------------------------------------------------------- #
# thresholds
# ---------------------------------------------------------------------- #
def test_rejects_inverted_thresholds(cfg):
    cfg["adaptive"]["stable_threshold"] = 9.0
    cfg["adaptive"]["active_threshold"] = 8.0
    _expect_error(cfg, "0 < stable_threshold < active_threshold")


def test_rejects_out_of_range_hysteresis(cfg):
    cfg["adaptive"]["hysteresis_fraction"] = 1.0
    _expect_error(cfg, "hysteresis_fraction")


# ---------------------------------------------------------------------- #
# ladders
# ---------------------------------------------------------------------- #
def test_rejects_ladder_value_outside_interval_bounds(cfg):
    cfg["adaptive"]["ladders"]["alert"] = [1]
    _expect_error(cfg, "outside")


def test_rejects_empty_ladder(cfg):
    cfg["adaptive"]["ladders"]["alert"] = []
    _expect_error(cfg, "empty")


def test_rejects_a_stable_ladder_that_shortens(cfg):
    cfg["adaptive"]["ladders"]["stable"] = [60, 20]
    _expect_error(cfg, "non-decreasing")


def test_rejects_an_active_ladder_that_lengthens(cfg):
    cfg["adaptive"]["ladders"]["active"] = [5, 30]
    _expect_error(cfg, "non-increasing")


def test_rejects_the_v01_active_ladder():
    """`active: [60, 30, 15, 5]` slowed the node down right after a change."""
    cfg = load_config()
    cfg["adaptive"]["ladders"]["active"] = [60, 30, 15, 5]
    _expect_error(cfg, "first ACTIVE rung")


def test_rejects_alert_ladder_slower_than_active(cfg):
    cfg["adaptive"]["ladders"]["alert"] = [60]
    _expect_error(cfg, "ALERT")


def test_rejects_confirmation_below_one(cfg):
    cfg["adaptive"]["ladder_confirmations"]["stable"] = 0
    _expect_error(cfg, "ladder_confirmations")


# ---------------------------------------------------------------------- #
# channels and upload
# ---------------------------------------------------------------------- #
def test_rejects_all_channels_disabled(cfg):
    for ch in cfg["adaptive"]["channels"].values():
        ch["use"] = False
    _expect_error(cfg, "at least one channel")


def test_rejects_non_positive_noise_floor(cfg):
    cfg["adaptive"]["channels"]["temperature"]["noise_floor"] = 0
    _expect_error(cfg, "noise_floor")


def test_rejects_negative_upload_thresholds(cfg):
    cfg["adaptive"]["upload"]["delta_threshold"] = -1
    _expect_error(cfg, "delta_threshold")


# ---------------------------------------------------------------------- #
# evaluation block
# ---------------------------------------------------------------------- #
def test_rejects_negative_match_tolerance(cfg):
    cfg["evaluation"]["event_match_tolerance_s"] = -1
    _expect_error(cfg, "event_match_tolerance_s")


def test_rejects_non_positive_label_duration(cfg):
    cfg["evaluation"]["gt_label_min_duration_s"] = 0
    _expect_error(cfg, "gt_label_min_duration_s")


def test_rejects_non_positive_label_deviation(cfg):
    cfg["evaluation"]["gt_label_min_deviation"]["temperature"] = 0
    _expect_error(cfg, "gt_label_min_deviation.temperature")


# ---------------------------------------------------------------------- #
# derived objects
# ---------------------------------------------------------------------- #
def test_analyzer_config_matches_the_yaml():
    cfg = load_config()
    ac = analyzer_config(cfg)
    assert ac.variety_window_s == float(cfg["adaptive"]["analyzer"]["variety_window_s"])
    assert ac.roc_window_s == float(cfg["adaptive"]["analyzer"]["roc_window_s"])
    assert ac.baseline_tau_s == float(cfg["adaptive"]["analyzer"]["baseline_tau_s"])
    assert ac.event_threshold == float(cfg["adaptive"]["event_threshold"])
    assert ac.event_min_duration_s == float(cfg["adaptive"]["event_min_duration_s"])
    assert {c.name for c in ac.channels} == set(cfg["adaptive"]["channels"])
    for c in ac.channels:
        assert c.noise_floor == pytest.approx(
            float(cfg["adaptive"]["channels"][c.name]["noise_floor"])
        )
        assert c.use == bool(cfg["adaptive"]["channels"][c.name]["use"])


def test_noise_floors_helper():
    cfg = load_config()
    nf = noise_floors(cfg)
    assert nf["temperature"] == pytest.approx(0.15)
    assert nf["humidity"] == pytest.approx(0.8)


def test_config_file_is_yaml_and_lives_where_documented():
    assert CONFIG_PATH.exists()
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        assert isinstance(yaml.safe_load(fh), dict)
    assert CONFIG_PATH == ROOT / "experiments" / "experiment_config.yaml"
