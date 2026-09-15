/*
 * parity_main.c — host-side driver for the Python/C policy parity test.
 *
 * It links the *same* two translation units that run on the ESP32-S3
 * (`change_detector.c` and `adaptive_scheduler.c`) plus `policy_config.c`, so
 * the configuration under test is the device configuration
 * (`config.example.h`, which mirrors experiments/experiment_config.yaml).
 *
 * Replay semantics mirror `simulator/replay.py::run_adaptive`:
 *
 *   next_sample = t[0]
 *   for each fixture row:
 *       if t < next_sample: skip
 *       score, event = cd_update(t, values, all_valid)
 *       as_update(t, values, all_valid, score, event, &decision)
 *       emit decision            (the sample the node just took)
 *       next_sample = t + decision.interval_s
 *
 * Usage: parity_main <fixture.csv> <out.csv>
 *
 * Input CSV columns : timestamp,temperature,humidity,pressure,light
 * Output CSV columns: timestamp,state,interval_s,score,event,upload_requested
 *
 * `timestamp` in the output is the sample time as seen by the policy.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "adaptive_scheduler.h"
#include "change_detector.h"
#include "policy_config.h"

#define MAX_ROWS 20000

static const char *state_name(as_state_t s)
{
    switch (s) {
    case AS_STABLE:
        return "STABLE";
    case AS_ACTIVE:
        return "ACTIVE";
    case AS_ALERT:
        return "ALERT";
    default:
        return "UNKNOWN";
    }
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s <fixture.csv> <out.csv>\n", argv[0]);
        return 2;
    }

    FILE *in = fopen(argv[1], "r");
    if (in == NULL) {
        fprintf(stderr, "cannot open fixture %s\n", argv[1]);
        return 2;
    }
    FILE *out = fopen(argv[2], "w");
    if (out == NULL) {
        fprintf(stderr, "cannot open output %s\n", argv[2]);
        fclose(in);
        return 2;
    }

    static double ts[MAX_ROWS];
    static float  values[MAX_ROWS][AS_NUM_CHANNELS];
    int n = 0;

    char line[512];
    /* header */
    if (fgets(line, sizeof(line), in) == NULL) {
        fprintf(stderr, "empty fixture\n");
        return 2;
    }
    while (n < MAX_ROWS && fgets(line, sizeof(line), in) != NULL) {
        double t, temperature, humidity, pressure, light;
        if (sscanf(line, "%lf,%lf,%lf,%lf,%lf",
                   &t, &temperature, &humidity, &pressure, &light) != 5) {
            continue;
        }
        ts[n] = t;
        values[n][0] = (float)temperature;
        values[n][1] = (float)humidity;
        values[n][2] = (float)pressure;
        values[n][3] = (float)light;
        n++;
    }
    fclose(in);

    if (n == 0) {
        fprintf(stderr, "fixture contained no rows\n");
        fclose(out);
        return 2;
    }

    cd_config_t cd_cfg;
    as_config_t as_cfg;
    policy_build_detector_config(&cd_cfg);
    policy_build_scheduler_config(&as_cfg);

    cd_t detector;
    as_t scheduler;
    cd_init(&detector, &cd_cfg);
    as_init(&scheduler, &as_cfg);

    bool valid[AS_NUM_CHANNELS];
    for (int c = 0; c < AS_NUM_CHANNELS; c++) {
        valid[c] = true;
    }

    fprintf(out, "timestamp,state,interval_s,score,event,upload_requested\n");

    double next_sample = ts[0];
    int emitted = 0;
    for (int i = 0; i < n; i++) {
        if (ts[i] < next_sample - 1e-9) {
            continue;
        }
        bool event = false;
        const float score = cd_update(&detector, ts[i], values[i], valid, &event);

        as_decision_t decision;
        as_update(&scheduler, ts[i], values[i], valid, score, event, &decision);

        fprintf(out, "%.3f,%s,%.6f,%.9f,%d,%d\n",
                ts[i], state_name(decision.state), decision.interval_s,
                decision.score, decision.detected_event ? 1 : 0,
                decision.upload_requested ? 1 : 0);
        emitted++;

        next_sample = ts[i] + decision.interval_s;
    }

    fclose(out);
    if (emitted == 0) {
        fprintf(stderr, "policy emitted no samples\n");
        return 1;
    }
    return 0;
}
