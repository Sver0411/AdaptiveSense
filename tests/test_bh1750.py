"""BH1750 / GY-302 light channel (`firmware/main/bh1750_proto.c`).

The protocol and the availability policy are pure C99, so both run on a
workstation against a fake transport instead of an I2C bus. That matters here
because two of the claims are otherwise only checkable by unplugging the part:

  * a short frame must be rejected, not read as a plausible lux value;
  * an unplugged light sensor must back off and recover on its own, without
    taking the temperature and humidity channels with it.

Reference point for the conversion: in the first hardware session the module at
0x23 answered with raw = 66, which is 55.0 lx. That is how the 1.2 divisor is
anchored to real light rather than to a datasheet alone.
"""

from __future__ import annotations

import math
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

BH1750_C = FIRMWARE_MAIN / "bh1750_proto.c"
BH1750_HOST = HOST_DIR / "bh1750_host_main.c"


@pytest.fixture(scope="module")
def bh1750_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bh1750") / "bh1750_host"
    return compile_host_binary(out, BH1750_HOST, BH1750_C)


def kv(line: str) -> dict:
    """Parse `a=1,b=2` style output into a dict."""
    parsed = {}
    for part in line.strip().split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            parsed[key] = value
    return parsed


def lux_table(bh1750_host) -> dict[int, float]:
    rows = {}
    for line in run(bh1750_host, "lux-table").strip().splitlines():
        parsed = kv(line)
        rows[int(parsed["raw"])] = float(parsed["lux"])
    return rows


# ---------------------------------------------------------------------- #
# conversion
# ---------------------------------------------------------------------- #
def test_darkness_reads_as_zero_not_as_an_error(bh1750_host):
    """raw 0 is a real reading, not a failure."""
    table = lux_table(bh1750_host)
    assert table[0] == 0.0


def test_the_module_s_reference_value_converts_correctly(bh1750_host):
    """raw 66 -> 55.0 lx.

    This is the anchor: 66 is what the physical GY-302 returned in the first
    hardware session, so the divisor is confirmed against real light.
    """
    table = lux_table(bh1750_host)
    assert table[66] == pytest.approx(55.0, abs=1e-4)


def test_the_register_maximum_does_not_overflow(bh1750_host):
    table = lux_table(bh1750_host)
    assert table[65535] == pytest.approx(54612.5, abs=0.01)  # float32 ulp is ~0.004 up here
    assert table[65535] < 1e6


def test_conversion_is_finite_non_negative_and_monotonic(bh1750_host):
    """No NaN, no Inf, no negative lux anywhere in the register range."""
    table = lux_table(bh1750_host)
    raws = sorted(table)
    for raw in raws:
        value = table[raw]
        assert math.isfinite(value), f"raw {raw} produced {value}"
        assert not math.isnan(value)
        assert value >= 0.0, f"raw {raw} produced a negative lux"
    values = [table[r] for r in raws]
    assert values == sorted(values), "lux must increase with the raw count"


def test_a_normal_room_value_is_in_a_plausible_range(bh1750_host):
    """Deliberately a wide range: the point is to catch nonsense, not to
    define what a room is."""
    table = lux_table(bh1750_host)
    assert 0.0 < table[66] < 20000.0


# ---------------------------------------------------------------------- #
# measurement over a fake transport
# ---------------------------------------------------------------------- #
def measure(bh1750_host, scenario, wait_ms=None) -> dict:
    args = ["measure", scenario]
    if wait_ms is not None:
        args.append(str(wait_ms))
    return kv(run(bh1750_host, *args).strip().splitlines()[0])


def test_a_complete_measurement_succeeds(bh1750_host):
    row = measure(bh1750_host, "ok")
    assert row["rc"] == "0"
    assert float(row["lux"]) == pytest.approx(55.0, abs=1e-4)
    # power on, then start the conversion, then one read of two bytes.
    assert row["writes"] == "2"
    assert row["reads"] == "1"


def test_zero_lux_is_reported_as_a_value(bh1750_host):
    row = measure(bh1750_host, "dark")
    assert row["rc"] == "0"
    assert float(row["lux"]) == 0.0


def test_maximum_lux_is_reported_as_a_value(bh1750_host):
    row = measure(bh1750_host, "max")
    assert row["rc"] == "0"
    assert float(row["lux"]) == pytest.approx(54612.5, abs=0.01)


