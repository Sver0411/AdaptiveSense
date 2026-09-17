"""SHT30 protocol layer (`firmware/main/sht30_proto.c`).

The protocol is split out from the I2C transport precisely so it can be tested
here with a fake one (`tests/c_host/sht30_host_main.c`), including the paths that
matter most and are hardest to provoke on a board: a corrupted frame, a write that
fails, a read that fails.

Every golden frame below was captured from the physical SHT30 over I2C, so the CRC
and the two conversion formulas are checked against silicon rather than against
themselves:

    status word      80 10 E1                                     (cmd 0xF32D)
    measurement      6A 12 1B 90 8F 98  -> 27.51 C / 56.47 %RH     (cmd 0x2400)
    serial number    2D B1 93 E9 03 67                            (cmd 0x3780)

The requirement this file exists to pin: **a frame whose CRC does not match is not
a measurement**. `sht30_decode_measurement` and `sht30_proto_measure` must refuse
it and leave the caller's outputs untouched, so a corrupted reading can never be
scored as real change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from host_build import FIRMWARE_MAIN, HOST_DIR, compile_host_binary, parse_key_values, run  # noqa: E402

SHT30_PROTO_C = FIRMWARE_MAIN / "sht30_proto.c"
SHT30_HOST = HOST_DIR / "sht30_host_main.c"

# Sentinel the host driver initialises its outputs with, so "untouched on
# failure" is observable rather than assumed. Printed with %.4f by the driver.
SENTINEL = "-999.0000"

# Golden captures.
REAL_STATUS = "80 10 E1"
REAL_MEASUREMENT = "6A 12 1B 90 8F 98"
REAL_MEASUREMENT_T = 27.5101
REAL_MEASUREMENT_RH = 56.4691


@pytest.fixture(scope="module")
def sht30_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sht30") / "sht30_host"
    return compile_host_binary(out, SHT30_HOST, SHT30_PROTO_C)


# ---------------------------------------------------------------------- #
# CRC
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "frame,expected_crc",
    [
        (REAL_STATUS, "E1"),
        ("2D B1 93", "93"),      # serial number, first half
        ("E9 03 67", "67"),      # serial number, second half
        ("6A 12 1B", "1B"),      # measurement, temperature half
        ("90 8F 98", "98"),      # measurement, humidity half
    ],
)
def test_crc_matches_frames_captured_from_the_device(sht30_host, frame, expected_crc):
    """Five independent real frames: the algorithm is settled, not tuned."""
    result = parse_key_values(run(sht30_host, "crc", frame))
    assert result["computed"] == expected_crc
    assert result["expected"] == expected_crc
    assert result["ok"] == "1"


# ---------------------------------------------------------------------- #
# conversion
# ---------------------------------------------------------------------- #
def test_decodes_the_real_measurement_frame(sht30_host):
    result = parse_key_values(run(sht30_host, "decode", REAL_MEASUREMENT))
    assert result["ok"] == "1"
    assert float(result["temp"]) == pytest.approx(REAL_MEASUREMENT_T, abs=5e-4)
    assert float(result["hum"]) == pytest.approx(REAL_MEASUREMENT_RH, abs=5e-4)


def test_conversion_endpoints(sht30_host):
    """Raw 0 and raw 0xFFFF must land on the specified range endpoints."""

    def crc8(data: bytes) -> int:
        crc = 0xFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ 0x31) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
        return crc

    def frame(raw_t: int, raw_rh: int) -> str:
        t = raw_t.to_bytes(2, "big")
        r = raw_rh.to_bytes(2, "big")
        return " ".join(f"{b:02X}" for b in (t + bytes([crc8(t)]) + r + bytes([crc8(r)])))

    low = parse_key_values(run(sht30_host, "decode", frame(0x0000, 0x0000)))
    assert low["ok"] == "1"
    assert float(low["temp"]) == pytest.approx(-45.0, abs=1e-3)
    assert float(low["hum"]) == pytest.approx(0.0, abs=1e-3)

    high = parse_key_values(run(sht30_host, "decode", frame(0xFFFF, 0xFFFF)))
    assert high["ok"] == "1"
    assert float(high["temp"]) == pytest.approx(130.0, abs=1e-3)
    assert float(high["hum"]) == pytest.approx(100.0, abs=1e-3)


def test_a_decode_never_writes_partial_results(sht30_host):
    """A frame whose humidity CRC is broken must not yield the temperature either.

    Half a measurement is not a measurement: if the frame is not trustworthy, the
    sample is absent, not partially present.
    """
    broken_rh = "6A 12 1B 90 8F 00"  # temperature half still valid
    result = parse_key_values(run(sht30_host, "decode", broken_rh))
    assert result["ok"] == "0"
    assert result["temp"] == SENTINEL
    assert result["hum"] == SENTINEL


# ---------------------------------------------------------------------- #
# measurement sequencing
# ---------------------------------------------------------------------- #
def test_measurement_issues_the_confirmed_command_and_waits(sht30_host):
    result = parse_key_values(run(sht30_host, "measure", "good"))
    assert result["rc"] == "0"
    assert result["command"] == "2400", "the command verified on the device"
    assert result["writes"] == "1"
    assert result["reads"] == "1", "one command yields exactly one readable frame"
    assert int(result["delay_ms"]) >= 15, "high-repeatability conversion time"
    assert float(result["temp"]) == pytest.approx(REAL_MEASUREMENT_T, abs=5e-4)
    assert float(result["hum"]) == pytest.approx(REAL_MEASUREMENT_RH, abs=5e-4)


@pytest.mark.parametrize("scenario", ["badcrc_t", "badcrc_rh"])
def test_bad_crc_makes_the_measurement_absent(sht30_host, scenario):
    """The core requirement: a CRC failure must invalidate the sample."""
    result = parse_key_values(run(sht30_host, "measure", scenario))
    assert result["rc"] == "-1"
    assert result["temp"] == SENTINEL, "outputs must not be written on a CRC failure"
    assert result["hum"] == SENTINEL


def test_write_failure_is_reported_and_no_read_is_attempted(sht30_host):
    result = parse_key_values(run(sht30_host, "measure", "writefail"))
    assert result["rc"] == "-1"
    assert result["reads"] == "0", "must not read after the command failed"
    assert result["delay_ms"] == "0", "must not wait for a conversion never started"


@pytest.mark.parametrize("scenario", ["readfail", "shortread"])
def test_read_failure_is_reported(sht30_host, scenario):
    result = parse_key_values(run(sht30_host, "measure", scenario))
    assert result["rc"] == "-1"
    assert result["temp"] == SENTINEL
    assert result["hum"] == SENTINEL


def test_no_transport_is_refused(sht30_host):
    """The 'not initialised' case at this layer: nothing to talk through."""
    result = parse_key_values(run(sht30_host, "null-io"))
    assert result["rc"] == "-1"
    assert result["temp"] == SENTINEL
    assert result["hum"] == SENTINEL


# ---------------------------------------------------------------------- #
# identity
# ---------------------------------------------------------------------- #
def test_identify_accepts_the_real_status_word(sht30_host):
    result = parse_key_values(run(sht30_host, "identify", "good"))
    assert result["rc"] == "0"
    assert result["command"] == "F32D"


def test_identify_rejects_a_bad_status_checksum(sht30_host):
    """An address alone does not make a device an SHT30."""
    result = parse_key_values(run(sht30_host, "identify", "badcrc"))
    assert result["rc"] == "-1"


@pytest.mark.parametrize("scenario", ["writefail", "readfail"])
def test_identify_reports_transport_failure(sht30_host, scenario):
    result = parse_key_values(run(sht30_host, "identify", scenario))
    assert result["rc"] == "-1"


# ---------------------------------------------------------------------- #
# coverage note
# ---------------------------------------------------------------------- #
# The remaining item on the driver's checklist — "sensor not initialised" — has
# two layers, and both are covered elsewhere in this suite rather than duplicated
# here:
#
#   protocol layer   a call with no transport at all      -> test_no_transport_is_refused
#   API layer        sensor_read() before sensor_init()   -> tests/test_sensor_contract.py
#                                                            ::test_read_before_init_is_refused
#
# The API-layer test is backend-independent: it drives the mock path, so it holds
# whichever backend the build selects.
