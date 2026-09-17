/*
 * sensor_host_main.c — host driver for the sensor bring-up contract.
 *
 * Two firmware units are exercised here, both of which are pure C or compile
 * cleanly in mock mode:
 *
 *   firmware/main/sensor.c             (CONFIG_AS_USE_MOCK_SENSOR=1)
 *   firmware/main/sensor_supervisor.c  (always pure C)
 *
 * `tests/test_sensor_contract.py` and `tests/test_sensor_supervisor.py` use it.
 *
 * Subcommands
 * -----------
 *   sensor-before-init
 *       Fill a sensor_read_t with a sentinel pattern, call sensor_read() without
 *       ever initialising, and report whether the call refused and whether the
 *       buffer was left untouched. This is the guarantee that a node whose sensor
 *       never came up cannot emit a reading of zeros that the change detector
 *       would treat as a measurement.
 *
 *   sensor-after-init
 *       sensor_init() then sensor_read(), reporting the values and validity. In
 *       mock mode the values are the documented deterministic sequence.
 *
 *   cycles <retry_s> <threshold> <n_cycles> <unplug_at> <plug_back_at>
 *       Replay main.c's sampling loop (bring-up, then the three-way read) cycle by
 *       cycle, and print the counters it produced. This is what pins the rule that
 *       the counters only move for a read that was actually attempted.
 *
 *   supervisor <retry_interval_s> <t1,t2,t3,...>
 *       Drive sensor_supervisor_t: the first attempt is made at t=0 (and fails),
 *       then each probe time is queried and, when the policy allows an attempt, a
 *       further failure is noted. Prints one CSV line per probe.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sensor.h"
#include "sensor_supervisor.h"

/* Sentinel values used to detect whether sensor_read() touched the buffer. */
#define SENTINEL_VALUE 12345.0f

static int cmd_sensor_before_init(void)
{
    sensor_read_t reading;
    for (int i = 0; i < SEN_CH_COUNT; i++) {
        reading.value[i] = SENTINEL_VALUE;
        reading.valid[i] = false;
    }

    const int rc = sensor_read(&reading);

    int untouched = 1;
    for (int i = 0; i < SEN_CH_COUNT; i++) {
        if (reading.value[i] != SENTINEL_VALUE) {
            untouched = 0;
        }
    }

    printf("read_rc=%d is_init=%d untouched=%d\n", rc,
           (int)sensor_is_initialized(), untouched);
    return 0;
}

static int cmd_sensor_after_init(void)
{
    const int init_rc = sensor_init();
    printf("init_rc=%d is_init=%d\n", init_rc, (int)sensor_is_initialized());
    if (init_rc != 0) {
        return 0;
    }

    sensor_read_t reading;
    const int rc = sensor_read(&reading);
    printf("read_rc=%d valid=%d%d%d%d temp=%.4f hum=%.4f press=%.4f light=%.4f\n",
           rc, (int)reading.valid[0], (int)reading.valid[1],
           (int)reading.valid[2], (int)reading.valid[3],
           (double)reading.value[SEN_CH_TEMPERATURE],
           (double)reading.value[SEN_CH_HUMIDITY],
           (double)reading.value[SEN_CH_PRESSURE],
           (double)reading.value[SEN_CH_LIGHT]);
    return 0;
}

static int cmd_supervisor(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr, "usage: supervisor <retry_interval_s> <t1,t2,...>\n");
        return 2;
    }
    const double retry_interval = strtod(argv[2], NULL);

    sensor_supervisor_t sup;
    sensor_sup_init(&sup, retry_interval, 0, 0.0); /* 0 = never drop out at runtime */
    printf("probe,0.000,%d,%d,%u,%u\n", (int)sensor_sup_should_attempt(&sup, 0.0),
           (int)sensor_sup_ready(&sup), sup.init_attempts, sup.init_failures);
    /* First attempt, which fails. */
    sensor_sup_note_attempt(&sup, 0.0, false);

    /* strtok() mutates its input, so work on a copy. Built with malloc + memcpy
     * rather than strdup: strdup is POSIX, not ISO C, and glibc hides it under
     * -std=c11, which is what the host tests compile with. Found by CI. */
    const size_t times_len = strlen(argv[3]) + 1;
    char *times = malloc(times_len);
    if (times == NULL) {
        fprintf(stderr, "allocation failed\n");
        return 2;
    }
    memcpy(times, argv[3], times_len);
    for (char *tok = strtok(times, ","); tok != NULL; tok = strtok(NULL, ",")) {
        const double t = strtod(tok, NULL);
        const int should = (int)sensor_sup_should_attempt(&sup, t);
        printf("probe,%.3f,%d,%d,%u,%u\n", t, should, (int)sensor_sup_ready(&sup),
               sup.init_attempts, sup.init_failures);
        if (should) {
            sensor_sup_note_attempt(&sup, t, false);
        }
    }
    free(times);

    printf("state,%s\n", sensor_sup_state_name(&sup));
    printf("final,%u,%u\n", sup.init_attempts, sup.init_failures);
    return 0;
}

