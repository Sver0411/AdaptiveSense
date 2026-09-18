"""MQTT payload format.

The payload is the node's only external interface, so its meaning matters. The
specific defect these tests pin down: an unavailable channel used to be sent as
`"light": 0`, and a consumer could not tell that from a genuinely dark room. The
replacement sends `null` for an unavailable channel and carries an explicit
`valid` map.

Which channels are *available* is a property of the shipped configuration, not of
this test: the SHT30 measures temperature and humidity, a BME280 would also
measure pressure, and the BH1750 is an optional light channel. So the expected
validity mask is derived from `config.example.h` rather than written down here —
otherwise turning the light sensor off in configuration would leave these tests,
and `payload_bytes_per_upload`, describing a build nobody ships.

`tests/c_host/payload_host_main.c` builds the payload on the host from the same
source the device runs, so the sizes below are measurements and not estimates.
"""

from __future__ import annotations

import json
import re
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
from simulator.config import load_config  # noqa: E402

sys.path.insert(0, str(HERE.parent))

PAYLOAD_C = FIRMWARE_MAIN / "communication_payload.c"
PAYLOAD_HOST = HOST_DIR / "payload_host_main.c"

CHANNELS = ("temperature", "humidity", "pressure", "light")

# Channel order is fixed by `sen_channel_t` in firmware/main/sensor.h and must not
# be rearranged: the payload, the change detector and the simulator all index by it.
LIGHT_INDEX = CHANNELS.index("light")


@pytest.fixture(scope="module")
def payload_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("payload") / "payload_host"
    return compile_host_binary(out, PAYLOAD_HOST, PAYLOAD_C)


def build(
    payload_host: Path,
    *,
    values=(24.1, 45.2, 1012.3, 0.0),
    valid="1110",
    state="STABLE",
    interval=60.0,
    event=0,
    timestamp=1234567890,
    device_id="node-01",
    max_len=None,
) -> str:
    args = [device_id, timestamp, *values, valid, state, interval, event]
    if max_len is not None:
        args.append(max_len)
    return run(payload_host, *args).strip()


def parse(payload_host: Path, **kwargs) -> dict:
    raw = build(payload_host, **kwargs)
    assert not raw.startswith("ERROR:"), raw
    return json.loads(raw)


# ---------------------------------------------------------------------- #
# the defect being fixed
# ---------------------------------------------------------------------- #
def masked(mask: str, *, light: bool | None = None) -> str:
    """A validity mask with the light channel forced to `light`.

    Used by the tests that are about an *unavailable* channel, which is a property
    of the payload builder and not of the shipped sensor set.
    """
    if light is None:
        return mask
    chars = list(mask)
    chars[LIGHT_INDEX] = "1" if light else "0"
    return "".join(chars)


def test_unavailable_channel_is_null_not_zero(payload_host):
    """An unavailable channel must read as null; 0 lux would be a lie."""
    payload = parse(payload_host, valid=masked(_configured_valid_mask(), light=False))
    assert payload["light"] is None, "an unavailable channel must not read as 0"
    assert payload["valid"]["light"] is False
    # and the value the sensor layer left behind (0.0) is not what is sent
    assert payload["light"] != 0


def test_available_channels_carry_numbers(payload_host):
    payload = parse(payload_host, values=(24.1, 45.2, 1012.3, 0.0), valid="1110")
    assert payload["temperature"] == pytest.approx(24.1, abs=0.005)
    assert payload["humidity"] == pytest.approx(45.2, abs=0.005)
    assert payload["pressure"] == pytest.approx(1012.3, abs=0.005)
    assert payload["valid"] == {
        "temperature": True, "humidity": True, "pressure": True, "light": False
    }


def test_zero_is_still_reported_when_the_channel_is_valid(payload_host):
    """A real 0 reading must survive; the fix must not swallow it."""
    payload = parse(payload_host, values=(0.0, 0.0, 0.0, 0.0), valid="1111")
    assert payload["temperature"] == 0.0
    assert payload["light"] == 0.0
    assert all(payload["valid"].values())


def test_all_channels_invalid(payload_host):
    payload = parse(payload_host, valid="0000")
    for name in CHANNELS:
        assert payload[name] is None
        assert payload["valid"][name] is False


# ---------------------------------------------------------------------- #
# the rest of the schema
# ---------------------------------------------------------------------- #
def test_schema_is_complete(payload_host):
    payload = parse(payload_host, state="ALERT", interval=5.0, event=1)
    assert set(payload) == {
        "device_id", "timestamp", "temperature", "humidity", "pressure", "light",
        "sampling_interval", "state", "event", "valid",
    }
    assert payload["device_id"] == "node-01"
    assert payload["state"] == "ALERT"
    assert payload["sampling_interval"] == pytest.approx(5.0)
    assert payload["event"] is True


def test_timestamp_is_milliseconds_without_decimals(payload_host):
    payload = parse(payload_host, timestamp=1757950800123)
    assert payload["timestamp"] == 1757950800123


def test_payload_is_deterministic(payload_host):
    first = build(payload_host)
    second = build(payload_host)
    assert first == second


def test_overflow_is_reported_not_truncated(payload_host):
    """A payload that does not fit must fail loudly, never be sent truncated."""
    raw = build(payload_host, max_len=64)
    assert raw == "ERROR:-1"


