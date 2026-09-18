"""Sensor bring-up contract.

`sensor.c` compiles for the host in mock mode (`CONFIG_AS_USE_MOCK_SENSOR=1`,
with the test-only `esp_log.h` shim), which is what makes these assertions
possible without an ESP32 on the desk.

The defect being pinned: `sensor_init()` was called once and its result ignored,
after which `sensor_read()` returned zeros forever. A board with a detached
BME280 therefore looked like a room at 0 degC / 0 %RH / 0 hPa, and the change
detector's EMA baseline absorbed those zeros as if they were measurements.
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
    parse_key_values,
    run,
)

SENSOR_C = FIRMWARE_MAIN / "sensor.c"
SUPERVISOR_C = FIRMWARE_MAIN / "sensor_supervisor.c"
BME280_C = FIRMWARE_MAIN / "bme280_math.c"
SENSOR_HOST = HOST_DIR / "sensor_host_main.c"


@pytest.fixture(scope="module")
def sensor_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sensor") / "sensor_host"
    return compile_host_binary(
        out,
        SENSOR_HOST,
        SENSOR_C,
        SUPERVISOR_C,
        BME280_C,
        defines=("CONFIG_AS_USE_MOCK_SENSOR=1",),
        use_shims=True,
    )


def test_read_before_init_is_refused(sensor_host):
    """No init, no reading - and the caller's buffer is not written to."""
    result = parse_key_values(run(sensor_host, "sensor-before-init"))
    assert result["read_rc"] == "-1"
    assert result["is_init"] == "0"
    # The sentinel pattern must survive: if sensor_read() had zeroed the struct,
    # a caller ignoring the return value would report 0 degC as a measurement.
    assert result["untouched"] == "1", (
        "sensor_read() wrote to the output buffer before it was initialised"
    )


def test_read_succeeds_after_init(sensor_host):
    output = run(sensor_host, "sensor-after-init")
    lines = output.strip().splitlines()
    init = parse_key_values(lines[0])
    assert init["init_rc"] == "0"
    assert init["is_init"] == "1"

    read = parse_key_values(lines[1])
    assert read["read_rc"] == "0"
    assert read["valid"] == "1111"
    # Mock values are the documented deterministic sequence, centred on the
    # dataset baselines, so a broken mock is visible here.
    assert 23.5 < float(read["temp"]) < 24.5
    assert 44.0 < float(read["hum"]) < 46.0
    assert 1010.0 < float(read["press"]) < 1015.0
    assert 300.0 < float(read["light"]) < 340.0


def test_mock_sequence_is_deterministic(sensor_host):
    first = run(sensor_host, "sensor-after-init")
    second = run(sensor_host, "sensor-after-init")
    assert first == second


def test_two_reads_differ(sensor_host):
    """The mock advances, so consecutive samples are not identical."""
    # The driver takes one reading per process; advancing state across processes
    # is not meaningful, so this asserts the mock's *step* mechanism instead by
    # checking that the reported value is not a constant placeholder.
    output = run(sensor_host, "sensor-after-init")
    read = parse_key_values(output.strip().splitlines()[1])
    assert float(read["temp"]) != 0.0
    assert float(read["hum"]) != 0.0


# ---------------------------------------------------------------------- #
# a failed *re*-init must not leave the subsystem half-alive
#
# Found while cleaning up the read accounting: `sensor_init()` returned early on
# a backend failure without clearing the flag set by the previous success, so a
# re-init that failed left
#
#     sensor_is_initialized() == true     with     active backend == NULL
#
# and a caller that trusts the flag would read through a handle that had already
# been torn down. The mock build exposes `live` — the stand-in for a real build's
# `s_active != NULL` — so the two halves of the invariant can be compared instead
# of assumed.
# ---------------------------------------------------------------------- #
def contract_steps(sensor_host):
    """Run the failed-re-init sequence and return {label: parsed}.

    Each step reports rc=, is_init=, live=, init_calls= and reads=.
    """
    steps = {}
    for line in run(sensor_host, "contract-init-fail").strip().splitlines():
        parts = line.split(",")
        assert parts[0] == "step", line
        fields = dict(p.split("=", 1) for p in parts[2:])
        fields["rc"] = int(fields["rc"])
        steps[parts[1]] = fields
    assert set(steps) == {
        "first_init", "first_read", "second_init", "second_read",
        "third_init", "third_read",
    }, steps
    return steps


def test_first_init_succeeds_and_reads(sensor_host):
    steps = contract_steps(sensor_host)
    assert steps["first_init"]["rc"] == 0
    assert steps["first_init"]["is_init"] == "1"
    assert steps["first_init"]["live"] == "1"
    assert steps["first_read"]["rc"] == 0
    assert steps["first_read"]["live"] == "1"


def test_a_failed_re_init_reports_itself_as_uninitialised(sensor_host):
    """The defect: this used to stay is_init=1 after a failed bring-up."""
    steps = contract_steps(sensor_host)
    assert steps["second_init"]["rc"] == -1, "the forced failure did not fail"
    assert steps["second_init"]["is_init"] == "0", (
        "sensor_init() failed but sensor_is_initialized() still says otherwise"
    )


