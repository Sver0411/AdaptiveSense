/*
 * power_mgmt.c — sleep-mode implementation.
 *
 * Three explicitly named modes; the configuration selects one and the log
 * states which one is active at boot. Nothing aliases "deep sleep" onto a light
 * sleep call.
 *
 * The Wi-Fi radio is NOT stopped here. Stopping the station before a light
 * sleep (v0.1) permanently killed the connection, because the wake path never
 * called `esp_wifi_start()` again — after the first sleep every publish failed
 * silently. Instead the communication layer enables the Wi-Fi driver's
 * modem-sleep power save once at start-up, and `esp_light_sleep_start()` then
 * keeps the connection alive while the modem sleeps between beacons. See
 * docs/power_management.md for the documented radio lifecycle.
 */
#include "power_mgmt.h"

#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "config.h"

static const char *TAG = "pm";

static pm_phase_t g_phase = PM_ACTIVE;
static power_stats_t g_stats;

pm_sleep_mode_t power_sleep_mode(void)
{
#if CONFIG_AS_SLEEP_MODE == 0
    return PM_SLEEP_NONE;
#elif CONFIG_AS_SLEEP_MODE == 1
    return PM_SLEEP_LIGHT;
#elif CONFIG_AS_SLEEP_MODE == 2
    return PM_SLEEP_DEEP;
#else
#error "CONFIG_AS_SLEEP_MODE must be 0 (none), 1 (light) or 2 (deep)"
#endif
}

const char *power_sleep_mode_name(pm_sleep_mode_t mode)
{
    switch (mode) {
    case PM_SLEEP_NONE:
        return "none (FreeRTOS delay)";
    case PM_SLEEP_LIGHT:
        return "light sleep";
    case PM_SLEEP_DEEP:
        return "deep sleep (experimental, not enabled)";
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

void power_deep_sleep(uint64_t sleep_us)
{
    /*
     * Not used by the default configuration: deep sleep reboots the chip, so
     * the change-detector and scheduler state (EMA baselines, ladder position,
     * event debounce) would have to be persisted in RTC memory. That is
     * deliberately out of scope for v0.2 — see README "Hardware status" and
     * docs/power_management.md.
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

    const int64_t before_us = esp_timer_get_time();
    const int64_t target_us = (int64_t)(sleep_s * 1e6);

    g_stats.sleep_calls++;
    g_stats.requested_s += sleep_s;

    switch (power_sleep_mode()) {
    case PM_SLEEP_LIGHT: {
        /* Timer wake only. No other wake source is enabled, so the node cannot
         * be woken by anything but the schedule. */
        esp_sleep_enable_timer_wakeup((uint64_t)target_us);
        const esp_err_t err = esp_light_sleep_start();
        if (err == ESP_OK) {
            g_stats.light_sleep_ok++;
        } else {
            g_stats.light_sleep_failed++;
            ESP_LOGW(TAG, "light sleep failed (%s); delaying instead",
                     esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0)));
        }
        break;
    }
    case PM_SLEEP_DEEP:
        /* Unreachable: config.example.h rejects this value at compile time. */
        ESP_LOGE(TAG, "deep sleep requested in the periodic path; aborting");
        vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0)));
        break;
    case PM_SLEEP_NONE:
    default:
        vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0)));
        break;
    }

    const int64_t after_us = esp_timer_get_time();
    const double elapsed = (double)(after_us - before_us) / 1e6;
    g_stats.actual_s += elapsed;
    return elapsed;
}
