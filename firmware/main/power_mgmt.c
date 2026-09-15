/*
 * power_mgmt.c — deep sleep / duty-cycle implementation.
 *
 * The node spends the vast majority of its time asleep and wakes only for each
 * scheduled sample. The Wi-Fi/MQTT connection is only maintained during the
 * awake window; the radio is stopped before sleeping.
 *
 * This module is scheduler-agnostic: it only provides "sleep for N seconds"
 * semantics with a real-time measurement of how long the sleep took, which is
 * used later as an energy proxy (see docs/methodology.md). Deep sleep is
 * exposed as a separate, non-returning path so the scheduling policy and the
 * power policy can be swapped and compared independently.
 */
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "power_mgmt.h"
#include "config.h"

static const char *TAG = "pm";
static pm_phase_t g_phase = PM_ACTIVE;

void power_set_phase(pm_phase_t phase)
{
    g_phase = phase;
}

pm_phase_t power_phase(void)
{
    return g_phase;
}

void power_deep_sleep(uint64_t sleep_us)
{
    esp_wifi_stop();
    esp_sleep_enable_timer_wakeup(sleep_us);
    /* Does not return: device boots from scratch at the next timer wake. */
    esp_deep_sleep_start();
}

double power_sleep(double sleep_s)
{
    if (sleep_s <= 0.0) return 0.0;

    int64_t before = esp_timer_get_time();   /* microseconds */
    int64_t target_us = (int64_t)(sleep_s * 1e6);

#if CONFIG_AS_DEEP_SLEEP_ENABLE
    esp_wifi_stop();
    esp_sleep_enable_timer_wakeup(target_us);
    esp_err_t r = esp_light_sleep_start();
    if (r != ESP_OK) {
        ESP_LOGW(TAG, "light-sleep start failed (%s); busy-waiting instead",
                 esp_err_to_name(r));
        vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0f)));
    }
#else
    vTaskDelay(pdMS_TO_TICKS((uint32_t)(sleep_s * 1000.0f)));
#endif

    int64_t after = esp_timer_get_time();
    return (double)(after - before) / 1e6;
}