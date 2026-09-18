/*
 * power_stats.c — the duty-cycle accounting rules.
 *
 * Pure C99, no ESP-IDF dependency, host-testable (tests/test_power_stats.py).
 *
 * It exists so that the attribution rule — which observed light sleeps belong to
 * the scheduled idle window and which do not — is one tested function instead of
 * a line inside an interrupt-context callback. The PM exit callback fires for
 * every automatic light sleep, and the chip can enter one during *any*
 * `vTaskDelay()`: the main loop's idle, but equally the BH1750's ~180 ms one-shot
 * conversion wait, or a transmission. Only the first of those is what
 * `idle_light_sleep_s / scheduled_idle_s` is meant to describe, so the other two
 * must reach the totals without reaching the idle counters.
 */
#include <stddef.h>

#include "power_stats.h"

void power_stats_note_light_sleep(power_stats_t *stats, double sleep_s,
                                  pm_phase_t phase)
{
    if (stats == NULL || sleep_s <= 0.0) {
        return;
    }

    /* Total first: every observed sleep is on the record, wherever it happened. */
    stats->light_sleep_entries++;
    stats->light_sleep_s += sleep_s;

    if (phase == PM_SLEEP) {
        /* And only the scheduled idle window's share reaches the idle counters. */
        stats->idle_light_sleep_entries++;
        stats->idle_light_sleep_s += sleep_s;
    }
}

double power_stats_idle_sleep_ratio(const power_stats_t *stats)
{
    if (stats == NULL || stats->scheduled_idle_s <= 0.0) {
        return 0.0;
    }
    /*
     * <= 1 by construction, not by clamping: idle_light_sleep_s only ever
     * accumulates entries that light_sleep_s accumulated too, from the same
     * observations. If this ever exceeds 1 the accounting is broken, and the
     * host test asserts that it cannot be.
     */
    return stats->idle_light_sleep_s / stats->scheduled_idle_s;
}
