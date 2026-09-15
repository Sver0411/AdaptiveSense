/*
 * power_mgmt.h — Power Management Layer.
 *
 * The node's duty cycle is: sample -> evaluate -> publish if requested -> idle
 * until the next scheduled sample. This layer owns only the "idle" part.
 *
 * How the node actually sleeps
 * ---------------------------
 * The application does **not** call `esp_light_sleep_start()`. It calls
 * `vTaskDelay()`, and the ESP-IDF power-management subsystem decides whether the
 * chip can enter light sleep while the scheduler is idle:
 *
 *     vTaskDelay()            (application, this file)
 *        └─ FreeRTOS idle task
 *             └─ automatic light sleep   (CONFIG_PM_ENABLE +
 *                                         CONFIG_FREERTOS_USE_TICKLESS_IDLE)
 *                  └─ Wi-Fi driver PM locks decide whether the modem may sleep
 *
 * This matters because the radio is then part of the decision: the Wi-Fi driver
 * acquires a PM lock while it needs the modem, and with `WIFI_PS_MIN_MODEM` it
 * releases it between DTIM beacons, so the association survives the sleep
 * without the application stopping and restarting the station.
 *
 * Modes
 * -----
 *   PM_SLEEP_NONE        `vTaskDelay()` only. PM_ENABLE is still compiled in, but
 *                        `esp_pm_configure()` is not called with light sleep, so
 *                        the chip stays awake. Bench/debug use.
 *   PM_SLEEP_AUTO_LIGHT  `vTaskDelay()` plus `esp_pm_configure()` with
 *                        `light_sleep_enable = true`. Default.
 *
 * Deep sleep is available as a documented primitive (`power_deep_sleep()`) but is
 * rejected at compile time by config.example.h: it reboots the chip, so the
 * detector's EMA baselines, the ladder position and the event debounce state
 * would not survive. It is experimental and not enabled.
 *
 * What is measured, and what is not
 * ---------------------------------
 * `power_get_stats()` reports only quantities the firmware can actually observe:
 *
 *   sleep_requests        how often the loop entered its idle phase
 *   scheduled_idle_s      the summed duration it asked to be idle for
 *   light_sleep_entries   how often the chip really entered light sleep
 *                         (counted by a PM callback, CONFIG_PM_LIGHT_SLEEP_CALLBACKS)
 *   light_sleep_us        the summed **actual** light-sleep time, taken from the
 *                         callback's exit value
 *
 * There is no "energy" field and no joule or millijoule figure: nothing in this
 * firmware can measure current. Real energy requires an external monitor
 * (INA219 / Joulescope / Nordic Power Profiler). See docs/hardware.md.
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
    PM_SLEEP       /* idle until the next scheduled sample */
} pm_phase_t;

typedef enum {
    PM_SLEEP_NONE = 0,      /* vTaskDelay only; the chip stays awake */
    PM_SLEEP_AUTO_LIGHT = 1 /* vTaskDelay + ESP-IDF automatic light sleep */
} pm_sleep_mode_t;

/* Duty-cycle statistics. Every field is an observation, not a model. */
typedef struct {
    unsigned long sleep_requests;      /* times the duty cycle went idle */
    double        scheduled_idle_s;    /* summed requested idle duration */
    unsigned long light_sleep_entries; /* light sleeps the PM subsystem reported */
    double        light_sleep_s;       /* summed ACTUAL light-sleep time */
    bool          pm_configured;       /* esp_pm_configure() succeeded */
} power_stats_t;

/* The sleep mode selected by CONFIG_AS_SLEEP_MODE. */
pm_sleep_mode_t power_sleep_mode(void);
const char *power_sleep_mode_name(pm_sleep_mode_t mode);

/*
 * Configure the ESP-IDF power manager, register the light-sleep observation
 * callback, and log the resulting mode.
 *
 * Returns 0 on success. A non-zero return means automatic light sleep is NOT
 * active (for example because the build has CONFIG_PM_ENABLE disabled); the node
 * still runs, it just never sleeps.
 */
int power_init(void);

/*
 * Idle for `sleep_s` seconds and return the elapsed wall-clock seconds measured
 * with `esp_timer`.
 *
 * The return value is the duration of the delay, which is what the caller needs
 * to keep its schedule. It is **not** a measurement of how long the chip spent in
 * light sleep — that is reported separately, and only when the PM subsystem
 * actually entered light sleep (`power_get_stats()->light_sleep_s`).
 *
 * This function must not stop Wi-Fi: the radio lifecycle belongs to the
 * communication layer.
 */
double power_sleep(double sleep_s);

/*
 * Enter deep sleep for `sleep_us` microseconds. Deep sleep does NOT return: the
 * device boots from scratch on the next timer wake. Documented primitive; not
 * selected by the default configuration.
 */
void power_deep_sleep(uint64_t sleep_us);

/* Set the immediate active phase (for logging). */
void power_set_phase(pm_phase_t phase);
pm_phase_t power_phase(void);

/* Accumulated duty-cycle statistics since boot. */
const power_stats_t *power_get_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POWER_MGMT_H */