/*
 * Drive the runtime-failure and recovery path.
 *
 *   recovery <retry_interval_s> <failures_before_unavailable> <n_read_failures> <reinit_time>
 *
 * Prints one CSV line per step:
 *   step,<label>,<ready>,<should_attempt>,<state>,<consecutive_failures>,<unavailability_events>
 */
static void probe_step(const sensor_supervisor_t *s, const char *label, double now)
{
    printf("step,%s,%d,%d,%s,%u,%u\n", label,
           (int)sensor_sup_ready(s), (int)sensor_sup_should_attempt(s, now),
           sensor_sup_state_name(s), s->read_failures_consecutive,
           s->unavailability_events);
}

static int cmd_recovery(int argc, char **argv)
{
    if (argc < 6) {
        fprintf(stderr, "usage: recovery <retry_s> <threshold> <n_failures> <reinit_t>\n");
        return 2;
    }
    const double retry = strtod(argv[2], NULL);
    const unsigned threshold = (unsigned)strtoul(argv[3], NULL, 10);
    const unsigned n_failures = (unsigned)strtoul(argv[4], NULL, 10);
    const double reinit_t = strtod(argv[5], NULL);

    sensor_supervisor_t sup;
    sensor_sup_init(&sup, retry, threshold, 0.0);
    probe_step(&sup, "start", 0.0);

    sensor_sup_note_attempt(&sup, 0.0, true); /* first bring-up succeeds */
    probe_step(&sup, "after-init", 0.0);

    /* Reads now start failing, one per second. */
    for (unsigned i = 1; i <= n_failures; i++) {
        const double t = (double)i;
        sensor_sup_note_read_failure(&sup, t);
        char label[32];
        snprintf(label, sizeof(label), "fail-%u", i);
        probe_step(&sup, label, t);
    }

    probe_step(&sup, "before-reprobe", reinit_t);

    /* Re-probe: first a failed attempt, to show the rate limit, then a success. */
    sensor_sup_note_attempt(&sup, reinit_t, false);
    probe_step(&sup, "after-failed-init", reinit_t);

    const double blocked_t = reinit_t + retry - 0.1;
    probe_step(&sup, "inside-retry-window", blocked_t);

    const double ok_t = reinit_t + retry;
    sensor_sup_note_attempt(&sup, ok_t, true);
    probe_step(&sup, "after-good-init", ok_t);

    sensor_sup_note_read_success(&sup);
    probe_step(&sup, "after-good-read", ok_t);

    printf("totals,%u,%u,%u,%u,%u\n", sup.init_attempts, sup.init_failures,
           sup.read_failures_total, sup.unavailability_events, sup.read_successes);
    return 0;
}

/*
 * Drive the public contract through a failed re-init.
 *
 *   contract-init-fail
 *
 * Sequence: init ok -> read ok -> force init to fail -> init -> read (must be
 * refused) -> allow init again -> init -> read.
 *
 * One CSV line per step:
 *
 *   step,<label>,rc=<n>,is_init=<0|1>,live=<0|1>,init_calls=<n>,reads=<n>
 *
 * `live` mirrors the device build's `s_active != NULL`. It is reported for the
 * same reason `is_init` is: after a failed re-init the two must agree, and a
 * build that reported `is_init=1` with no backend behind it would be handing a
 * stale handle to the next read. `reads` counts only calls that got past the
 * contract, so a refused read must leave it unchanged.
 */
static void contract_step(const char *label, int rc)
{
    sensor_mock_stats_t st;
    sensor_mock_stats(&st);
    printf("step,%s,rc=%d,is_init=%d,live=%d,init_calls=%u,reads=%u\n",
           label, rc, (int)sensor_is_initialized(), (int)st.backend_live,
           st.init_calls, st.read_calls);
}

static int cmd_contract_init_fail(void)
{
    contract_step("first_init", sensor_init());

    sensor_read_t r;
    contract_step("first_read", sensor_read(&r));

    /* Make the next bring-up attempt fail, as a run-time re-init can. */
    sensor_mock_set_init_failure(true);
    contract_step("second_init", sensor_init());

    /* The contract must refuse to read, and must not hand back a stale handle. */
    contract_step("second_read", sensor_read(&r));

    sensor_mock_set_init_failure(false);
    contract_step("third_init", sensor_init());
    contract_step("third_read", sensor_read(&r));
    return 0;
}

