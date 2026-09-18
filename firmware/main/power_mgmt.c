/*
 * power_mgmt.c — duty-cycle implementation on top of the ESP-IDF power manager.
 *
 * The application asks to be idle with `vTaskDelay()`; whether the chip then
 * enters light sleep is decided by the ESP-IDF PM subsystem together with the
 * Wi-Fi driver's PM locks. See power_mgmt.h for the full rationale.
 *
 * Two things this file deliberately does NOT do, both of which were wrong before:
 *
 *  1. It never calls `esp_light_sleep_start()`. Sleeping from the application
 *     layer bypasses the PM subsystem, so the Wi-Fi driver has no say in whether
 *     the modem may sleep — which is how an earlier revision ended up claiming
 *     the association would survive a sleep it had not asked the driver about.
 *
 *  2. It never stops the radio. Stopping the station before a sleep without ever
 *     restarting it permanently killed the connection.
 */
#include "power_mgmt.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "config.h"
#include "power_stats.h"

static const char *TAG = "pm";

static pm_phase_t g_phase = PM_ACTIVE;
static power_stats_t g_stats;

/* ------------------------------------------------------------------ */
/* light-sleep observation                                             */
/* ------------------------------------------------------------------ */
#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS

/*
 * Called from the IDLE task context, so it must not block. It only increments
 * counters, which is exactly what the application layer cannot do for itself:
 * without these callbacks the firmware would have to *assume* that a completed
 * `vTaskDelay()` means the chip slept, and would have no way to tell whether the
 * Wi-Fi driver's PM locks had kept it awake.
 */
static esp_err_t light_sleep_exit_cb(int64_t sleep_time_us, void *arg)
{
    (void)arg;
    if (sleep_time_us > 0) {
        /*
         * The attribution rule lives in power_stats.c (pure C, host-tested): the
         * sleep always reaches the totals, and reaches the idle counters only if
         * the duty cycle was in its PM_SLEEP phase. This callback runs in idle
         * task context and must not block, so it does nothing else.
         */
        power_stats_note_light_sleep(&g_stats, (double)sleep_time_us / 1e6,
                                     g_phase);
    }
    return ESP_OK;
}

static esp_err_t light_sleep_enter_cb(int64_t sleep_time_us, void *arg)
{
    (void) sleep_time_us;
    (void) arg;
    return ESP_OK; /* allow light sleep */
}

static void register_light_sleep_observer(void)
{
    esp_pm_sleep_cbs_register_config_t cbs = {
        .enter_cb = light_sleep_enter_cb,
        .exit_cb = light_sleep_exit_cb,
        .enter_cb_user_arg = NULL,
        .exit_cb_user_arg = NULL,
        .enter_cb_prior = 0,
        .exit_cb_prior = 0,
    };
    const esp_err_t err = esp_pm_light_sleep_register_cbs(&cbs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "light-sleep observation callback not registered (%s); "
                      "the duty-cycle log will report no sleep observations",
                 esp_err_to_name(err));
    }
}

#endif /* CONFIG_PM_LIGHT_SLEEP_CALLBACKS */

/* ------------------------------------------------------------------ */
/* mode / phase bookkeeping                                            */
/* ------------------------------------------------------------------ */
pm_sleep_mode_t power_sleep_mode(void)
{
#if CONFIG_AS_SLEEP_MODE == 0
    return PM_SLEEP_NONE;
#elif CONFIG_AS_SLEEP_MODE == 1
    return PM_SLEEP_AUTO_LIGHT;
#else
#error "CONFIG_AS_SLEEP_MODE must be 0 (none) or 1 (auto light); 2 (deep) is rejected by config.example.h"
#endif
}

const char *power_sleep_mode_name(pm_sleep_mode_t mode)
{
    switch (mode) {
    case PM_SLEEP_NONE:
        return "none (vTaskDelay only, chip stays awake)";
    case PM_SLEEP_AUTO_LIGHT:
        return "automatic light sleep (ESP-IDF PM + FreeRTOS tickless idle)";
    default:
        return "unknown";
    }
}

void power_set_phase(pm_phase_t phase)
{
    g_phase = phase;
}

pm_phase_t power_phase(void)
{
    return g_phase;
}

const power_stats_t *power_get_stats(void)
{
    return &g_stats;
}

/* ------------------------------------------------------------------ */
/* init                                                                */
/* ------------------------------------------------------------------ */
int power_init(void)
{
    const pm_sleep_mode_t mode = power_sleep_mode();

#if CONFIG_PM_ENABLE
    /*
     * No dynamic frequency scaling: the node has no CPU-bound work, so max ==
     * min and the CPU frequency stays where sdkconfig puts it. Light sleep is
     * the only power feature being asked for.
     *
     * CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ is a valid ESP32-S3 value (80 / 160 / 240)
     * and is used for both bounds, so the frequencies cannot drift away from
     * sdkconfig.
     */
    esp_pm_config_t pm_config = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .light_sleep_enable = (mode == PM_SLEEP_AUTO_LIGHT),
    };

    const esp_err_t err = esp_pm_configure(&pm_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_pm_configure failed: %s", esp_err_to_name(err));
        return -1;
    }
    g_stats.pm_configured = true;

    ESP_LOGI(TAG, "power management: mode=%s, cpu %d-%d MHz, light_sleep=%d",
             power_sleep_mode_name(mode), pm_config.min_freq_mhz,
             pm_config.max_freq_mhz, (int)pm_config.light_sleep_enable);

#if CONFIG_PM_LIGHT_SLEEP_CALLBACKS
    if (mode == PM_SLEEP_AUTO_LIGHT) {
        register_light_sleep_observer();
    }
#endif
    return 0;
#else
    /* CONFIG_PM_ENABLE is off: the chip will never enter automatic light sleep,
     * so say so instead of letting the log imply otherwise. */
    ESP_LOGW(TAG, "CONFIG_PM_ENABLE is disabled: automatic light sleep is "
                  "unavailable and the node stays awake between samples "
                  "(requested mode: %s)",
             power_sleep_mode_name(mode));
    return -1;
#endif
}

/* ------------------------------------------------------------------ */
/* sleep                                                               */
/* ------------------------------------------------------------------ */
void power_deep_sleep(uint64_t sleep_us)
{
    /*
     * Not used by the default configuration: deep sleep reboots the chip, so the
     * change detector's EMA baselines, the ladder position and the event debounce
     * state would all be lost. That is out of scope for v0.3 — see the README's
     * Hardware status and Limitations.
     */
    ESP_LOGW(TAG, "entering deep sleep: the device will reboot and all "
                  "scheduling state will be lost");
    esp_sleep_enable_timer_wakeup(sleep_us);
    esp_deep_sleep_start();
}

double power_sleep(double sleep_s)
{
    if (sleep_s <= 0.0) {
        return 0.0;
    }

    g_stats.sleep_requests++;
    g_stats.scheduled_idle_s += sleep_s;

    const int64_t before_us = esp_timer_get_time();

    /* Both modes idle the same way; PM_SLEEP_AUTO_LIGHT differs only in that
     * esp_pm_configure() armed light sleep, so the PM subsystem may use this
     * idle window (and the Wi-Fi driver may let the modem sleep through it). */
    vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0)));

    const int64_t after_us = esp_timer_get_time();
    return (double)(after_us - before_us) / 1e6;
}
