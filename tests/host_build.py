"""Shared helper for the host-side C tests.

Several test modules compile firmware sources for the host:

    tests/test_parity_python_c.py      the policy (change_detector, adaptive_scheduler)
    tests/test_bme280_math.py          the BME280 maths
    tests/test_communication_payload.py  the MQTT payload builder
    tests/test_sensor_contract.py      the sensor bring-up contract
    tests/test_sensor_supervisor.py    the sensor retry policy

They all need the same three things: find a C compiler, build with the project's
warning flags, and run the binary. That lives here so a change to the build flags
cannot be applied to one test and forgotten in another.

A missing compiler is reported as a failure rather than a silent skip: the
"Python and firmware agree" claim is only meaningful if the test actually ran.
`AS_SKIP_PARITY=1` is the explicit opt-out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_MAIN = ROOT / "firmware" / "main"
HOST_DIR = ROOT / "tests" / "c_host"
SHIM_DIR = HOST_DIR / "shims"

CFLAGS = ("-std=c11", "-Wall", "-Wextra", "-Werror", "-O2")


def find_c_compiler() -> str | None:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def require_c_compiler() -> str:
    """Return a compiler path, or fail with an actionable message."""
    compiler = find_c_compiler()
    if compiler is None:
        if os.environ.get("AS_SKIP_PARITY") == "1":
            pytest.skip("no C compiler and AS_SKIP_PARITY=1")
        pytest.fail(
            "No C compiler (cc/gcc/clang) found, so the firmware sources cannot "
            "be compiled for the host and the claims that depend on them are "
            "unverified. Install a compiler, or set AS_SKIP_PARITY=1 to opt out "
            "explicitly."
        )
    return compiler


def compile_host_binary(
    out_path: Path,
    *sources: Path | str,
    defines: Sequence[str] = (),
    use_shims: bool = False,
    link_math: bool = True,
) -> Path:
    """Compile `sources` into `out_path` with the project's flags.

    `use_shims` prepends `tests/c_host/shims` to the include path, which lets a
    source that includes an ESP-IDF header (for example `esp_log.h`) build on a
    workstation. Only the mock/no-hardware paths are supported that way.
    """
    compiler = require_c_compiler()

    cmd: List[str] = [compiler, *CFLAGS]
    for define in defines:
        cmd.append(f"-D{define}")
    if use_shims:
        cmd += ["-I", str(SHIM_DIR)]
    cmd += ["-I", str(FIRMWARE_MAIN), "-o", str(out_path)]
    cmd += [str(s) for s in sources]
    if link_math:
        cmd.append("-lm")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            "host build failed:\n"
            + " ".join(cmd)
            + "\n"
            + result.stdout
            + result.stderr
        )
    return out_path


def run(binary: Path, *args: object) -> str:
    """Run a host binary and return stdout, asserting it exited cleanly."""
    result = subprocess.run(
        [str(binary), *[str(a) for a in args]], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{binary.name} exited {result.returncode}\n{result.stdout}{result.stderr}"
        )
    return result.stdout


def parse_key_values(output: str) -> dict:
    """Parse `key=value` pairs (space or newline separated) into a dict."""
    parsed = {}
    for token in output.replace("\n", " ").split():
        if "=" in token:
            key, value = token.split("=", 1)
            parsed[key] = value
    return parsed


__all__ = [
    "ROOT",
    "FIRMWARE_MAIN",
    "HOST_DIR",
    "SHIM_DIR",
    "find_c_compiler",
    "require_c_compiler",
    "compile_host_binary",
    "run",
    "parse_key_values",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    print("compiler:", find_c_compiler() or "<none>")
    sys.exit(0)
