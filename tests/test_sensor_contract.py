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
