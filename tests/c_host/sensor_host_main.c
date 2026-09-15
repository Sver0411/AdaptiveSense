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
    sensor_sup_init(&sup, retry_interval, 0.0);
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

    fprintf(stderr, "unknown subcommand: %s\n", argv[1]);
    return 2;
}
