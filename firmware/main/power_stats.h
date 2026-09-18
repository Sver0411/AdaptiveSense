/*
 * power_stats.h — duty-cycle accounting rules, pure C99 and host-testable.
 *
 * The types live in power_mgmt.h (which is itself ESP-IDF free); this unit holds
 * only the *rules* for updating them, so that the rule "a light sleep counts
 * towards the idle ratio only if the duty cycle was in its PM_SLEEP phase" is
 * one tested function rather than a line inside a callback. See power_mgmt.h for
 * why the totals and the idle subset are reported separately.
 */
#ifndef ADAPTIVESENSE_POWER_STATS_H
#define ADAPTIVESENSE_POWER_STATS_H

#include "power_mgmt.h"

#ifdef __cplusplus
extern "C" {
#endif

void power_stats_note_light_sleep(power_stats_t *stats, double sleep_s,
                                  pm_phase_t phase);

double power_stats_idle_sleep_ratio(const power_stats_t *stats);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POWER_STATS_H */
