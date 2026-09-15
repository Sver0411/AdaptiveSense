#!/usr/bin/env python3
"""Regenerate `tests/fixtures/parity_trace.csv`.

The trace is a deterministic 1 Hz sensor sequence with no randomness at all. It
is fed to both the Python policy (`simulator/adaptive.py`) and the C policy
(`firmware/main/change_detector.c` + `adaptive_scheduler.c`, compiled for the
host) and the two outputs must agree. `tests/test_parity_python_c.py` does that
comparison.

Design notes — why the levels are what they are:

* every step is placed well away from a threshold boundary, so a difference
  between `float` (device) and `double` (host/Python) arithmetic cannot flip a
  state transition. The parity test is about the *algorithm*, not about rounding.
* the trace deliberately exercises all three states and all three channels:

  | segment | channel | intent |
  |---------|---------|--------|
  | 0-100   | -       | initial upload + STABLE backoff 20 -> 40 -> 60 |
  | 100-250 | temp +1.0 degC (6.7 noise floors)  | moderate change -> ACTIVE |
  | 400-510 | temp +6.0 degC (40 noise floors)   | sudden change -> ALERT directly |
  | 600-700 | humidity +4.0 %RH (5.0 floors)     | a different channel drives ACTIVE |
  | 800-900 | light +140 lux (4.7 floors)        | the third channel drives ACTIVE |

Usage::

    python tests/make_parity_fixture.py
"""

from __future__ import annotations

import csv
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "parity_trace.csv"
DURATION_S = 900
FIELDS = ["timestamp", "temperature", "humidity", "pressure", "light"]


def ramp(t: float, t0: float, t1: float, a: float, b: float) -> float:
    if t <= t0:
        return a
    if t >= t1:
        return b
    return a + (b - a) * (t - t0) / (t1 - t0)


def temperature(t: float) -> float:
    if t < 100:
        return 24.0
    if t < 103:
        return ramp(t, 100, 103, 24.0, 25.0)
    if t < 250:
        return 25.0
    if t < 253:
        return ramp(t, 250, 253, 25.0, 24.0)
    if t < 400:
        return 24.0
    if t < 402:
        return ramp(t, 400, 402, 24.0, 30.0)
    if t < 500:
        return 30.0
    if t < 510:
        return ramp(t, 500, 510, 30.0, 24.0)
    return 24.0


def humidity(t: float) -> float:
    """Weakly coupled to temperature, plus one independent step at t = 600."""
    value = 45.0 - 0.5 * (temperature(t) - 24.0)
    if 600 <= t < 700:
        value += 4.0
    return value


def light(t: float) -> float:
    """Constant, plus one independent step at t = 800."""
    return 460.0 if t >= 800 else 320.0


def main() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for i in range(DURATION_S):
            t = float(i)
            w.writerow(
                {
                    "timestamp": t,
                    "temperature": round(temperature(t), 2),
                    "humidity": round(humidity(t), 2),
                    "pressure": 1012.0,
                    "light": round(light(t), 1),
                }
            )
    print(f"wrote {DURATION_S} rows to {FIXTURE}")


if __name__ == "__main__":
    main()
