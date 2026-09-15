"""BME280 driver maths: calibration parsing and compensation.

Two things are verified here, both of which were broken in v0.1:

1. **Calibration parsing** (`firmware/main/bme280_math.c`). The register image is
   built by the test, so the expected coefficients are known independently:
   `dig_H1` must come from `0xA1` (the last byte of the 26-byte block at `0x88`),
   the humidity coefficients from offsets 0..6 of the 7-byte block at `0xE1`, and
   `dig_H4`/`dig_H5` must be assembled from their nibble pairs and sign-extended.
   v0.1 read `buf[7]`/`buf[8]` out of a 7-byte buffer and took `dig_H1` from the
   wrong block entirely.

2. **Compensation** against an independent Python transcription of the vendor
   (`BoschSensortec/BME280_SensorAPI`) equations. v0.1's integer humidity path
   returned a value 4096x too large.

The C translation unit is compiled for the host, so this runs in CI with no
hardware and no ESP-IDF installation.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "firmware" / "main" / "bme280_math.c"
HEADERS = ROOT / "firmware" / "main"
HARNESS = ROOT / "tests" / "c_host" / "bme280_host_main.c"


# ---------------------------------------------------------------------- #
# register image construction
# ---------------------------------------------------------------------- #
CAL = {
    # temperature
    "dig_T1": 28414, "dig_T2": 26349, "dig_T3": 50,
    # pressure
    "dig_P1": 36938, "dig_P2": -10680, "dig_P3": 3024, "dig_P4": 7673,
    "dig_P5": -156, "dig_P6": -7, "dig_P7": 9900, "dig_P8": -10230, "dig_P9": 4285,
    # humidity
    "dig_H1": 75, "dig_H2": 370, "dig_H3": 0,
    "dig_H4": 290, "dig_H5": -50, "dig_H6": -30,
}


def _u16(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def block1() -> bytes:
    """0x88..0xA1: T1..P9 (offsets 0..23), 0xA0 reserved, dig_H1 at offset 25."""
    b = bytearray()
    b += _u16(CAL["dig_T1"]) + _u16(CAL["dig_T2"]) + _u16(CAL["dig_T3"])
    for key in ("dig_P1", "dig_P2", "dig_P3", "dig_P4", "dig_P5",
                "dig_P6", "dig_P7", "dig_P8", "dig_P9"):
        b += _u16(CAL[key])
    b += bytes([0xA0 % 256])          # offset 24: 0xA0 (reserved)
    b += bytes([CAL["dig_H1"]])       # offset 25: 0xA1
    assert len(b) == 26
    return bytes(b)


def block2() -> bytes:
    """0xE1..0xE7: H2, H3, H4, H5, H6.

    The nibble packing follows the vendor driver's own parser
    (`parse_humidity_calib_data()` in `BoschSensortec/BME280_SensorAPI`,
    `bme280.c`), which is the authority for what the silicon actually returns:

        dig_h4 = ((int8_t)reg[3] * 16) | (reg[4] & 0x0F)
        dig_h5 = ((int8_t)reg[5] * 16) | (reg[4] >> 4)

    Note that H4's high part is the byte at 0xE4 while H5's high part is the byte
    at 0xE6, with the shared register 0xE5 contributing H4's low nibble in
    `[3:0]` and H5's low nibble in `[7:4]`. This is *not* the grouping a naive
    reading of the datasheet memory map suggests, which is exactly why it is
    implemented from the vendor source and pinned by a test.
    """
    b = bytearray()
    b += _u16(CAL["dig_H2"])                       # 0xE1/0xE2 -> offsets 0,1
    b += bytes([CAL["dig_H3"]])                    # 0xE3      -> offset 2

    h4 = CAL["dig_H4"] & 0x0FFF
    h5 = CAL["dig_H5"] & 0x0FFF
    b += bytes([(h4 >> 4) & 0xFF])                 # 0xE4      -> offset 3
    b += bytes([((h5 & 0x0F) << 4) | (h4 & 0x0F)])  # 0xE5     -> offset 4
    b += bytes([(h5 >> 4) & 0xFF])                 # 0xE6      -> offset 5
    b += bytes([CAL["dig_H6"] & 0xFF])             # 0xE7      -> offset 6
    assert len(b) == 7
    return bytes(b)


# ---------------------------------------------------------------------- #
# independent Python transcription of the vendor equations
# ---------------------------------------------------------------------- #
def compensate(cal: Dict[str, int], adc_T: int, adc_P: int, adc_H: int):
    var1 = (adc_T / 16384.0 - cal["dig_T1"] / 1024.0) * cal["dig_T2"]
    var2 = (adc_T / 131072.0 - cal["dig_T1"] / 8192.0) ** 2 * cal["dig_T3"]
    t_fine = int(var1 + var2)
    temperature = max(-40.0, min(85.0, (var1 + var2) / 5120.0))

    p1 = (t_fine / 2.0) - 64000.0
    p2 = p1 * p1 * cal["dig_P6"] / 32768.0
    p2 = p2 + p1 * cal["dig_P5"] * 2.0
    p2 = (p2 / 4.0) + (cal["dig_P4"] * 65536.0)
    p3 = cal["dig_P3"] * p1 * p1 / 524288.0
    p1 = (p3 + cal["dig_P2"] * p1) / 524288.0
    p1 = (1.0 + p1 / 32768.0) * cal["dig_P1"]
    if p1 == 0.0:
        pressure = 30000.0
    else:
        pressure = 1048576.0 - adc_P
        pressure = (pressure - (p2 / 4096.0)) * 6250.0 / p1
        p1b = cal["dig_P9"] * pressure * pressure / 2147483648.0
        p2b = pressure * cal["dig_P8"] / 32768.0
        pressure = pressure + (p1b + p2b + cal["dig_P7"]) / 16.0
        pressure = max(30000.0, min(110000.0, pressure))

    h1 = t_fine - 76800.0
    h2 = cal["dig_H4"] * 64.0 + (cal["dig_H5"] / 16384.0) * h1
    h3 = adc_H - h2
    h4 = cal["dig_H2"] / 65536.0
    h5 = 1.0 + (cal["dig_H3"] / 67108864.0) * h1
    h6 = 1.0 + (cal["dig_H6"] / 67108864.0) * h1 * h5
    h6 = h3 * h4 * (h5 * h6)
    humidity = h6 * (1.0 - cal["dig_H1"] * h6 / 524288.0)
    humidity = max(0.0, min(100.0, humidity))

    return t_fine, temperature, pressure, humidity


# ---------------------------------------------------------------------- #
# harness
# ---------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        if os.environ.get("AS_SKIP_PARITY") == "1":
            pytest.skip("no C compiler and AS_SKIP_PARITY=1")
        pytest.fail("No C compiler found; the BME280 maths cannot be verified.")

    out = tmp_path_factory.mktemp("bme280") / "bme280_host"
    result = subprocess.run(
        [compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
         "-I", str(HEADERS), "-o", str(out),
         str(HARNESS), str(SOURCE), "-lm"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"build failed:\n{result.stderr}"
    return out


def run_harness(binary: Path, adc_T: int, adc_P: int, adc_H: int) -> Dict[str, float]:
    result = subprocess.run(
        [str(binary), block1().hex(), block2().hex(),
         str(adc_T), str(adc_P), str(adc_H)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"harness failed:\n{result.stderr}"
    parsed: Dict[str, float] = {}
    for line in result.stdout.strip().splitlines():
        key, value = line.split(",")
        parsed[key] = float(value)
    return parsed


def test_calibration_parsing(binary):
    """Every coefficient must match the register image built by this test."""
    got = run_harness(binary, 534203, 300000, 26559)
    for key, expected in CAL.items():
        assert got[key] == pytest.approx(expected), (
            f"{key}: parsed {got[key]}, register image says {expected}"
        )


def test_h1_comes_from_register_0xa1(binary):
    """dig_H1 lives in the 26-byte block, not in the 0xE1 humidity block."""
    got = run_harness(binary, 534203, 300000, 26559)
    # block2 byte 0 is dig_H2's LSB, which is emphatically not dig_H1.
    assert got["dig_H1"] == CAL["dig_H1"]
    assert got["dig_H1"] != (CAL["dig_H2"] & 0xFF)


def test_h4_h5_nibble_assembly_and_sign_extension(binary):
    """H4 = (0xE4 << 4) | 0xE5[3:0]; H5 = (0xE6 << 4) | 0xE5[7:4]; both 12-bit signed."""
    got = run_harness(binary, 534203, 300000, 26559)
    assert got["dig_H4"] == CAL["dig_H4"]
    # dig_H5 is deliberately negative in the fixture: the 12-bit field must be
    # sign-extended, otherwise it comes back as 4046.
    assert got["dig_H5"] == CAL["dig_H5"] < 0
    assert got["dig_H5"] != (CAL["dig_H5"] & 0x0FFF)


def test_compensation_matches_vendor_equations(binary):
    """Temperature, pressure and humidity against an independent transcription."""
    cases = [
        (534203, 300000, 26559),
        (560000, 340000, 40000),
        (500000, 260000, 10000),
    ]
    for adc_T, adc_P, adc_H in cases:
        got = run_harness(binary, adc_T, adc_P, adc_H)
        t_fine, temperature, pressure, humidity = compensate(CAL, adc_T, adc_P, adc_H)
        assert got["t_fine"] == t_fine, f"t_fine for {adc_T}"
        assert got["temperature_c"] == pytest.approx(temperature, rel=1e-9)
        assert got["pressure_pa"] == pytest.approx(pressure, rel=1e-9)
        assert got["humidity_pct"] == pytest.approx(humidity, rel=1e-9)


def test_humidity_is_in_percent_not_the_v01_scale(binary):
    """Regression guard for the 4096x error in v0.1.

    v0.1 divided the raw integer accumulator by 1024 and reported ~184 000 %RH
    for this register image; the correct answer is ~45 %RH.
    """
    got = run_harness(binary, 534203, 300000, 26559)
    assert 0.0 <= got["humidity_pct"] <= 100.0
    assert 40.0 < got["humidity_pct"] < 55.0


def test_humidity_is_clamped_to_physical_range(binary):
    """The vendor equations clamp humidity to [0, 100] %RH."""
    got = run_harness(binary, 534203, 300000, 65535)
    assert 0.0 <= got["humidity_pct"] <= 100.0