# ---------------------------------------------------------------------- #
# the configured payload size is measured, not guessed
# ---------------------------------------------------------------------- #
def _config_define(name: str) -> int:
    """Read a numeric `#define` from the committed configuration."""
    text = (FIRMWARE_MAIN / "config.example.h").read_text(encoding="utf-8")
    match = re.search(rf"^#define\s+{name}\s+(\d+)", text, flags=re.M)
    assert match, f"{name} not found in config.example.h"
    return int(match.group(1))


def _configured_backend() -> int:
    """1 = BME280 (temperature, humidity, pressure), 2 = SHT30 (temperature,
    humidity). A backend marks the channels it cannot measure invalid, and an
    invalid channel is sent as `null`, so the payload length depends on this.
    """
    backend = _config_define("CONFIG_AS_SENSOR_BACKEND")
    assert backend in (1, 2), f"unexpected CONFIG_AS_SENSOR_BACKEND={backend}"
    return backend


def _configured_valid_mask() -> str:
    """Derive the channel-validity mask from the shipped configuration.

    Channel order is fixed by `sen_channel_t`: temperature, humidity, pressure,
    light. What each build can actually measure:

      temperature, humidity   every backend
      pressure                only the BME280 (CONFIG_AS_SENSOR_BACKEND == 1)
      light                   only when the optional BH1750 is compiled in
                              (CONFIG_AS_USE_BH1750) *and* the policy has not
                              excluded the channel (CONFIG_AS_USE_LIGHT)

    Deriving this is what keeps the measurement honest: with the BH1750 fitted the
    light channel carries a number, and a build without it sends `null`, and the
    two do not produce the same number of bytes in general.
    """
    pressure = _configured_backend() == 1
    light = bool(_config_define("CONFIG_AS_USE_BH1750")) and \
        bool(_config_define("CONFIG_AS_USE_LIGHT"))
    return "".join("1" if v else "0" for v in (True, True, pressure, light))


# Representative readings for the shipped build: an indoor temperature and
# humidity, and a normal indoor illuminance. The size constant is anchored to
# these, and the test below shows how far a realistic payload can move around them.
REPRESENTATIVE_VALUES = (27.51, 56.47, 0.0, 141.7)
REPRESENTATIVE_TIMESTAMP = 1234567890


def test_configured_payload_size_matches_the_shipped_payload(payload_host):
    """`payload_bytes_per_upload` must describe what the configured build emits.

    The channel-validity mask is derived from the same configuration the firmware
    is built from, so switching the backend — or turning the light sensor off —
    cannot leave the size constant describing some other build.
    """
    cfg = load_config()
    configured = int(cfg["adaptive"]["energy"]["payload_bytes_per_upload"])

    shipped = build(
        payload_host,
        values=REPRESENTATIVE_VALUES,
        valid=_configured_valid_mask(),
        timestamp=REPRESENTATIVE_TIMESTAMP,
    )
    assert len(shipped) == configured, (
        f"the shipped configuration (validity {_configured_valid_mask()}) produces a "
        f"{len(shipped)}-byte payload but payload_bytes_per_upload={configured}; "
        f"update the YAML to the measured size"
    )


def test_the_representative_size_is_one_point_in_a_narrow_band(payload_host):
    """The constant is representative, not exact — and the band is narrow.

    The length moves with the digits in the readings (`0.0` vs `12345.6` lux) and
    with the width of the timestamp, so a single number can only ever describe a
    representative case. What matters for the energy proxy is that the variation is
    small next to the buffer and next to the difference between strategies.
    """
    mask = _configured_valid_mask()
    lengths = []
    for lux in (0.0, 8.3, 141.7, 757.5, 12345.6, 54612.5):
        lengths.append(
            len(build(payload_host, values=(27.51, 56.47, 0.0, lux), valid=mask))
        )
    for timestamp in (1234567890, 9999999999999):
        lengths.append(
            len(build(payload_host, values=REPRESENTATIVE_VALUES, valid=mask,
                      timestamp=timestamp))
        )
    assert max(lengths) - min(lengths) <= 12, (
        f"payload length varies by more than expected: {min(lengths)}-{max(lengths)}"
    )


def test_a_realistic_payload_still_fits_the_buffer(payload_host):
    """The widest realistic payload must fit COMM_PAYLOAD_MAX_LEN with headroom."""
    widest = build(
        payload_host,
        values=(123.45, 100.0, 1100.0, 65535.0),
        valid="1111",
        timestamp=9999999999999,
    )
    assert len(widest) <= 320


def test_payload_length_depends_on_the_digits(payload_host):
    """Why the configured size is representative rather than exact.

    The JSON is built with fixed precision, so its length moves with the number
    of integer digits in the readings and with the boolean width of the validity
    flags. The configured constant describes the common case; it is not a wire
    protocol frame size.
    """
    narrow = build(payload_host, values=(0.0, 0.0, 0.0, 0.0), valid="1110")
    wide = build(payload_host, values=(123.45, 100.0, 1100.0, 65535.0), valid="1110")
    assert len(narrow) < len(wide)
    # and every form still fits the declared buffer with room to spare
    assert len(wide) <= 320


def test_payload_fits_the_declared_buffer(payload_host):
    """COMM_PAYLOAD_MAX_LEN must have headroom for the widest realistic payload."""
    widest = build(
        payload_host,
        values=(123.45, 100.0, 1100.0, 65535.0),
        valid="1111",
        state="STABLE",
        interval=60.0,
        timestamp=9999999999999,
        device_id="node-01",
    )
    assert len(widest) <= 320
