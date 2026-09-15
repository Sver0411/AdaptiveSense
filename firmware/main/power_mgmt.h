/*
 * power_mgmt.h — Power Management Layer.
 *
 * Encapsulates the node's duty cycle. It is deliberately independent of the
 * adaptive scheduler: given a target sleep duration it enters light or deep
 * sleep and reports how long the node actually slept. This separation lets
 * alternative power policies be compared without touching scheduling.
 */
#ifndef ADAPTIVESENSE_POWER_MGMT_H
#define ADAPTIVESENSE_POWER_MGMT_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PM_ACTIVE,   /* radio + sensors on, compute/transmit in progress */
    PM_SAMPLE,   /* sensor read window (short) */
    PM_TRANSMIT, /* Wi-Fi/MQTT transmission window (short) */
    PM_SLEEP,    /* sleeping until the next scheduled sample */
} pm_phase_t;

/* Work flow phases used by main.c to drive the duty cycle:
 *   WAKE -> READ SENSOR -> EVALUATE CHANGE (scheduler) -> DECIDE UPLOAD
 *        -> DETERMINE NEXT INTERVAL -> SLEEP
 */

/* Enter a sleep mode for `sleep_s` seconds and return the ACTUAL elapsed
 * seconds. Uses light sleep (memory retained) so the node can resume and the
 * sleep duration can be measured. */
double power_sleep(double sleep_s);

/* Enter deep sleep for `sleep_us` microseconds. Deep sleep does NOT return:
 * the device boots from scratch on the next timer wake. This is the
 * lowest-power path and is kept independent of the adaptive scheduler so the
 * two can be tuned separately. */
void power_deep_sleep(uint64_t sleep_us);

/* Set the immediate active phase (for logging / energy-proxy bookkeeping). */
void power_set_phase(pm_phase_t phase);
pm_phase_t power_phase(void);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POWER_MGMT_H */