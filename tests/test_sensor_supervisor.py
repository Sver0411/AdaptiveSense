"""Sensor bring-up retry policy (`firmware/main/sensor_supervisor.c`).

The policy replaces "init once, then fail forever": while the sensor has never
come up, re-initialisation is allowed but rate-limited to one attempt per
`retry_interval_s`, so a board whose BME280 is reconnected later recovers on its
own without the main loop spinning.

The policy lives in a pure-C unit precisely so it can be tested like this.
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
    run,
)

SUPERVISOR_C = FIRMWARE_MAIN / "sensor_supervisor.c"
SENSOR_C = FIRMWARE_MAIN / "sensor.c"
BME280_C = FIRMWARE_MAIN / "bme280_math.c"
SENSOR_HOST = HOST_DIR / "sensor_host_main.c"


@pytest.fixture(scope="module")
def sensor_host(tmp_path_factory) -> Path:
    """Build the host driver.

    `sensor.c` is linked as well even though this module only exercises the
    supervisor: the shared driver's other subcommands reference the sensor API, so
    the translation unit has to resolve. It is compiled in mock mode with the test
    shim, which is cheap.
    """
    out = tmp_path_factory.mktemp("supervisor") / "sensor_host"
    return compile_host_binary(
        out,
        SENSOR_HOST,
        SENSOR_C,
        SUPERVISOR_C,
        BME280_C,
        defines=("CONFIG_AS_USE_MOCK_SENSOR=1",),
        use_shims=True,
    )


def trace(sensor_host: Path, retry_interval_s: float, probes) -> tuple[list[dict], dict]:
    """Return (probe rows, totals).

    A probe row reports the counters *before* the attempt it may go on to make, so
    the totals come from the driver's trailing `final,` line.
    """
    output = run(sensor_host, "supervisor", retry_interval_s,
                 ",".join(str(p) for p in probes))
    rows = []
    totals = {}
    for line in output.strip().splitlines():
        parts = line.split(",")
        if parts[0] == "probe":
            rows.append({
                "t": float(parts[1]),
                "should": int(parts[2]),
                "ready": int(parts[3]),
                "attempts": int(parts[4]),
                "failures": int(parts[5]),
            })
        elif parts[0] == "final":
            totals = {"attempts": int(parts[1]), "failures": int(parts[2])}
    assert rows, output
    return rows, totals


def test_first_attempt_is_allowed_immediately(sensor_host):
    rows, _ = trace(sensor_host, 5.0, [0])
    assert rows[0]["t"] == 0.0
    assert rows[0]["should"] == 1, "a node with no sensor should try at once"
    assert rows[0]["ready"] == 0


def test_retry_is_rate_limited(sensor_host):
    """After a failed attempt, no further attempt before the retry interval."""
    rows, _ = trace(sensor_host, 5.0, [0, 1.0, 4.9, 5.0, 9.9, 10.0])
    by_time = {r["t"]: r for r in rows}

    # t=0 is the first attempt; the probe line at t=0 after the attempt is the
    # second entry, so use explicit times from 1.0 onwards.
    assert by_time[1.0]["should"] == 0
    assert by_time[4.9]["should"] == 0
    assert by_time[5.0]["should"] == 1, "the retry interval has elapsed"
    assert by_time[9.9]["should"] == 0
    assert by_time[10.0]["should"] == 1


def test_attempts_are_counted(sensor_host):
    _, totals = trace(sensor_host, 5.0, [0, 1.0, 5.0, 10.0, 20.0])
    # one attempt at t=0 plus one at each of 5.0, 10.0, 20.0
    assert totals["attempts"] == 4
    assert totals["failures"] == 4


def test_never_busy_loops(sensor_host):
    """A hundred probes inside one retry interval must not trigger re-inits."""
    probes = [round(0.01 * i, 2) for i in range(1, 100)]
    _, totals = trace(sensor_host, 5.0, probes)
    assert totals["attempts"] == 1, (
        f"{totals['attempts']} attempts inside a single retry interval"
    )


def test_zero_retry_interval_still_counts_attempts(sensor_host):
    rows, totals = trace(sensor_host, 0.0, [0, 0, 0])
    # With no spacing every probe is allowed - but the counter makes the
    # behaviour observable rather than silent.
    assert totals["attempts"] >= 1
    assert rows[-1]["ready"] == 0


def test_state_name_reports_unavailable(sensor_host):
    output = run(sensor_host, "supervisor", 5.0, "1")
    assert "unavailable" in output


# ---------------------------------------------------------------------- #
# runtime failure and recovery
#
# The defect this pins down, found on hardware: once bring-up had succeeded, the
# supervisor's `initialized` flag was never cleared, so a sensor unplugged while
# the node ran produced "no usable reading ... (sensor ready)" forever — the state
# claimed ready while every read failed — and `sensor_init()` was never called
# again. Recovery happened only by luck, because the driver's device handle was
# still registered and the part reappeared.
# ---------------------------------------------------------------------- #
def recovery_trace(sensor_host, *, retry, threshold, failures, reinit_t):
    """Run the recovery scenario and return (steps by label, totals)."""
    output = run(sensor_host, "recovery", retry, threshold, failures, reinit_t)
    steps = {}
    totals = {}
    for line in output.strip().splitlines():
        parts = line.split(",")
        if parts[0] == "step":
            steps[parts[1]] = {
                "ready": int(parts[2]),
                "should": int(parts[3]),
                "state": parts[4],
                "consecutive": int(parts[5]),
                "events": int(parts[6]),
            }
        elif parts[0] == "totals":
            totals = {
                "init_attempts": int(parts[1]),
                "init_failures": int(parts[2]),
                "read_failures": int(parts[3]),
                "unavailability_events": int(parts[4]),
                "read_successes": int(parts[5]),
            }
    assert steps and totals, output
    return steps, totals


def test_a_live_sensor_is_ready(sensor_host):
    steps, _ = recovery_trace(sensor_host, retry=5, threshold=3, failures=5, reinit_t=10)
    assert steps["after-init"]["ready"] == 1
    assert steps["after-init"]["state"] == "ready"
    assert steps["after-init"]["should"] == 0


def test_isolated_read_failures_are_tolerated(sensor_host):
    """One bad transfer must not tear down a working driver."""
    steps, _ = recovery_trace(sensor_host, retry=5, threshold=3, failures=5, reinit_t=10)
    assert steps["fail-1"]["ready"] == 1
    assert steps["fail-2"]["ready"] == 1
    assert steps["fail-2"]["events"] == 0


def test_the_state_does_not_claim_ready_while_reads_are_failing(sensor_host):
    """The specific reporting defect: "ready" while nothing was being read."""
    steps, _ = recovery_trace(sensor_host, retry=5, threshold=3, failures=5, reinit_t=10)
    assert steps["fail-1"]["state"] == "ready (reads failing)"
    assert steps["fail-1"]["state"] != "ready"


def test_enough_consecutive_failures_mark_the_sensor_unavailable(sensor_host):
    steps, totals = recovery_trace(sensor_host, retry=5, threshold=3, failures=5, reinit_t=10)
    assert steps["fail-2"]["ready"] == 1, "not yet at the threshold"
    assert steps["fail-3"]["ready"] == 0, "the threshold is where it drops out"
    assert steps["fail-3"]["state"] == "unavailable (will retry)"
    assert steps["fail-3"]["events"] == 1
    assert totals["unavailability_events"] == 1
    assert totals["read_failures"] == 5


def test_bring_up_is_re_attempted_after_going_unavailable(sensor_host):
    """The specific behavioural defect: `sensor_init()` must be reachable again.

    Before the fix this was always false, so no re-probe could ever happen.
    """
    steps, _ = recovery_trace(sensor_host, retry=5, threshold=3, failures=3, reinit_t=10)
    assert steps["fail-3"]["should"] == 1
    assert steps["before-reprobe"]["should"] == 1


def test_a_failed_re_init_is_rate_limited(sensor_host):
    steps, totals = recovery_trace(sensor_host, retry=5, threshold=3, failures=3, reinit_t=10)
    assert steps["after-failed-init"]["should"] == 0, "must not retry immediately"
    assert steps["inside-retry-window"]["should"] == 0, "nor inside the interval"
    assert totals["init_failures"] == 1


def test_a_good_re_init_restores_the_sensor(sensor_host):
    steps, totals = recovery_trace(sensor_host, retry=5, threshold=3, failures=5, reinit_t=10)
    assert steps["after-good-init"]["ready"] == 1
    assert steps["after-good-init"]["state"] == "ready"
    assert steps["after-good-init"]["consecutive"] == 0, "a fresh init means a fresh device"
    assert steps["after-good-read"]["ready"] == 1
    assert totals["init_attempts"] == 3
    assert totals["read_successes"] == 1


def test_threshold_zero_reproduces_the_old_behaviour(sensor_host):
    """The setting that reproduces the defect, so the difference is demonstrable.

    With the threshold at 0 the sensor never drops out: the state stays "ready
    (reads failing)" however many reads fail, and bring-up is never re-attempted.
    That is exactly what was observed on hardware before the fix.
    """
    steps, totals = recovery_trace(sensor_host, retry=5, threshold=0, failures=20, reinit_t=10)
    assert steps["fail-20"]["ready"] == 1, "never drops out"
    assert steps["fail-20"]["should"] == 0, "so bring-up is never reachable again"
    assert steps["fail-20"]["events"] == 0
    assert totals["unavailability_events"] == 0
    assert totals["read_failures"] == 20


# ---------------------------------------------------------------------- #
# the counters must only move for a read that was actually attempted
#
# The defect this pins down, found by review of main.c: one condition combined
# readiness with the read result,
#
#     if (!sensor_sup_ready(&s) || sensor_read(&r) != 0) {
#         sensor_sup_note_read_failure(&s, t);
#
# so with short-circuit evaluation "the sensor is unavailable, therefore no read
# was attempted" was recorded as "a read failed". Every cycle spent waiting for a
# re-probe moved the counters again: a hardware run reported 59 read failures
# where 3 reads had actually been attempted.
#
# The host driver's `cycles` subcommand replays main.c's order (bring-up, then the
# three-way read) so these assertions are made against the loop's behaviour rather
# than against a description of it.
# ---------------------------------------------------------------------- #
def cycles_trace(sensor_host, *, retry=5, threshold=3, n_cycles=20,
                 unplug_at=1, plug_back_at=12):
    """Replay the sampling loop and return (rows by cycle number, totals)."""
    output = run(sensor_host, "cycles", str(retry), str(threshold),
                 str(n_cycles), str(unplug_at), str(plug_back_at))
    rows = {}
    totals = {}
    for line in output.strip().splitlines():
        parts = line.split(",")
        if parts[0] == "cycle":
            rows[int(parts[1])] = {
                "t": float(parts[2]),
                "init": parts[3],           # - | ok | fail
                "read": parts[4],           # - | ok | fail
                "total": int(parts[5]),
                "consecutive": int(parts[6]),
                "ready": int(parts[7]),
            }
        elif parts[0] == "totals":
            totals = {
                "init_attempts": int(parts[1]),
                "init_failures": int(parts[2]),
                "read_failures": int(parts[3]),
                "read_successes": int(parts[4]),
                "unavailability_events": int(parts[5]),
            }
    assert rows and totals, output
    return rows, totals


def test_a_healthy_sensor_reads_without_any_failure(sensor_host):
    rows, totals = cycles_trace(sensor_host, n_cycles=5,
                                unplug_at=999, plug_back_at=999)
    assert all(r["read"] == "ok" for r in rows.values())
    assert all(r["total"] == 0 for r in rows.values())
    assert totals["read_failures"] == 0
    assert totals["read_successes"] == 5


def test_only_an_attempted_read_counts_as_a_failure(sensor_host):
    """The invariant the defect violated: the counter equals the number of reads
    that were actually tried and failed - not the number of cycles that passed."""
    rows, totals = cycles_trace(sensor_host)
    attempted_failures = sum(1 for r in rows.values() if r["read"] == "fail")
    assert totals["read_failures"] == attempted_failures
    assert attempted_failures == 3, "one per cycle the sensor was reachable and dead"
    assert totals["read_failures"] < len(rows), (
        "the counter grew past the number of reads that were attempted, which is "
        "what the short-circuit defect produced"
    )


def test_the_threshold_ends_the_failures(sensor_host):
    rows, totals = cycles_trace(sensor_host)
    assert rows[1]["consecutive"] == 1
    assert rows[2]["consecutive"] == 2
    assert rows[3]["consecutive"] == 3
    assert rows[3]["ready"] == 0, "the third failure is the one that drops it out"
    assert totals["unavailability_events"] == 1


def test_waiting_for_a_re_probe_does_not_move_the_counters(sensor_host):
    """The specific regression: cycles with no read must leave the counters alone."""
    rows, totals = cycles_trace(sensor_host)
    waiting = {i: r for i, r in rows.items()
               if r["ready"] == 0 and r["read"] == "-" and i < 14}
    assert len(waiting) >= 8, waiting
    frozen_total = {r["total"] for r in waiting.values()}
    frozen_consec = {r["consecutive"] for r in waiting.values()}
    assert frozen_total == {3}, f"read_failures_total moved while waiting: {frozen_total}"
    assert frozen_consec == {3}, (
        f"read_failures_consecutive moved while waiting: {frozen_consec}"
    )
    assert totals["read_failures"] == 3


def test_no_read_is_attempted_while_the_sensor_is_unavailable(sensor_host):
    """Reads stop at the drop-out and resume only after a successful bring-up.

    The cycle that *causes* the drop-out is not a violation: the sensor was still
    considered usable when that cycle began, so the read was legitimate and it is
    the failure that changed the state. What must not happen is a read in a cycle
    that began with the sensor already declared unavailable - and the one way back
    is a bring-up that succeeds, which is exactly main.c's order.
    """
    rows, _ = cycles_trace(sensor_host)
    recovery = min(i for i, r in rows.items() if r["init"] == "ok")
    attempted = [i for i, r in rows.items() if r["read"] != "-"]
    assert attempted == [1, 2, 3] + list(range(recovery, len(rows) + 1)), attempted

    down = False
    for i, r in sorted(rows.items()):
        if down and r["init"] != "ok":
            assert r["read"] == "-", f"cycle {i} read a sensor already known to be down"
        down = (r["ready"] == 0)


def test_re_probes_are_attempted_while_unavailable_and_rate_limited(sensor_host):
    rows, totals = cycles_trace(sensor_host)
    assert rows[4]["init"] == "fail", "bring-up must be reachable again"
    assert rows[5]["init"] == "-", "and rate-limited, not spun"
    assert rows[9]["init"] == "fail", "the next attempt comes one interval later"
    assert totals["init_failures"] == 2
    assert totals["init_attempts"] == 4, "the initial success plus three re-probes"


def test_recovery_clears_the_streak_and_reads_resume(sensor_host):
    rows, _ = cycles_trace(sensor_host)
    assert rows[14]["init"] == "ok", "the part came back"
    assert rows[14]["ready"] == 1
    assert rows[14]["consecutive"] == 0, "a fresh bring-up means a fresh device"
    assert rows[14]["read"] == "ok"
    assert all(rows[i]["read"] == "ok" for i in range(14, 21))
    # The historical total is kept: it is a record of the outage, not a claim
    # about the present, so it is not reset by recovery.
    assert rows[20]["total"] == 3


def test_the_total_records_the_outage_but_the_streak_recovers(sensor_host):
    """The two counters mean different things, and the difference is deliberate.

    `read_failures_total` is a record of what happened, so recovery does not erase
    it; `read_failures_consecutive` describes the present, so it goes back to zero.
    A build that reset the total would hide the outage from the log;
    """
    rows, totals = cycles_trace(sensor_host)
    assert rows[1]["total"] == 1, "it starts with the first read that was tried"
    assert rows[20]["total"] == 3 == totals["read_failures"]
    assert rows[20]["consecutive"] == 0
    assert rows[20]["ready"] == 1
