/*
 * soil_host_main.c — host driver for the soil moisture maths
 * (`firmware/main/soil_moisture_math.c`).
 *
 * That unit is pure C99, so both the normalisation and the batch statistics run
 * on a workstation. `tests/test_soil_moisture_math.py` uses it.
 *
 * Subcommands
 * -----------
 *   normalize <dry_raw> <wet_raw> <raw>
 *       Print the clamped index, the unclamped value, and whether the
 *       calibration was accepted.
 *
 *   calib <dry_raw> <wet_raw> [adc_max_raw]
 *       Print whether the calibration would be accepted.
 *
 *   batch <v1,v2,v3,...>
 *       Print the batch statistics of the given raw samples.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "soil_moisture_math.h"

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <normalize|calib|batch ...>\n", argv[0]);
        return 2;
    }

    if (strcmp(argv[1], "normalize") == 0) {
        if (argc < 5) {
            fprintf(stderr, "usage: normalize <dry> <wet> <raw>\n");
            return 2;
        }
        soil_moisture_calib_t calib = {
            .dry_raw = atoi(argv[2]),
            .wet_raw = atoi(argv[3]),
        };
        const int raw = atoi(argv[4]);
        const bool usable = soil_moisture_calib_valid(&calib, 4095);

        float norm = -1.0f, unclamped = -1.0f;
        const int rc = soil_moisture_normalize(&calib, raw, &norm, &unclamped);
        printf("normalize,raw=%d,dry=%d,wet=%d,calib_valid=%d,rc=%d,"
               "norm=%.6f,unclamped=%.6f\n",
               raw, calib.dry_raw, calib.wet_raw, (int)usable, rc,
               (double)norm, (double)unclamped);
        return 0;
    }

    if (strcmp(argv[1], "calib") == 0) {
        if (argc < 4) {
            fprintf(stderr, "usage: calib <dry> <wet> [adc_max_raw]\n");
            return 2;
        }
        soil_moisture_calib_t calib = {
            .dry_raw = atoi(argv[2]),
            .wet_raw = atoi(argv[3]),
        };
        const int max_raw = (argc >= 5) ? atoi(argv[4]) : 4095;
        printf("calib,valid=%d\n",
               (int)soil_moisture_calib_valid(&calib, max_raw));
        return 0;
    }

    if (strcmp(argv[1], "batch") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: batch <v1,v2,...>\n");
            return 2;
        }
        /* strtok() mutates its input, so work on a copy (see sensor_host_main.c
         * for why: strdup is POSIX, not ISO C, under -std=c11). */
        const size_t len = strlen(argv[2]) + 1u;
        char *copy = malloc(len);
        if (copy == NULL) {
            return 2;
        }
        memcpy(copy, argv[2], len);

        /* Two passes: count, then fill. Batches here are tens of samples. */
        int count = 0;
        for (char *tok = strtok(copy, ","); tok != NULL; tok = strtok(NULL, ",")) {
            count++;
        }
        if (count < 1) {
            fprintf(stderr, "no samples given\n");
            free(copy);
            return 2;
        }
        int *samples = malloc((size_t)count * sizeof(int));
        if (samples == NULL) {
            free(copy);
            return 2;
        }
        memcpy(copy, argv[2], len);
        int i = 0;
        for (char *tok = strtok(copy, ","); tok != NULL; tok = strtok(NULL, ",")) {
            samples[i++] = atoi(tok);
        }
        free(copy);

        soil_batch_stats_t st;
        soil_batch_stats_compute(samples, count, &st);
        printf("batch,n=%d,min=%d,max=%d,mean=%.4f,std=%.4f,median=%d,"
               "trimmed=%.4f\n",
               count, st.min, st.max, st.mean, st.std, st.median,
               st.trimmed_mean);
        free(samples);
        return 0;
    }

    fprintf(stderr, "unknown subcommand: %s\n", argv[1]);
    return 2;
}
