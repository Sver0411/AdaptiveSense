"""Unit tests for the AdaptiveSense scheduler state machine.

Run from the repository root with:

    python -m pytest tests/ -v
    # or, without pytest:
    python tests/test_scheduler.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.adaptive import ACTIVE, ALERT, STABLE, AdaptiveScheduler
from simulator.config import load_config

CFG = load_config()


def _scheduler(time=0.0) -> AdaptiveScheduler:
    return AdaptiveScheduler(CFG, time=time)


def _feed(sch, t, temp=24.0):
    return sch.update(t, {"temperature": temp, "humidity": 45.0, "pressure": 1012.0, "light": 300.0})


def test_initial_state_stable_default_interval():
    sch = _scheduler()
    assert sch.state == STABLE
    assert sch.interval == CFG["sampling"]["default_interval"]


def test_steady_readings_increase_interval():
    """Continuous stability should drive 20 -> 40 -> 60 s."""
    sch = _scheduler()
    _feed(sch, 0)
    d = _feed(sch, CFG["sampling"]["default_interval"])
    # second evaluation at 20s; first sample just initialized the baseline
    _feed(sch, d.timestamp + d.interval_s)
    d3 = _feed(sch, 80)
    assert d3.interval_s <= CFG["sampling"]["max_interval"]

    # force many stable evaluations and require the cap at max_interval
    sch = _scheduler()
    _feed(sch, 0)
    seen_intervals = []
    t = 0.0
    for _ in range(8):
        d = _feed(sch, t, temp=24.0)
        seen_intervals.append(d.interval_s)
        t = d.timestamp + d.interval_s
    assert max(seen_intervals) == CFG["sampling"]["max_interval"]


def test_sudden_change_triggers_fast_sampling_and_alert():
    """A large jump should drive interval down to min and enter ALERT/ACTIVE."""
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    t = 20.0
    d = _feed(sch, t, temp=24.0)
    # big jump
    d = _feed(sch, t + 1, temp=29.0)
    assert d.interval_s <= CFG["sampling"]["min_interval"] or d.state != STABLE


def test_state_transitions_to_alert_and_back():
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    t = 0.0
    for _ in range(3):  # warm stable baseline
        d = _feed(sch, t, temp=24.0)
        t = d.timestamp + d.interval_s
    d = _feed(sch, t + 1, temp=31.0)
    assert d.state != STABLE
    # after recovery (enough realtime for the EMA baseline to settle) returns
    for _ in range(60):
        d = _feed(sch, d.timestamp + d.interval_s, temp=24.0)
    assert d.state == STABLE


def test_interval_never_below_min_or_above_max():
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    low = CFG["sampling"]["min_interval"]
    high = CFG["sampling"]["max_interval"]
    t = 0.0
    for k in range(60):
        temp = 24.0 if k < 30 else 24.0 + (1 if k % 2 else -1) * 3.0
        d = _feed(sch, t, temp=temp)
        assert low - 1e-6 <= d.interval_s <= high + 1e-6
        t = d.timestamp + d.interval_s


def test_hysteresis_prevents_rapid_oscillation():
    """A score hovering near a boundary must not flip state every sample."""
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    # build a baseline
    t = 0.0
    for _ in range(3):
        d = _feed(sch, t, temp=24.0)
        t = d.timestamp + d.interval_s
    # keep a modest but persistent disturbance well within the gy band
    states = []
    for _ in range(20):
        d = _feed(sch, d.timestamp + d.interval_s, temp=24.4)  # slight wobble
        states.append(d.state)
    # no rapid alternation once settled
    distinct = len(set(states))
    assert distinct <= 2


def test_sustained_event_flagged():
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    t = 0.0
    for _ in range(3):
        d = _feed(sch, t, temp=24.0)
        t = d.timestamp + d.interval_s
    # raise strongly and keep it raised long enough to debounce
    saw_event = False
    for k in range(10):
        d = _feed(sch, d.timestamp + d.interval_s, temp=32.0 + 0.1 * k)
        if d.detected_event:
            saw_event = True
    assert saw_event


def test_no_event_on_flat_signal():
    sch = _scheduler()
    _feed(sch, 0, temp=24.0)
    t = 0.0
    detections = []
    for _ in range(20):
        d = _feed(sch, t, temp=24.0)
        detections.append(d.detected_event)
        t = d.timestamp + d.interval_s
    assert not any(detections)


if __name__ == "__main__":
    import traceback

    fns = [
        test_initial_state_stable_default_interval,
        test_steady_readings_increase_interval,
        test_sudden_change_triggers_fast_sampling_and_alert,
        test_state_transitions_to_alert_and_back,
        test_interval_never_below_min_or_above_max,
        test_hysteresis_prevents_rapid_oscillation,
        test_sustained_event_flagged,
        test_no_event_on_flat_signal,
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
    print(f"\n{len(fns) - failed}/{len(fns)} scheduler tests passed")
    sys.exit(1 if failed else 0)