/*
 * Replay the sampling loop the way main.c runs it.
 *
 *   cycles <retry_interval_s> <failures_before_unavailable> <n_cycles>
 *          <unplug_at_t> <plug_back_at_t>
 *
 * Why this command exists
 * -----------------------
 * The read-failure counters are only meaningful if the *caller* respects
 * readiness, and the caller is where the defect was. main.c used one condition
 *
 *     if (!sensor_sup_ready(&s) || sensor_read(&r) != 0) {
 *         sensor_sup_note_read_failure(&s, t);     // also reached when not ready
 *
 * so short-circuit evaluation meant "the sensor is unavailable, so no read was
 * attempted" was recorded as "a read failed". Every cycle spent waiting for a
 * re-probe incremented the counters again: on hardware that reported 59 read
 * failures where 3 reads had actually been attempted.
 *
 * So this replays main.c's order — bring-up first, then the three-way read — and
 * prints the counters it produced. A test can then assert the property that was
 * violated: while the sensor is unavailable and waiting, the counters do not move.
 *
 * Timeline. Cycle 0 at t=0 brings the sensor up. Cycles 1..n run one per second.
 * The part is absent for unplug_at_t <= t < plug_back_at_t, so every read attempted
 * in that window fails and bring-up fails too.
 *
 * One line per cycle:
 *
 *   cycle,<i>,<t>,<init>,<read>,<total>,<consecutive>,<ready>
 *
 * where <init> is - | ok | fail, <read> is - | ok | fail, and `-` means the step
 * was not reached at all. A trailing `totals,` line carries the supervisor's own
 * counters.
 */
static int cmd_cycles(int argc, char **argv)
{
    if (argc < 7) {
        fprintf(stderr, "usage: cycles <retry_s> <threshold> <n_cycles> "
                        "<unplug_at_t> <plug_back_at_t>\n");
        return 2;
    }
    const double retry = strtod(argv[2], NULL);
    const unsigned threshold = (unsigned)strtoul(argv[3], NULL, 10);
    const unsigned n_cycles = (unsigned)strtoul(argv[4], NULL, 10);
    const double unplug_at = strtod(argv[5], NULL);
    const double plug_back_at = strtod(argv[6], NULL);

    sensor_supervisor_t sup;
    sensor_sup_init(&sup, retry, threshold, 0.0);

    /* Cycle 0: main.c's bring-up step, on a part that is present. */
    if (!sensor_sup_ready(&sup) && sensor_sup_should_attempt(&sup, 0.0)) {
        sensor_sup_note_attempt(&sup, 0.0, sensor_init() == 0);
    }

    for (unsigned i = 1; i <= n_cycles; i++) {
        const double t = (double)i;
        const bool present = (t < unplug_at) || (t >= plug_back_at);
        const char *init = "-";
        const char *read = "-";

        /* ---- BRING-UP (main.c does this before deciding whether to read) ---- */
        if (!sensor_sup_ready(&sup) && sensor_sup_should_attempt(&sup, t)) {
            /* A part that is absent cannot be brought up. */
            const bool ok = present;
            if (ok) {
                sensor_init();
            } else {
                sensor_mock_set_init_failure(true);
                sensor_init();
                sensor_mock_set_init_failure(false);
            }
            sensor_sup_note_attempt(&sup, t, ok);
            init = ok ? "ok" : "fail";
        }

        /* ---- READ SENSOR ------------------------------------------------ */
        if (!sensor_sup_ready(&sup)) {
            /* Case 1: unusable. No read is attempted and nothing is counted. */
            read = "-";
        } else {
            sensor_read_t r;
            bool read_ok = sensor_read(&r) == 0;
            if (read_ok && !present) {
                /* The mock would happily answer; the part is what is absent. */
                read_ok = false;
            }
            if (read_ok) {
                /* Case 3. */
                sensor_sup_note_read_success(&sup);
                read = "ok";
            } else {
                /* Case 2: a read was genuinely attempted and failed. */
                sensor_sup_note_read_failure(&sup, t);
                read = "fail";
            }
        }

        printf("cycle,%u,%.3f,%s,%s,%u,%u,%d\n", i, t, init, read,
               sup.read_failures_total, sup.read_failures_consecutive,
               (int)sensor_sup_ready(&sup));
    }

    printf("totals,%u,%u,%u,%u,%u\n", sup.init_attempts, sup.init_failures,
           sup.read_failures_total, sup.read_successes,
           sup.unavailability_events);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <sensor-before-init|sensor-after-init|supervisor ...>\n",
                argv[0]);
        return 2;
    }

    if (strcmp(argv[1], "sensor-before-init") == 0) {
        return cmd_sensor_before_init();
    }
    if (strcmp(argv[1], "sensor-after-init") == 0) {
        return cmd_sensor_after_init();
    }
    if (strcmp(argv[1], "supervisor") == 0) {
        return cmd_supervisor(argc, argv);
    }
    if (strcmp(argv[1], "recovery") == 0) {
        return cmd_recovery(argc, argv);
    }
    if (strcmp(argv[1], "contract-init-fail") == 0) {
        return cmd_contract_init_fail();
    }
    if (strcmp(argv[1], "cycles") == 0) {
        return cmd_cycles(argc, argv);
    }

    fprintf(stderr, "unknown subcommand: %s\n", argv[1]);
    return 2;
}
