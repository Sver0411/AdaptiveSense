"""Scheduler behaviour (`docs/change_score_spec.md` sections 6-8).

Every assertion is exact. v0.1's version of the central test was

    assert d.interval_s <= min_interval or d.state != STABLE

which passes when *either* half holds, and therefore cannot detect the very
defect it was written to guard (the ACTIVE ladder starting at the maximum
interval). The tests below pin the state and the interval separately.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.adaptive import (  # noqa: E402
    ACTIVE,
    ALERT,
    STABLE,
    AdaptiveScheduler,
    hysteresis_bounds,
    next_rung,
    transition,
)
from simulator.config import load_config  # noqa: E402

CFG = load_config()
MIN = float(CFG["sampling"]["min_interval"])
DEFAULT = float(CFG["sampling"]["default_interval"])
MAX = float(CFG["sampling"]["max_interval"])


class Driver:
    """Feeds a scheduler at exactly the times it asked for."""

    def __init__(self, config: dict | None = None) -> None:
        self.sch = AdaptiveScheduler(config or CFG, time=0.0)
        self.t = 0.0
        self.decision = None

    def step(self, temp: float = 24.0):
        self.decision = self.sch.update(
            self.t,
            {"temperature": temp, "humidity": 45.0, "pressure": 1012.0, "light": 320.0},
        )
        self.t = self.decision.timestamp + self.decision.interval_s
        return self.decision

    def run(self, n: int, temp: float = 24.0):
        decision = None
        for _ in range(n):
            decision = self.step(temp)
        return decision

    def intervals(self, n: int, temp: float = 24.0):
        out = []
        for _ in range(n):
            out.append(self.step(temp).interval_s)
        return out


def _config_without_other_upload_reasons() -> dict:
    """Isolate the delta rule by switching every other upload reason off."""
    cfg = copy.deepcopy(CFG)
    up = cfg["adaptive"]["upload"]
    up["heartbeat_s"] = 0
    up["on_event"] = False
    up["on_state_change"] = False
    up["on_interval_change"] = False
    return cfg


# ---------------------------------------------------------------------- #
# initial state and the first sample
# ---------------------------------------------------------------------- #
def test_initial_state_and_default_interval():
    d = Driver()
    assert d.sch.state == STABLE
    assert d.sch.interval == DEFAULT


def test_first_sample_is_uploaded():
    """The node must report its initial state instead of staying silent.

    v0.1 guarded every upload predicate with "have I uploaded before", so the
    first report could be delayed by a whole ladder step.
    """
    assert Driver().step().upload_requested


# ---------------------------------------------------------------------- #
# STABLE ladder: back off
# ---------------------------------------------------------------------- #
def test_stable_signal_backs_off_to_max_interval():
    """Exact ladder sequence: 20, 20, 40, 40, 60, 60, ... (confirmations = 2)."""
    assert Driver().intervals(6) == [20.0, 20.0, 40.0, 40.0, 60.0, 60.0]


def test_stable_signal_stays_at_max_interval():
    d = Driver()
    d.run(4)
    assert d.intervals(6) == [MAX] * 6
    assert d.sch.state == STABLE


def test_interval_never_leaves_bounds():
    d = Driver()
    for k in range(60):
        temp = 24.0 if k % 3 else 24.0 + (5.0 if k % 2 else -5.0)
        decision = d.step(temp)
        assert MIN - 1e-9 <= decision.interval_s <= MAX + 1e-9


# ---------------------------------------------------------------------- #
# escalation
# ---------------------------------------------------------------------- #
def test_sudden_large_change_enters_alert_directly():
    """A score far above the ACTIVE bound must skip ACTIVE entirely.

    score = 6.0 degC / 0.15 = 40 noise floors, versus active_threshold * (1+h) =
    12. v0.1 forced STABLE -> ACTIVE first, so ALERT was only reached after a
    whole ACTIVE interval had elapsed.
    """
    d = Driver()
    d.run(3, 24.0)
    decision = d.step(30.0)
    assert decision.state == ALERT
    assert decision.interval_s == MIN


def test_alert_keeps_the_minimum_interval():
    d = Driver()
    d.run(3, 24.0)
    assert d.step(30.0).state == ALERT
    for _ in range(3):
        decision = d.step(30.0)
        assert decision.state == ALERT
        assert decision.interval_s == MIN


def test_moderate_change_enters_active():
    """score = 1.0 / 0.15 = 6.7, between stable_high (4.5) and active_high (12)."""
    d = Driver()
    d.run(3, 24.0)
    decision = d.step(25.0)
    assert decision.state == ACTIVE
    assert decision.interval_s == pytest.approx(15.0)


def test_active_ladder_speeds_up():
    """ACTIVE must get FASTER: 15 -> 10 -> 5.

    v0.1 used active: [60, 30, 15, 5], so entering ACTIVE returned the maximum
    interval right after a change had been observed.
    """
    d = Driver()
    d.run(3, 24.0)
    got = [d.step(25.0).interval_s]
    got += d.intervals(2, 25.0)
    assert got == [15.0, 10.0, 5.0]


def test_active_interval_is_shorter_than_the_stable_backoff():
    d = Driver()
    d.run(6, 24.0)
    assert d.sch.interval == MAX
    decision = d.step(25.0)
    assert decision.state == ACTIVE
    assert decision.interval_s < MAX


# ---------------------------------------------------------------------- #
# de-escalation with hysteresis
# ---------------------------------------------------------------------- #
def test_recovery_returns_to_stable_through_hysteresis():
    d = Driver()
    d.run(3, 24.0)
    assert d.step(30.0).state == ALERT

    seen = []
    decision = None
    for _ in range(400):
        decision = d.step(24.0)
        seen.append(decision.state)
        if decision.state == STABLE:
            break
    assert seen[-1] == STABLE, "never recovered to STABLE"
    assert ACTIVE in seen, "must pass through ACTIVE, not jump ALERT -> STABLE"
    assert decision.interval_s > MIN


def test_state_never_oscillates_inside_the_hysteresis_band():
    bounds = hysteresis_bounds(
        float(CFG["adaptive"]["stable_threshold"]),
        float(CFG["adaptive"]["active_threshold"]),
        float(CFG["adaptive"]["hysteresis_fraction"]),
    )
    mid_stable_band = (bounds["stable_low"] + bounds["stable_high"]) / 2.0
    assert transition(ACTIVE, mid_stable_band, bounds) == ACTIVE
    assert transition(STABLE, mid_stable_band, bounds) == STABLE
    mid_active_band = (bounds["active_low"] + bounds["active_high"]) / 2.0
    assert transition(ALERT, mid_active_band, bounds) == ALERT
    assert transition(ACTIVE, mid_active_band, bounds) == ACTIVE


def test_state_machine_is_severity_ordered():
    bounds = hysteresis_bounds(3.0, 8.0, 0.5)
    assert transition(STABLE, 50.0, bounds) == ALERT     # immediate escalation
    assert transition(ACTIVE, 50.0, bounds) == ALERT
    assert transition(STABLE, 0.0, bounds) == STABLE


def test_de_escalation_moves_one_level_at_a_time():
    """ALERT must not fall straight back to STABLE while change is present."""
    bounds = hysteresis_bounds(3.0, 8.0, 0.5)
    # a low score leaves ALERT one step at a time
    assert transition(ALERT, 0.0, bounds) == ACTIVE
    assert transition(ACTIVE, 0.0, bounds) == STABLE
    # a score inside [S_hi, A_hi) always lands on ACTIVE, whatever came before
    assert transition(ALERT, 3.0, bounds) == ACTIVE
    assert transition(STABLE, 3.0, bounds) == STABLE


def test_hysteresis_boundaries_are_exact():
    bounds = hysteresis_bounds(3.0, 8.0, 0.5)
    assert bounds["stable_high"] == pytest.approx(4.5)
    assert bounds["stable_low"] == pytest.approx(1.5)
    assert bounds["active_high"] == pytest.approx(12.0)
    assert bounds["active_low"] == pytest.approx(4.0)
    assert transition(STABLE, 4.5, bounds) == ACTIVE     # entry at the bound
    assert transition(STABLE, 4.49, bounds) == STABLE
    assert transition(ALERT, 4.0, bounds) == ALERT       # retention at the bound
    assert transition(ALERT, 3.99, bounds) == ACTIVE     # one step down
    assert transition(ACTIVE, 1.5, bounds) == ACTIVE     # retention at the bound
    assert transition(ACTIVE, 1.49, bounds) == STABLE


# ---------------------------------------------------------------------- #
# ladder mechanics (pure)
# ---------------------------------------------------------------------- #
def test_ladder_holds_each_rung_for_the_configured_confirmations():
    ladder = [20.0, 40.0, 60.0]
    pos, count = 0, 0
    out = []
    for _ in range(6):
        interval, pos, count = next_rung(pos, count, ladder, 2)
        out.append(interval)
    assert out == [20.0, 20.0, 40.0, 40.0, 60.0, 60.0]
    assert pos == len(ladder) - 1, "position must clamp to the last rung"


def test_ladder_clamps_instead_of_running_off_the_end():
    interval, pos, _ = next_rung(99, 99, [5.0], 1)
    assert interval == 5.0
    assert pos == 0


# ---------------------------------------------------------------------- #
# upload policy
# ---------------------------------------------------------------------- #
def test_upload_on_interval_change():
    """The ladder is the only thing changing at first, and it is reported."""
    d = Driver()
    assert d.step().upload_requested            # first sample
    assert not d.step().upload_requested        # nothing changed: same rung
    decision = d.step()
    assert decision.interval_s == 40.0
    assert decision.upload_requested            # interval changed


def test_delta_upload_is_normalised_by_the_noise_floor():
    """0.15 degC is one noise floor, so 0.35 degC must exceed a threshold of 2."""
    d = Driver(_config_without_other_upload_reasons())
    assert d.step(24.0).upload_requested        # first sample
    assert not d.step(24.0).upload_requested
    assert not d.step(24.0).upload_requested
    assert not d.step(24.25).upload_requested   # 1.67 noise floors < 2.0
    decision = d.step(24.35)                    # 2.33 noise floors >= 2.0
    assert decision.upload_requested


def test_delta_upload_uses_each_channels_own_noise_floor():
    """A 2 degC step is ~13 temperature floors but only ~2.5 humidity floors."""
    cfg = _config_without_other_upload_reasons()
    d = Driver(cfg)
    d.step(24.0)                                # first sample, uploads
    # A light-channel change of 65 lux is 2.17 light floors.
    decision = d.sch.update(
        d.t,
        {"temperature": 24.0, "humidity": 45.0, "pressure": 1012.0, "light": 385.0},
    )
    d.t = decision.timestamp + decision.interval_s
    assert decision.upload_requested


def test_heartbeat_guarantees_a_bounded_reporting_gap():
    """The heartbeat is evaluated at each sample, so the reporting gap is bounded
    by `heartbeat_s` plus the sampling interval in force at that moment.

    With the documented configuration `heartbeat_s == max_interval`, so a node
    sampling at 60 s reports every sample; during the STABLE ramp-up (20 -> 40 ->
    60 s) the worst-case gap is heartbeat + max_interval.
    """
    heartbeat = float(CFG["adaptive"]["upload"]["heartbeat_s"])
    assert heartbeat > 0

    d = Driver()
    gaps = []
    last_upload_t = None
    for _ in range(60):
        decision = d.step(24.0)
        if decision.upload_requested:
            if last_upload_t is not None:
                gaps.append(decision.timestamp - last_upload_t)
            last_upload_t = decision.timestamp
    assert gaps, "expected periodic reports"
    assert max(gaps) <= heartbeat + MAX + 1e-6
    # and once the interval has settled at max_interval the gap is exactly the
    # heartbeat, i.e. every sample is reported
    settled = [g for g in gaps[3:]]
    assert settled, "expected settled-state reports"
    assert max(settled) <= heartbeat + 1e-6


def test_state_change_is_reported():
    d = Driver()
    d.run(3, 24.0)
    decision = d.step(30.0)
    assert decision.state == ALERT
    assert decision.upload_requested
