"""MQTT payload format.

The payload is the node's only external interface, so its meaning matters. The
specific defect these tests pin down: the firmware implements a BME280 only, so
the light channel has no sensor behind it, and an earlier revision sent
`"light": 0`. A consumer could not tell that from a genuinely dark room.

The replacement sends `null` for an unavailable channel and carries an explicit
`valid` map. `tests/c_host/payload_host_main.c` builds the payload on the host
from the same source the device runs.
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
def test_unavailable_channel_is_null_not_zero(payload_host):
    """The light channel has no sensor in this build; 0 lux would be a lie."""
    payload = parse(payload_host, valid="1110")
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
def _configured_backend() -> int:
    """Read CONFIG_AS_SENSOR_BACKEND from the committed configuration.

    1 = BME280 (temperature, humidity, pressure), 2 = SHT30 (temperature,
    humidity). A backend marks the channels it cannot measure invalid, and an
    invalid channel is sent as `null`, so the payload length depends on this.
    """
    text = (FIRMWARE_MAIN / "config.example.h").read_text(encoding="utf-8")
    match = re.search(r"^#define\s+CONFIG_AS_SENSOR_BACKEND\s+(\d+)", text, flags=re.M)
    assert match, "CONFIG_AS_SENSOR_BACKEND not found in config.example.h"
    return int(match.group(1))


def test_configured_payload_size_matches_the_shipped_payload(payload_host):
    """`payload_bytes_per_upload` must describe what the configured build emits.

    The expected channel-validity mask is derived from the same configuration the
    firmware is built from, so switching the sensor backend cannot leave the size
    constant describing the other one. `tests/c_host/payload_host_main.c` builds
    the payload from the same source the device runs, so this is a measurement and
    not an estimate.
    """
    backend = _configured_backend()
    valid_mask = {
        1: "1110",  # BME280: pressure is real, no light sensor in this build
        2: "1100",  # SHT30: no pressure sensor either
    }[backend]

    cfg = load_config()
    configured = int(cfg["adaptive"]["energy"]["payload_bytes_per_upload"])

    shipped = build(
        payload_host,
        values=(27.51, 56.47, 1012.30, 0.0),
        valid=valid_mask,
        timestamp=1234567890,
    )
    assert len(shipped) == configured, (
        f"CONFIG_AS_SENSOR_BACKEND={backend} produces a {len(shipped)}-byte payload "
        f"but payload_bytes_per_upload={configured}; update the YAML to the "
        f"measured size"
    )


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
