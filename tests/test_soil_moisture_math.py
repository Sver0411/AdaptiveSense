"""Soil moisture maths (`firmware/main/soil_moisture_math.c`).

The maths are pure C99, so the direction handling, the clamping and the invalid
calibration cases all run on a workstation rather than on the ADC.

What the normalised value is, and is not: a relative index between two calibrated
endpoints (0.0 = dry endpoint, 1.0 = wet endpoint). Not volumetric water content,
not an absolute soil-moisture percentage — see the header of
`soil_moisture_math.h`.
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

SOIL_MATH_C = FIRMWARE_MAIN / "soil_moisture_math.c"
SOIL_HOST = HOST_DIR / "soil_host_main.c"

ADC_MAX_RAW = 4095  # 12-bit


@pytest.fixture(scope="module")
def soil_host(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("soil") / "soil_host"
    return compile_host_binary(out, SOIL_HOST, SOIL_MATH_C, link_math=True)


def kv(line: str) -> dict:
    parsed = {}
    for part in line.strip().split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            parsed[key] = value
    assert parsed, line
    return parsed


def normalize(soil_host, dry: int, wet: int, raw: int) -> dict:
    return kv(run(soil_host, "normalize", str(dry), str(wet), str(raw)))


# ---------------------------------------------------------------------- #
# direction handling
# ---------------------------------------------------------------------- #
def test_dry_above_wet_maps_the_endpoints_correctly(soil_host):
    """The module type whose output falls as the soil wets."""
    assert float(normalize(soil_host, 3000, 1000, 3000)["norm"]) == pytest.approx(0.0)
    assert float(normalize(soil_host, 3000, 1000, 2000)["norm"]) == pytest.approx(0.5)
    assert float(normalize(soil_host, 3000, 1000, 1000)["norm"]) == pytest.approx(1.0)


def test_dry_below_wet_maps_the_endpoints_correctly(soil_host):
    """The other module direction, handled by the same formula with no flag."""
    assert float(normalize(soil_host, 1000, 3000, 1000)["norm"]) == pytest.approx(0.0)
    assert float(normalize(soil_host, 1000, 3000, 2000)["norm"]) == pytest.approx(0.5)
    assert float(normalize(soil_host, 1000, 3000, 3000)["norm"]) == pytest.approx(1.0)


def test_midpoint_is_half_in_both_directions(soil_host):
    assert float(normalize(soil_host, 3000, 1000, 2000)["norm"]) == pytest.approx(0.5)
    assert float(normalize(soil_host, 1000, 3000, 2000)["norm"]) == pytest.approx(0.5)


# ---------------------------------------------------------------------- #
# clamping, and what the unclamped value preserves
# ---------------------------------------------------------------------- #
def test_out_of_range_readings_are_clamped(soil_host):
    """A reading past an endpoint clamps to 0 / 1, so the index stays a 0-1
    index rather than running off to meaningless values."""
    above_dry = normalize(soil_host, 3000, 1000, 3500)
    assert float(above_dry["norm"]) == 0.0
    assert float(above_dry["unclamped"]) < 0.0, "the overshoot must stay visible"

    below_wet = normalize(soil_host, 3000, 1000, 500)
    assert float(below_wet["norm"]) == 1.0
    assert float(below_wet["unclamped"]) > 1.0


def test_unclamped_is_available_in_range_too(soil_host):
    row = normalize(soil_host, 3000, 1000, 2000)
    assert float(row["unclamped"]) == pytest.approx(0.5)


def test_normalization_is_monotonic_in_raw(soil_host):
    """Whichever direction the module moves, the index must not double back.

    With dry > wet the index *falls* as the raw count rises, so the sequence is
    checked against the direction the calibration implies rather than against
    plain ascending order.
    """
    dry, wet = 3200, 1200
    values = [float(normalize(soil_host, dry, wet, raw)["norm"])
              for raw in range(wet, dry + 1, 100)]
    assert values == sorted(values, reverse=True)

    dry, wet = 1200, 3200
    values = [float(normalize(soil_host, dry, wet, raw)["norm"])
              for raw in range(dry, wet + 1, 100)]
    assert values == sorted(values)


# ---------------------------------------------------------------------- #
# invalid calibrations
# ---------------------------------------------------------------------- #
def test_equal_endpoints_are_rejected(soil_host):
    """dry == wet would divide by zero; the normalisation must refuse, not
    produce garbage."""
    row = normalize(soil_host, 2000, 2000, 2000)
    assert int(row["rc"]) == -1
    assert int(row["calib_valid"]) == 0


def test_endpoints_outside_the_adc_range_are_rejected(soil_host):
    assert kv(run(soil_host, "calib", "-10", "3000"))["valid"] == "0"
    assert kv(run(soil_host, "calib", "0", "4096"))["valid"] == "0"
    assert kv(run(soil_host, "calib", "0", "4095"))["valid"] == "1"


def test_endpoints_too_close_are_rejected(soil_host):
    """A near-zero span amplifies noise: one count of jitter would be a visible
    part of the index."""
    assert kv(run(soil_host, "calib", "2000", "2001"))["valid"] == "0"
    assert kv(run(soil_host, "calib", "2001", "2000"))["valid"] == "0"


@pytest.mark.parametrize(
    ("dry", "wet", "raw"),
    [
        (2000, 2001, 2000),  # span too narrow
        (2001, 2000, 2000),  # narrow span in the other direction
        (-10, 3000, 2000),  # endpoint below ADC range
        (1000, 4096, 2000),  # endpoint above ADC range
    ],
)
def test_invalid_calibrations_do_not_normalize(soil_host, dry, wet, raw):
    row = normalize(soil_host, dry, wet, raw)
    assert int(row["calib_valid"]) == 0
    assert int(row["rc"]) == -1
    assert float(row["norm"]) == -1.0
    assert float(row["unclamped"]) == -1.0


def test_a_wide_enough_calibration_is_accepted_in_both_directions(soil_host):
    assert kv(run(soil_host, "calib", "3000", "1000"))["valid"] == "1"
    assert kv(run(soil_host, "calib", "1000", "3000"))["valid"] == "1"


# ---------------------------------------------------------------------- #
# batch statistics
# ---------------------------------------------------------------------- #
def test_batch_stats_of_identical_samples(soil_host):
    row = kv(run(soil_host, "batch", ",".join(["2048"] * 16)))
    assert int(row["n"]) == 16
    assert int(row["min"]) == 2048 == int(row["max"])
    assert float(row["mean"]) == pytest.approx(2048.0)
    assert float(row["std"]) == 0.0
    assert int(row["median"]) == 2048


def test_batch_stats_with_one_outlier(soil_host):
    """The reason a trimmed mean or median exists: 15 calm samples and one
    wild one. The mean moves; the median and the trimmed mean barely do."""
    samples = [2048] * 15 + [3048]
    row = kv(run(soil_host, "batch", ",".join(str(s) for s in samples)))
    assert int(row["n"]) == 16
    assert int(row["min"]) == 2048
    assert int(row["max"]) == 3048
    assert float(row["mean"]) == pytest.approx(2110.5, abs=1e-3)
    assert int(row["median"]) == 2048, "the median must ignore the outlier"
    assert float(row["trimmed"]) == pytest.approx(2048.0, abs=1.0), (
        "the trimmed mean must also ignore it"
    )


def test_batch_stats_of_a_known_spread(soil_host):
    samples = list(range(1, 17))  # 1..16
    row = kv(run(soil_host, "batch", ",".join(str(s) for s in samples)))
    assert float(row["mean"]) == pytest.approx(8.5)
    # sample standard deviation over n - 1: sum of squared deviations is
    # n(n^2 - 1)/12 = 340 for 1..16, divided by 15.
    assert float(row["std"]) == pytest.approx(math.sqrt(340.0 / 15.0), abs=1e-3)
    assert int(row["median"]) == 8 or int(row["median"]) == 9
    assert int(row["median"]) == (8 + 9) // 2


def test_a_single_sample_batch_has_zero_std(soil_host):
    row = kv(run(soil_host, "batch", "1234"))
    assert int(row["n"]) == 1
    assert int(row["min"]) == 1234 == int(row["max"])
    assert float(row["std"]) == 0.0