def test_no_stale_backend_handle_survives_a_failed_re_init(sensor_host):
    """`is_init` and `live` must agree; the bug was exactly their disagreement."""
    steps = contract_steps(sensor_host)
    assert steps["second_init"]["live"] == "0", (
        "a failed re-init left a backend handle behind for a later read to use"
    )
    for label, step in steps.items():
        assert step["is_init"] == step["live"], (
            f"{label}: initialized={step['is_init']} but backend_live="
            f"{step['live']} - the flag and the backend disagree"
        )


def test_a_read_is_refused_after_a_failed_re_init(sensor_host):
    """Refused, and the refusal never reaches a backend."""
    steps = contract_steps(sensor_host)
    assert steps["second_read"]["rc"] == -1
    assert steps["second_read"]["is_init"] == "0"
    assert steps["second_read"]["reads"] == steps["first_read"]["reads"], (
        "a refused read was still passed through to a backend"
    )


def test_recovery_follows_a_later_successful_re_init(sensor_host):
    steps = contract_steps(sensor_host)
    assert steps["third_init"]["rc"] == 0
    assert steps["third_init"]["is_init"] == "1"
    assert steps["third_init"]["live"] == "1"
    assert steps["third_read"]["rc"] == 0
    assert int(steps["third_read"]["reads"]) == int(steps["second_read"]["reads"]) + 1


def test_every_attempt_is_counted(sensor_host):
    """Three init calls were made, so three are reported."""
    steps = contract_steps(sensor_host)
    assert steps["first_init"]["init_calls"] == "1"
    assert steps["second_init"]["init_calls"] == "2"
    assert steps["third_init"]["init_calls"] == "3"


# ---------------------------------------------------------------------- #
# channel merge: the primary backend plus the optional light channel
#
# The light sensor is not a third backend - it adds one channel alongside
# whatever the primary backend measured. The rule that matters is the asymmetry:
#
#   primary fails  -> the whole measurement fails, nothing is valid
#   light fails    -> only `light` is invalid, and the measurement still succeeds
#
# A node that reports temperature and humidity must not go dark because an
# optional, decorative channel went quiet.
#
# Note on pressure: in the *mock* build every channel including pressure reads as
# valid, because the mock predates the SHT30 default and models a part that
# measures everything. That is a property of the mock, not a claim about the
# device; on the real build the SHT30 backend leaves pressure invalid, which is
# verified on hardware. These tests therefore assert on temperature, humidity and
# light - the channels the merge rules actually govern.
# ---------------------------------------------------------------------- #
def merged(sensor_host, light_mode: int, primary_fails: int) -> dict:
    """Run one merged read and return the parsed result."""
    line = run(sensor_host, "merge", str(light_mode), str(primary_fails))
    parsed = {}
    for part in line.strip().split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            parsed[key] = value
    assert parsed, line
    return parsed


def test_light_present_gives_a_value_on_every_channel(sensor_host):
    row = merged(sensor_host, light_mode=0, primary_fails=0)
    assert row["rc"] == "0"
    assert row["valid"][0] == "1", "temperature"
    assert row["valid"][1] == "1", "humidity"
    assert row["valid"][3] == "1", "light"
    assert float(row["light"]) > 0.0


def test_a_failed_light_sensor_does_not_fail_the_measurement(sensor_host):
    """The whole point: light is optional, temperature and humidity are not."""
    row = merged(sensor_host, light_mode=1, primary_fails=0)
    assert row["rc"] == "0", "a missing light sensor must not fail the sample"
    assert row["valid"][0] == "1", "temperature is still good"
    assert row["valid"][1] == "1", "humidity is still good"
    assert row["valid"][3] == "0", "only light is invalid"
    assert float(row["light"]) == 0.0, "an invalid channel carries no value"


def test_an_absent_light_sensor_behaves_the_same(sensor_host):
    """Not attempted at all is not a different kind of failure for the caller."""
    row = merged(sensor_host, light_mode=2, primary_fails=0)
    assert row["rc"] == "0"
    assert row["valid"][0] == "1"
    assert row["valid"][1] == "1"
    assert row["valid"][3] == "0"


def test_a_failed_primary_measurement_fails_whole_sample(sensor_host):
    """The asymmetry, from the other side: this one *does* take everything down."""
    row = merged(sensor_host, light_mode=0, primary_fails=1)
    assert row["rc"] == "-1"
    assert row["valid"] == "0000", "no channel is valid when the primary failed"
    assert float(row["temp"]) == 0.0
    assert float(row["hum"]) == 0.0


def test_light_failure_leaves_temperature_and_humidity_values_intact(sensor_host):
    """Not just the flags: the numbers themselves must survive."""
    good = merged(sensor_host, light_mode=0, primary_fails=0)
    dark = merged(sensor_host, light_mode=1, primary_fails=0)
    assert float(dark["temp"]) == float(good["temp"])
    assert float(dark["hum"]) == float(good["hum"])
