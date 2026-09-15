/*
 * main.c — AdaptiveSense node main loop.
 *
 * Documented duty cycle (one iteration):
 *
 *   SLEEP until the scheduled sample time
 *   -> READ SENSOR            (forced-mode BME280 conversion)
 *   -> EVALUATE CHANGE        (change_detector: score + debounced event)
 *   -> ADAPTIVE SCHEDULER     (state, next interval, upload decision)
 *   -> PUBLISH if requested   (and record whether the broker accepted it)
 *   -> DETERMINE NEXT WAKE
 *
 * The layers (sensor / change detection / adaptive scheduler / communication /
 * power management) are wired here and never know about each other directly.
 * The configuration is built by policy_config.c so that the device and the
 * host-side parity test run the identical policy configuration.
 *
 * Three behaviours differ from v0.1 and are deliberate:
 *
 *  1. A failed sensor read does NOT feed a zeroed sample into the change
 *     detector. The EMA baseline would be poisoned by the zeros, so the node
 *     retries after `min_interval` instead.
 *  2. `upload_requested` (the policy decision) and `publish_success` (the
 *     transport outcome) are logged and counted separately. v0.1 discarded the
 *     publish return value, so a dead broker was invisible.
 *  3. The sleep mode is stated at boot and never aliases deep sleep onto a light
 *     sleep call.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "adaptive_scheduler.h"
#include "change_detector.h"
#include "communication.h"
#include "config.h"
#include "policy_config.h"
#include "power_mgmt.h"
#include "sensor.h"

static const char *TAG = "main";

/* Log a duty-cycle + counter summary every N cycles. */
#define STATS_LOG_EVERY_CYCLES 10u

/* The state and comm enums must share the same ordering. */
_Static_assert((int)AS_STABLE == (int)COMM_STABLE, "state enum mismatch");
_Static_assert((int)AS_ACTIVE == (int)COMM_ACTIVE, "state enum mismatch");
_Static_assert((int)AS_ALERT == (int)COMM_ALERT, "state enum mismatch");
_Static_assert(CD_NUM_CHANNELS == AS_NUM_CHANNELS, "channel count mismatch");

/* Detector and scheduler live in static storage: `cd_t` alone is several
 * kilobytes, which would not fit on an app_main stack. */
static cd_config_t g_cd_cfg;
static as_config_t g_sched_cfg;
static cd_t g_detector;
static as_t g_scheduler;

static float g_values[CD_NUM_CHANNELS];
static bool  g_valid[CD_NUM_CHANNELS];

void app_main(void)
{
    ESP_LOGI(TAG, "AdaptiveSense node booting (device=%s)", CONFIG_AS_DEVICE_ID);
    ESP_LOGI(TAG, "sleep mode: %s", power_sleep_mode_name(power_sleep_mode()));
    ESP_LOGI(TAG, "BME280 measurement mode: forced (one conversion per sample)");

    policy_build_detector_config(&g_cd_cfg);
    policy_build_scheduler_config(&g_sched_cfg);

    if (sensor_init() != 0) {
        ESP_LOGE(TAG, "sensor init failed; every sample will be retried");
    }

    if (communication_start() != 0) {
        ESP_LOGW(TAG, "communication start failed; publishes will count as failures");
    }

    cd_init(&g_detector, &g_cd_cfg);
    as_init(&g_scheduler, &g_sched_cfg);
    if (g_detector.hist_truncated) {
        ESP_LOGE(TAG, "history ring overflowed: increase CD_MAX_SLOTS");
    }

    /* Seconds since boot, from the single documented time base. */
    double next_wake = (double)esp_timer_get_time() / 1e6;
    unsigned long cycle = 0;

    while (1) {
        cycle++;

        /* ---- SLEEP until the scheduled sample time ---------------------- */
        double now = (double)esp_timer_get_time() / 1e6;
        if (next_wake > now) {
            power_set_phase(PM_SLEEP);
            const double requested = next_wake - now;
            const double slept = power_sleep(requested);
            if (slept + 0.5 < requested) {
                ESP_LOGD(TAG, "sleep returned early: requested %.2fs, got %.2fs",
                         requested, slept);
            }
            now = (double)esp_timer_get_time() / 1e6;
        }
        const double t_sample = now;

        /* ---- READ SENSOR ------------------------------------------------ */
        power_set_phase(PM_SAMPLE);
        sensor_read_t reading;
        if (sensor_read(&reading) != 0) {
            ESP_LOGW(TAG, "sensor read failed at cycle %lu; retrying in %ds",
                     cycle, (int)CONFIG_AS_MIN_INTERVAL_S);
            next_wake = t_sample + (double)CONFIG_AS_MIN_INTERVAL_S;
            continue;
        }
        memcpy(g_valid, reading.valid, sizeof(g_valid));
        for (int i = 0; i < CD_NUM_CHANNELS; i++) {
            g_values[i] = reading.value[i];
        }

        /* ---- EVALUATE CHANGE -------------------------------------------- */
        bool event = false;
        const float score =
            cd_update(&g_detector, t_sample, g_values, g_valid, &event);

        /* ---- ADAPTIVE SCHEDULER ----------------------------------------- */
        as_decision_t decision;
        as_update(&g_scheduler, t_sample, g_values, g_valid, score, event,
                  &decision);

        /* ---- DECIDE UPLOAD / TRANSPORT ---------------------------------- */
        bool publish_success = false;
        if (decision.upload_requested) {
            power_set_phase(PM_TRANSMIT);
            publish_success =
                communication_publish(t_sample * 1000.0, g_values,
                                      (comm_state_t)decision.state,
                                      decision.interval_s,
                                      decision.detected_event) == 0;
        } else {
            power_set_phase(PM_ACTIVE);
        }

        ESP_LOGI(TAG,
                 "cycle=%lu t=%.1f state=%d interval=%.1fs score=%.2f "
                 "event=%d upload_requested=%d publish_success=%d "
                 "temp=%.2f hum=%.2f",
                 cycle, t_sample, (int)decision.state, decision.interval_s,
                 (double)score, (int)decision.detected_event,
                 (int)decision.upload_requested, (int)publish_success,
                 (double)g_values[0], (double)g_values[1]);

        /* ---- DETERMINE NEXT WAKE ---------------------------------------- */
        next_wake = t_sample + (double)decision.interval_s;

        if (cycle % STATS_LOG_EVERY_CYCLES == 0) {
            const power_stats_t *pw = power_get_stats();
            communication_log_stats();
            ESP_LOGI(TAG,
                     "duty cycle: sleeps=%lu light_ok=%lu light_failed=%lu "
                     "sleep_requested=%.1fs sleep_actual=%.1fs",
                     pw->sleep_calls, pw->light_sleep_ok, pw->light_sleep_failed,
                     pw->requested_s, pw->actual_s);
        }
    }
}
