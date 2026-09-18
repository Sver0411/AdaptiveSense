"""Duty-cycle accounting (`firmware/main/power_stats.c`).

The ESP-IDF light-sleep callback fires for *every* automatic light sleep, and the
chip can enter one during any `vTaskDelay()` — the main loop's scheduled idle, but
equally the BH1750's ~180 ms one-shot conversion wait, or a transmission. Before
this rule existed there was only one pair of counters, so dividing them by the
scheduled idle time mixed every phase's sleep into a ratio that was supposed to
describe the idle window alone.

The rule under test: a light sleep always reaches the totals, and reaches the
idle counters only when the duty cycle was in its PM_SLEEP phase. From that rule
`idle_light_sleep_s <= light_sleep_s` follows by construction — the ratio cannot
exceed 1 unless the accounting is broken, which is what these tests check rather
than clamp.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from host_build import (  # noqa: E402
    FIRMWARE_MAIN,
    HOST_DIR,
    compile_host_binary,
    run,
)

POWER_STATS_C = FIRMWARE_MAIN / "power_stats.c"
POWER_HOST = HOST_DIR / "power_stats_host_main.c"


@pytest.fixture(scope="module")
def power_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("power_stats") / "power_stats_host"
    return compile_host_binary(out, POWER_HOST, POWER_STATS_C)


def apply(power_host, steps: str) -> dict:
    """Apply a timeline and return the resulting counters."""
    line = run(power_host, "stats", steps).strip()
    parsed = {}
    for part in line.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            parsed[key] = value
    assert parsed, line
    return parsed


# ---------------------------------------------------------------------- #
# the attribution rule
# ---------------------------------------------------------------------- #
def test_a_sleep_during_the_idle_window_reaches_both_counters(power_host):
    row = apply(power_host, "sched:60,PM_SLEEP:30")
    assert float(row["total_s"]) == pytest.approx(30.0)
    assert float(row["idle_s"]) == pytest.approx(30.0)
    assert int(row["entries"]) == 1
    assert int(row["idle_entries"]) == 1


def test_a_sleep_outside_the_idle_window_reaches_only_the_total(power_host):
    """The case that motivated the split: a conversion wait is sleep, but it is
    not scheduled idle."""
    for phase in ("PM_SAMPLE", "PM_TRANSMIT", "PM_ACTIVE"):
        row = apply(power_host, f"sched:60,{phase}:0.18")
        assert float(row["total_s"]) == pytest.approx(0.18), phase
        assert float(row["idle_s"]) == "0.000000" or float(row["idle_s"]) == 0.0, (
            f"{phase} sleep must not be attributed to the idle window"
        )
        assert int(row["idle_entries"]) == 0


def test_the_bh1750_conversion_wait_scenario(power_host):
    """The concrete case: idle sleep, plus a conversion wait that also slept.

    The total sees both; the idle window sees only its own share, so the ratio
    describes the idle window rather than everything the chip ever did.
    """
    row = apply(power_host, "sched:60,PM_SLEEP:55,PM_SAMPLE:0.18,PM_SLEEP:4")
    assert float(row["total_s"]) == pytest.approx(59.18)
    assert float(row["idle_s"]) == pytest.approx(59.0)
    assert float(row["ratio"]) == pytest.approx(59.0 / 60.0)


# ---------------------------------------------------------------------- #
# the invariant
# ---------------------------------------------------------------------- #
def test_idle_never_exceeds_the_total(power_host):
    """Holds by construction: every idle entry is also a total entry."""
    row = apply(power_host, "sched:100,PM_SLEEP:10,PM_SAMPLE:5,PM_SLEEP:20,"
                            "PM_TRANSMIT:2,PM_SLEEP:30,PM_ACTIVE:1")
    assert float(row["idle_s"]) <= float(row["total_s"])
    assert int(row["idle_entries"]) <= int(row["entries"])
    assert row["invariant_ok"] == "1"


def test_the_ratio_cannot_exceed_one(power_host):
    """Even with more non-idle sleep than idle sleep, the ratio stays a ratio of
    the idle window. It must not be clamped into looking correct — it must be
    correct because of how it is accumulated."""
    row = apply(power_host, "sched:10,PM_SLEEP:8,PM_SAMPLE:50,PM_TRANSMIT:50")
    assert float(row["total_s"]) == pytest.approx(108.0)
    assert float(row["idle_s"]) == pytest.approx(8.0)
    assert float(row["ratio"]) == pytest.approx(0.8)
    assert float(row["ratio"]) <= 1.0
    assert row["invariant_ok"] == "1"


def test_the_ratio_is_the_idle_share_of_the_scheduled_window(power_host):
    row = apply(power_host, "sched:40,PM_SLEEP:30,PM_SAMPLE:9")
    assert float(row["ratio"]) == pytest.approx(30.0 / 40.0)


# ---------------------------------------------------------------------- #
# degenerate inputs
# ---------------------------------------------------------------------- #
def test_zero_and_negative_sleeps_are_ignored(power_host):
    row = apply(power_host, "sched:60,PM_SLEEP:0,PM_SLEEP:-5,PM_SAMPLE:0")
    assert int(row["entries"]) == 0
    assert float(row["total_s"]) == 0.0


def test_nothing_scheduled_gives_a_zero_ratio(power_host):
    row = apply(power_host, "PM_SLEEP:10")
    assert float(row["sched"]) == 0.0
    assert float(row["ratio"]) == 0.0
    assert row["invariant_ok"] == "1"


def test_counters_start_at_zero(power_host):
    row = apply(power_host, "sched:0")
    assert int(row["entries"]) == 0
    assert int(row["idle_entries"]) == 0
    assert float(row["total_s"]) == 0.0
    assert row["invariant_ok"] == "1"
