"""Change-score definitions (`docs/change_score_spec.md` sections 1-5).

These tests pin the three indicators to the *documented* definitions. v0.1 had
two different implementations that disagreed on the rate-of-change reducer and on
which window to use, so every clause below is checked explicitly.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.scoring import AnalyzerConfig, ChangeAnalyzer, ChannelConfig  # noqa: E402


def analyzer(*, variety: float = 30.0, roc: float = 10.0, tau: float = 60.0,
             threshold: float = 8.0, duration: float = 10.0,
             noise: float = 0.15) -> ChangeAnalyzer:
    cfg = AnalyzerConfig(
        channels=(ChannelConfig("temperature", noise, True),),
        variety_window_s=variety,
        roc_window_s=roc,
        baseline_tau_s=tau,
        event_threshold=threshold,
        event_min_duration_s=duration,
    )
    return ChangeAnalyzer(cfg)


def feed(an: ChangeAnalyzer, points):
    out = []
    for t, v in points:
        out.append(an.update(t, {"temperature": v}))
    return out


# ---------------------------------------------------------------------- #
# section 1 — EMA baseline
# ---------------------------------------------------------------------- #
def test_first_sample_establishes_the_baseline():
    an = analyzer()
    score, per_channel, event = an.update(0.0, {"temperature": 24.0})
    assert score == 0.0
    assert per_channel["temperature"] == 0.0
    assert not event
    assert an.baseline("temperature") == 24.0


def test_deviation_is_measured_against_the_pre_update_baseline():
    """A step must show its FULL size, not a partly-smoothed residual."""
    an = analyzer(tau=60.0)
    feed(an, [(0.0, 24.0), (20.0, 24.0)])
    score, per_channel, _ = an.update(40.0, {"temperature": 25.0})
    # dt = 20 s, tau = 60 s -> alpha = 0.25, so a post-update reference would
    # report 0.75 degC instead of 1.0 degC.
    assert per_channel["temperature"] == pytest.approx(1.0 / 0.15, rel=1e-9)


def test_ema_uses_the_time_based_alpha():
    an = analyzer(tau=60.0)
    feed(an, [(0.0, 0.0)])
    feed(an, [(60.0, 60.0)])
    # alpha = 60 / (60 + 60) = 0.5 -> the baseline has moved half way.
    assert an.baseline("temperature") == pytest.approx(30.0)


def test_long_interval_does_not_discard_the_baseline():
    """A change after a long quiet period is still visible (spec section 1)."""
    an = analyzer()
    feed(an, [(0.0, 24.0), (60.0, 24.0), (120.0, 24.0)])
    score, per_channel, _ = an.update(180.0, {"temperature": 26.0})
    assert per_channel["temperature"] == pytest.approx(2.0 / 0.15)


# ---------------------------------------------------------------------- #
# section 2 — standard deviation
# ---------------------------------------------------------------------- #
def test_std_is_population_std_over_the_variety_window():
    """`_std` implements documented section 2, so it is tested directly.

    (White-box on purpose: the STD indicator is never the maximum of the three
    for the trace shapes this project uses, so an end-to-end assertion would
    either be vacuous or would have to be tuned to force STD to dominate.)
    """
    an = analyzer(variety=5.0, roc=1.0, tau=1e12)
    feed(an, [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])
    values = (0.0, 1.0, 2.0, 3.0)
    mean = 1.5
    population = (sum((v - mean) ** 2 for v in values) / 4) ** 0.5
    sample = (sum((v - mean) ** 2 for v in values) / 3) ** 0.5
    assert an._std("temperature") == pytest.approx(population)
    # population (/N), not sample (/N-1) normalisation
    assert an._std("temperature") != pytest.approx(sample)


def test_std_window_is_measured_in_seconds():
    an = analyzer(variety=5.0, roc=1.0, tau=1e12)
    feed(an, [(0.0, 0.0), (1.0, 10.0)])   # a wide spread inside the window
    feed(an, [(20.0, 0.0)])               # ...which has now aged out
    # only the current sample remains, so there is nothing to take a std of
    assert an._std("temperature") == 0.0
    assert an.history_length("temperature") == 1


# ---------------------------------------------------------------------- #
# section 3 — rate of change
# ---------------------------------------------------------------------- #
def _oscillating_trace() -> list:
    """21 flat samples, then 20 alternating samples of +/-0.4 at 1 Hz."""
    points = [(float(t), 0.0) for t in range(0, 21)]
    for i, t in enumerate(range(21, 41)):
        points.append((float(t), 0.4 if i % 2 == 0 else -0.4))
    return points


def test_roc_uses_roc_window_not_the_variety_window():
    """The ROC indicator must average over `roc_window_s` alone.

    The trace is flat for 21 s and then oscillates at 0.8 units/s. With a 3 s ROC
    window every contributing pair is a fast one, so the indicator is 0.8. With a
    30 s window the flat prefix is averaged in as well, giving 20*0.8/30 = 0.533.

    v0.1's live scheduler used the *variety* window here while the firmware used a
    hardcoded 10 s, so this behaviour was undefined.
    """
    samples = _oscillating_trace()
    short = analyzer(variety=30.0, roc=3.0, tau=60.0, noise=1.0)
    wide = analyzer(variety=30.0, roc=30.0, tau=60.0, noise=1.0)
    feed(short, samples[:-1])
    feed(wide, samples[:-1])
    _, per_short, _ = short.update(samples[-1][0], {"temperature": samples[-1][1]})
    _, per_wide, _ = wide.update(samples[-1][0], {"temperature": samples[-1][1]})

    assert per_short["temperature"] == pytest.approx(0.8, rel=0.05)
    assert per_wide["temperature"] == pytest.approx(20 * 0.8 / 30.0, rel=0.05)
    assert per_short["temperature"] > per_wide["temperature"]


def test_roc_counts_each_interval_exactly_once():
    """Every adjacent pair contributes once; the newest pair is not doubled.

    Tested directly on the documented indicator (section 3): the deviation term
    would otherwise dominate and hide a duplicated ROC term.
    """
    an = analyzer(variety=30.0, roc=5.0, tau=1e12, noise=1.0)
    feed(an, [(0.0, 0.0)])
    feed(an, [(10.0, 10.0)])   # rate 1.0
    an.update(20.0, {"temperature": 30.0})  # rate 2.0
    # With a 5 s window only the (10 s -> 20 s) pair is in range.
    assert an._roc("temperature", 20.0) == pytest.approx(2.0, rel=1e-6)

    an = analyzer(variety=30.0, roc=30.0, tau=1e12, noise=1.0)
    feed(an, [(0.0, 0.0)])
    feed(an, [(10.0, 10.0)])   # rate 1.0
    an.update(20.0, {"temperature": 30.0})  # rate 2.0
    # Two distinct pairs, counted once each: (1.0 + 2.0) / 2.
    assert an._roc("temperature", 20.0) == pytest.approx(1.5, rel=1e-6)


def test_score_is_the_max_of_the_three_indicators_over_the_noise_floor():
    an = analyzer(variety=30.0, roc=10.0, tau=60.0, noise=0.5)
    feed(an, [(0.0, 10.0), (10.0, 10.0)])
    _, per_channel, _ = an.update(20.0, {"temperature": 12.0})
    assert per_channel["temperature"] == pytest.approx((12.0 - 10.0) / 0.5)


# ---------------------------------------------------------------------- #
# section 4 / 5 — overall score and event debounce
# ---------------------------------------------------------------------- #
def test_overall_score_is_the_max_over_enabled_channels():
    cfg = AnalyzerConfig(
        channels=(
            ChannelConfig("temperature", 0.15, True),
            ChannelConfig("humidity", 0.8, True),
            ChannelConfig("pressure", 0.3, False),
        ),
        variety_window_s=30.0,
        roc_window_s=10.0,
        baseline_tau_s=60.0,
        event_threshold=8.0,
        event_min_duration_s=10.0,
    )
    an = ChangeAnalyzer(cfg)
    an.update(0.0, {"temperature": 24.0, "humidity": 45.0, "pressure": 1012.0})
    score, per_channel, _ = an.update(
        20.0, {"temperature": 24.1, "humidity": 50.0, "pressure": 1030.0}
    )
    assert per_channel["pressure"] == 0.0  # use = False
    assert score == pytest.approx(max(per_channel["temperature"], per_channel["humidity"]))


def test_event_requires_the_score_to_persist():
    an = analyzer(threshold=8.0, duration=10.0)
    feed(an, [(0.0, 24.0)])
    _, _, event = an.update(20.0, {"temperature": 30.0})   # above threshold, t0
    assert not event                                        # not debounced yet
    _, _, event = an.update(25.0, {"temperature": 30.0})    # 5 s later
    assert not event
    _, _, event = an.update(30.0, {"temperature": 30.0})    # 10 s later
    assert event


def test_event_resets_when_the_score_falls_below_the_threshold():
    """The debounce must clear, not latch forever.

    A short variety window removes the STD term and one-sample ROC window keeps
    the ROC term small, so the score is driven by the deviation alone and the
    reset can be observed precisely.
    """
    an = analyzer(variety=1.0, roc=1.0, tau=60.0, noise=1.0, threshold=5.0,
                  duration=10.0)
    feed(an, [(0.0, 24.0)])                  # score 0
    an.update(10.0, {"temperature": 30.0})   # dev 6.0 -> debounce starts
    assert not an.event_active
    an.update(20.0, {"temperature": 30.0})   # 10 s later -> latched
    assert an.event_active
    an.update(30.0, {"temperature": 24.0})   # dev has decayed to ~1.6 -> reset
    assert not an.event_active
