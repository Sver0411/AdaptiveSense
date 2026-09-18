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
 *   sleep_requests          how often the loop entered its idle phase
 *   scheduled_idle_s        the summed duration it asked to be idle for
 *   light_sleep_entries     how often the chip really entered light sleep,
 *                           in ANY phase (PM callback,
 *                           CONFIG_PM_LIGHT_SLEEP_CALLBACKS)
 *   light_sleep_s           the summed **actual** light-sleep time, total
 *   idle_light_sleep_entries / idle_light_sleep_s
 *                           the subset of the above that happened while the duty
 *                           cycle was in its PM_SLEEP phase
 *
 * Why the split matters. The PM callback fires for *every* automatic light sleep,
 * and the chip can enter one during any `vTaskDelay()` — not only the main loop's
 * scheduled idle. The BH1750's one-shot conversion, for example, waits ~180 ms
 * inside `vTaskDelay()`, and the chip may well sleep through part of that. So
 * `light_sleep_s` (total) counts sleep wherever it happened, while
 * `idle_light_sleep_s` counts only sleep inside the scheduled idle window, which
 * is the quantity `idle_light_sleep_s / scheduled_idle_s` means. Dividing the
 * total by the scheduled idle instead would attribute other phases' sleep to the
 * idle window and could exceed 100 %.
 *
 * `idle_light_sleep_s <= light_sleep_s` holds by construction: every sleep that
 * reaches the idle counters reaches the totals too. The accounting rule lives in
 * `power_stats.c` (pure C99) so it can be tested on a host rather than trusted.
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

/* Duty-cycle statistics. Every field is an observation, not a model.
 *
 * `light_sleep_*` is the TOTAL automatic light sleep, in whatever phase it
 * happened; `idle_light_sleep_*` is the subset inside the scheduled idle window
 * (PM_SLEEP). See the header comment above for why the two are kept apart. */
typedef struct {
    unsigned long sleep_requests;            /* times the duty cycle went idle */
    double        scheduled_idle_s;          /* summed requested idle duration */
    unsigned long light_sleep_entries;       /* total light sleeps observed */
    double        light_sleep_s;             /* total ACTUAL light-sleep time */
    unsigned long idle_light_sleep_entries;  /* subset during PM_SLEEP */
    double        idle_light_sleep_s;        /* subset during PM_SLEEP */
    bool          pm_configured;             /* esp_pm_configure() succeeded */
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

/*
 * Apply one observed light sleep to `stats`.
 *
 * The sleep is added to the totals always, and to the idle-phase counters only
 * when `phase` is PM_SLEEP — i.e. when the duty cycle was in its scheduled idle
 * window rather than in a sensor read, a conversion wait or a transmission.
 * Pure C99 (host-tested in tests/test_power_stats.py) so the attribution rule is
 * a tested property rather than a line inside a callback.
 */
void power_stats_note_light_sleep(power_stats_t *stats, double sleep_s,
                                  pm_phase_t phase);

/*
 * idle_light_sleep_s / scheduled_idle_s, i.e. the fraction of the scheduled idle
 * window the chip actually slept through. Returns 0 when nothing has been
 * scheduled yet. Guaranteed <= 1 by construction; see power_stats.c.
 */
double power_stats_idle_sleep_ratio(const power_stats_t *stats);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_POWER_MGMT_H */
