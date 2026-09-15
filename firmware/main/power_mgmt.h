/*
 * power_mgmt.h — Power Management Layer.
 *
 * Encapsulates the node's duty cycle. It is deliberately independent of the
 * adaptive scheduler: given a target sleep duration it enters the configured
 * sleep mode and reports how long the node actually slept.
 *
 * The three modes are named after what they actually do (see
 * `docs/power_management.md`):
 *
 *   NONE   FreeRTOS `vTaskDelay`. Radio and CPU stay on. Development default
 *          when the node must stay reachable.
 *   LIGHT  `esp_light_sleep_start()`. RAM and the Wi-Fi connection are
 *          retained; the Wi-Fi driver's modem-sleep power save puts the radio
 *          to sleep between beacons. This is the default.
 *   DEEP   `esp_deep_sleep_start()`. The chip REBOOTS on wake, so the
 *          scheduling state cannot survive. Marked experimental and rejected
 *          at compile time in this version — see `config.example.h`.
 *
 * v0.1 had `CONFIG_AS_DEEP_SLEEP_ENABLE` guarding a call to
 * `esp_light_sleep_start()`, and it stopped Wi-Fi before every sleep without
 * ever restarting it.
 */
#ifndef ADAPTIVESENSE_POWER_MGMT_H
#define ADAPTIVESENSE_POWER_MGMT_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PM_ACTIVE = 0, /* radio + sensors on, compute/transmit in progress */
    PM_SAMPLE,     /* sensor read window (short) */
    PM_TRANSMIT,   /* Wi-Fi/MQTT transmission window (short) */
    PM_SLEEP       /* sleeping until the next scheduled sample */
} pm_phase_t;

typedef enum {
    PM_SLEEP_NONE = 0,
    PM_SLEEP_LIGHT = 1,
    PM_SLEEP_DEEP = 2
} pm_sleep_mode_t;

/* Duty-cycle statistics, usable as an energy proxy (no physical units). */
typedef struct {
    unsigned long sleep_calls;
    unsigned long light_sleep_ok;
    unsigned long light_sleep_failed;
    double requested_s; /* summed requested sleep duration */
    double actual_s;    /* summed measured sleep duration */
} power_stats_t;

/* The sleep mode selected by CONFIG_AS_SLEEP_MODE. */
pm_sleep_mode_t power_sleep_mode(void);
const char *power_sleep_mode_name(pm_sleep_mode_t mode);

/*
 * Sleep for `sleep_s` seconds using the configured mode and return the ACTUAL
 * elapsed seconds measured with `esp_timer`. Must not stop Wi-Fi: the radio
 * lifecycle is the communication layer's responsibility.
 */
double power_sleep(double sleep_s);

/*
 * Enter deep sleep for `sleep_us` microseconds. Deep sleep does NOT return: the
 * device boots from scratch on the next timer wake. Kept as a documented
 * primitive; not selected by default in this version because the adaptive
 * scheduler state is not persisted across a reboot.
 */
void power_deep_sleep(uint64_t sleep_us);

/* Set the immediate active phase (for logging / energy-proxy bookkeeping). */
void power_set_phase(pm_phase_t phase);
pm_phase_t power_phase(void);

/* Accumulated duty-cycle statistics since boot. */
const power_stats_t *power_get_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POWER_MGMT_H */
