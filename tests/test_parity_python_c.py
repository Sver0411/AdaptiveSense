"""Python/C policy parity test.

`simulator/adaptive.py` and `firmware/main/{change_detector,adaptive_scheduler}.c`
are two implementations of the same specification
(`docs/change_score_spec.md`). This test compiles the C policy for the host and
runs both implementations over `tests/fixtures/parity_trace.csv`; the two must
produce the same state, interval, event flag and upload decision for every
sample.

The C build links `policy_config.c`, which reads `config.example.h` — the same
configuration the device is built from. So this test also verifies that the
firmware and the simulator are configured identically, not just coded similarly.

v0.1 had no such test, and the three run-times silently used three different
definitions of the rate-of-change score.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulator.adaptive import AdaptiveScheduler  # noqa: E402
from simulator.config import load_config  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "parity_trace.csv"
HARNESS = ROOT / "tests" / "c_host" / "parity_main.c"
C_SOURCES = [
    ROOT / "firmware" / "main" / "change_detector.c",
    ROOT / "firmware" / "main" / "adaptive_scheduler.c",
    ROOT / "firmware" / "main" / "policy_config.c",
]
INCLUDE_DIR = ROOT / "firmware" / "main"

SCORE_REL_TOL = 1e-3   # float on the device vs double on the host
INTERVAL_TOL = 1e-3


def _find_compiler() -> str | None:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def load_trace() -> List[Dict[str, float]]:
    with open(FIXTURE, newline="", encoding="utf-8") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    return [
        {
            "timestamp": float(r["timestamp"]),
            "temperature": float(r["temperature"]),
            "humidity": float(r["humidity"]),
            "pressure": float(r["pressure"]),
            "light": float(r["light"]),
        }
        for r in rows
    ]


def run_python_policy(trace: List[Dict[str, float]], cfg: dict) -> List[Dict[str, object]]:
    """Mirror the replay loop in simulator/replay.py (and parity_main.c)."""
    scheduler = AdaptiveScheduler(cfg, time=trace[0]["timestamp"])
    next_sample = trace[0]["timestamp"]
    out: List[Dict[str, object]] = []
    for row in trace:
        if row["timestamp"] < next_sample - 1e-9:
            continue
        values = {k: v for k, v in row.items() if k != "timestamp"}
        decision = scheduler.update(row["timestamp"], values)
        out.append(
            {
                "timestamp": decision.timestamp,
                "state": decision.state,
                "interval_s": decision.interval_s,
                "score": decision.score,
                "event": int(decision.detected_event),
                "upload_requested": int(decision.upload_requested),
            }
        )
        next_sample = decision.timestamp + decision.interval_s
    return out


def run_c_policy(tmp_path: Path) -> List[Dict[str, object]]:
    compiler = _find_compiler()
    if compiler is None:
        if os.environ.get("AS_SKIP_PARITY") == "1":
            pytest.skip("no C compiler and AS_SKIP_PARITY=1")
        pytest.fail(
            "No C compiler (cc/gcc/clang) found, so the Python/C parity claim "
            "cannot be verified. Install a compiler, or set AS_SKIP_PARITY=1 to "
            "opt out explicitly."
        )

    binary = tmp_path / "parity_policy"
    out_csv = tmp_path / "parity_c.csv"
    cmd = [
        compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
        "-I", str(INCLUDE_DIR),
        "-o", str(binary),
        str(HARNESS),
        *[str(p) for p in C_SOURCES],
        "-lm",
    ]
    build = subprocess.run(cmd, capture_output=True, text=True)
    assert build.returncode == 0, f"C policy build failed:\n{build.stderr}"

    run = subprocess.run([str(binary), str(FIXTURE), str(out_csv)],
                         capture_output=True, text=True)
    assert run.returncode == 0, f"C policy run failed:\n{run.stderr}"

    with open(out_csv, newline="", encoding="utf-8") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    return [
        {
            "timestamp": float(r["timestamp"]),
            "state": r["state"],
            "interval_s": float(r["interval_s"]),
            "score": float(r["score"]),
            "event": int(r["event"]),
            "upload_requested": int(r["upload_requested"]),
        }
        for r in rows
    ]


@pytest.fixture(scope="module")
def parity_results(tmp_path_factory):
    cfg = load_config()
    trace = load_trace()
    py = run_python_policy(trace, cfg)
    c = run_c_policy(tmp_path_factory.mktemp("parity"))
    return py, c


def test_fixture_is_reproducible():
    """The committed trace must match what the generator produces."""
    generator = ROOT / "tests" / "make_parity_fixture.py"
    before = FIXTURE.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(generator)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    after = FIXTURE.read_text(encoding="utf-8")
    assert before == after, (
        "tests/fixtures/parity_trace.csv is out of date: re-run "
        "tests/make_parity_fixture.py"
    )


def test_sample_count_matches(parity_results):
    py, c = parity_results
    assert len(py) == len(c), (
        f"Python emitted {len(py)} samples, C emitted {len(c)}"
    )


def test_states_match(parity_results):
    py, c = parity_results
    mismatches = [
        (p["timestamp"], p["state"], k["state"])
        for p, k in zip(py, c)
        if p["state"] != k["state"]
    ]
    assert not mismatches, f"state mismatches (t, python, c): {mismatches[:10]}"


def test_event_flags_match(parity_results):
    py, c = parity_results
    mismatches = [
        (p["timestamp"], p["event"], k["event"])
        for p, k in zip(py, c)
        if p["event"] != k["event"]
    ]
    assert not mismatches, f"event mismatches (t, python, c): {mismatches[:10]}"


def test_upload_decisions_match(parity_results):
    py, c = parity_results
    mismatches = [
        (p["timestamp"], p["upload_requested"], k["upload_requested"])
        for p, k in zip(py, c)
        if p["upload_requested"] != k["upload_requested"]
    ]
    assert not mismatches, f"upload mismatches (t, python, c): {mismatches[:10]}"


def test_intervals_match(parity_results):
    py, c = parity_results
    mismatches = [
        (p["timestamp"], p["interval_s"], k["interval_s"])
        for p, k in zip(py, c)
        if abs(p["interval_s"] - k["interval_s"]) > INTERVAL_TOL
    ]
    assert not mismatches, f"interval mismatches (t, python, c): {mismatches[:10]}"


def test_scores_match(parity_results):
    py, c = parity_results
    worst = 0.0
    worst_at = None
    for p, k in zip(py, c):
        denom = max(1e-9, abs(p["score"]))
        rel = abs(p["score"] - k["score"]) / denom
        if rel > worst:
            worst, worst_at = rel, p["timestamp"]
    assert worst < SCORE_REL_TOL, (
        f"max relative score difference {worst:.3e} at t={worst_at} "
        f"(tolerance {SCORE_REL_TOL})"
    )


def test_trace_exercises_all_states(parity_results):
    """Guard against a fixture that stops discriminating (e.g. all STABLE)."""
    py, _ = parity_results
    states = {p["state"] for p in py}
    assert states == {"STABLE", "ACTIVE", "ALERT"}, (
        f"parity fixture only reached {sorted(states)}; it must cover all three "
        f"states or the parity test proves little"
    )


def test_first_sample_is_uploaded_by_both(parity_results):
    py, c = parity_results
    assert py[0]["upload_requested"] == 1
    assert c[0]["upload_requested"] == 1