def test_a_failed_command_write_is_a_failure(bh1750_host):
    row = measure(bh1750_host, "write-fail")
    assert row["rc"] == "-1"
    assert row["reads"] == "0", "no read should follow a failed command"
    assert row["delays"] == "0", "and no conversion wait should be spent"


def test_a_short_frame_is_rejected(bh1750_host):
    """One byte is not a measurement. Accepting it would produce a plausible
    looking wrong value, which is worse than having no value."""
    row = measure(bh1750_host, "short-read")
    assert row["rc"] == "-1"


def test_a_failed_read_is_a_failure(bh1750_host):
    row = measure(bh1750_host, "read-error")
    assert row["rc"] == "-1"


def test_the_conversion_wait_is_bounded_and_configurable(bh1750_host):
    """One wait of the configured length, never a poll-for-completion loop."""
    default = measure(bh1750_host, "ok")
    assert default["delays"] == "1"
    assert int(default["wait_ms"]) == 180

    short = measure(bh1750_host, "ok", 10)
    assert int(short["wait_ms"]) == 10
    assert short["rc"] == "0"


def test_a_failure_leaves_the_output_untouched(bh1750_host):
    """`lux` must not be written when the measurement failed: a caller that
    ignores the return value must not see a stale or sentinel value that looks
    like a reading."""
    row = measure(bh1750_host, "read-error")
    assert row["rc"] == "-1"
    assert float(row["lux"]) == -1.0, "the sentinel was not overwritten"


# ---------------------------------------------------------------------- #
# availability policy
# ---------------------------------------------------------------------- #
def policy_rows(bh1750_host, probe_ms, steps) -> list[dict]:
    out = run(bh1750_host, "policy", str(probe_ms), steps)
    return [kv(line) for line in out.strip().splitlines()
            if line.startswith("policy,t=")]


def test_the_first_attempt_is_allowed_immediately(bh1750_host):
    rows = policy_rows(bh1750_host, 5000, "0:ok")
    assert rows[0]["should"] == "1"
    assert rows[0]["available"] == "1"


def test_one_failed_read_is_tolerated(bh1750_host):
    """A single failed transfer must not drop the channel."""
    rows = policy_rows(bh1750_host, 5000, "0:ok,1000:fail")
    assert rows[-1]["available"] == "1"
    assert rows[-1]["consec"] == "1"
    assert rows[-1]["events"] == "0"


def test_two_consecutive_failures_declare_it_gone(bh1750_host):
    rows = policy_rows(bh1750_host, 5000, "0:ok,1000:fail,2000:fail")
    last = rows[-1]
    assert last["available"] == "0"
    assert last["events"] == "1", "the transition is counted once, not per failure"


def test_an_absent_sensor_does_not_generate_traffic(bh1750_host):
    """The point of the backoff: no I2C attempt at all between re-probes."""
    rows = policy_rows(bh1750_host, 5000, "0:ok,1000:fail,2000:fail,"
                                          "3000:fail,4000:fail,5000:fail")
    backing_off = [r for r in rows if int(r["t"]) in (3000, 4000, 5000)]
    assert backing_off, rows
    assert all(r["should"] == "0" for r in backing_off)
    assert int(rows[-1]["skips"]) == 3


def test_the_re_probe_happens_after_the_interval(bh1750_host):
    rows = policy_rows(bh1750_host, 5000, "0:ok,1000:fail,2000:fail,7000:-")
    at_7000 = [r for r in rows if r["t"] == "7000"][0]
    assert at_7000["should"] == "1", "the backoff has elapsed"


def test_recovery_needs_no_restart(bh1750_host):
    """Unplugged, then plugged back in: the next probe after the interval
    restores the channel and clears the streak."""
    rows = policy_rows(bh1750_host, 5000,
                       "0:ok,1000:fail,2000:fail,3000:fail,7000:ok,8000:ok")
    gone = [r for r in rows if r["t"] == "3000"][0]
    assert gone["available"] == "0"

    back = [r for r in rows if r["t"] == "7000"][0]
    assert back["available"] == "1"
    assert back["consec"] == "0", "a successful read resets the streak"
    assert back["events"] == "1", "the outage is still on the record"

    after = [r for r in rows if r["t"] == "8000"][0]
    assert after["reads"] == "3"


def test_repeated_outages_are_counted_separately(bh1750_host):
    rows = policy_rows(bh1750_host, 1000,
                       "0:ok,100:fail,200:fail,1500:ok,"
                       "2000:fail,3000:fail,4500:ok")
    assert rows[-1]["events"] == "2"
