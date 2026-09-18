/*
 * power_stats_host_main.c — host driver for the duty-cycle accounting rules
 * (`firmware/main/power_stats.c`).
 *
 * The PM exit callback cannot be driven from a workstation, but the rule it
 * applies can: which observed light sleeps belong to the scheduled idle window
 * and which do not. This driver applies a timeline of observations and prints the
 * resulting counters, so `tests/test_power_stats.py` can assert the property that
 * matters — `idle_light_sleep_s <= light_sleep_s`, and a ratio that cannot exceed
 * 1 because of how the numbers are accumulated rather than because they were
 * clamped.
 *
 *   stats <spec>
 *
 *     spec   comma-separated steps, applied in order:
 *              PM_SLEEP:<s>    a light sleep during the scheduled idle window
 *              PM_SAMPLE:<s>   a light sleep during a sensor read
 *              PM_TRANSMIT:<s> a light sleep during a transmission
 *              PM_ACTIVE:<s>   a light sleep in the active phase
 *              sched:<s>       the loop asked to be idle for <s> seconds
 *
 * Prints one line:
 *
 *   stats,entries=<n>,total_s=<x>,idle_entries=<m>,idle_s=<y>,
 *          sched=<z>,ratio=<r>,invariant_ok=<0|1>
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "power_stats.h"

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: stats <step,...>\n");
        return 2;
    }

    power_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    /* strtok() mutates its input, so work on a copy. malloc + memcpy rather than
     * strdup: strdup is POSIX, not ISO C, and glibc hides it under -std=c11. */
    const size_t n = strlen(argv[2]) + 1u;
    char *steps = malloc(n);
    if (steps == NULL) {
        return 2;
    }
    memcpy(steps, argv[2], n);

    for (char *tok = strtok(steps, ","); tok != NULL; tok = strtok(NULL, ",")) {
        char *colon = strchr(tok, ':');
        if (colon == NULL) {
            continue;
        }
        *colon = '\0';
        const double seconds = strtod(colon + 1, NULL);

        if (strcmp(tok, "sched") == 0) {
            stats.sleep_requests++;
            stats.scheduled_idle_s += seconds;
            continue;
        }

        pm_phase_t phase;
        if (strcmp(tok, "PM_SLEEP") == 0) {
            phase = PM_SLEEP;
        } else if (strcmp(tok, "PM_SAMPLE") == 0) {
            phase = PM_SAMPLE;
        } else if (strcmp(tok, "PM_TRANSMIT") == 0) {
            phase = PM_TRANSMIT;
        } else if (strcmp(tok, "PM_ACTIVE") == 0) {
            phase = PM_ACTIVE;
        } else {
            fprintf(stderr, "unknown phase: %s\n", tok);
            free(steps);
            return 2;
        }
        power_stats_note_light_sleep(&stats, seconds, phase);
    }
    free(steps);

    const double ratio = power_stats_idle_sleep_ratio(&stats);
    const int invariant_ok =
        (stats.idle_light_sleep_s <= stats.light_sleep_s) &&
        (stats.idle_light_sleep_entries <= stats.light_sleep_entries) &&
        (ratio <= 1.0);

    printf("stats,entries=%lu,total_s=%.6f,idle_entries=%lu,idle_s=%.6f,"
           "sched=%.6f,ratio=%.6f,invariant_ok=%d\n",
           stats.light_sleep_entries, stats.light_sleep_s,
           stats.idle_light_sleep_entries, stats.idle_light_sleep_s,
           stats.scheduled_idle_s, ratio, invariant_ok);
    return 0;
}
