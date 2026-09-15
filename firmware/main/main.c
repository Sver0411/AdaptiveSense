/*
 * main.c — AdaptiveSense node main loop.
 *
 * Documented duty cycle (one iteration):
 *
 *   IDLE until the scheduled sample time   (vTaskDelay; the ESP-IDF power manager
 *                                           may enter light sleep while idle)
 *   -> READ SENSOR            (forced-mode BME280 conversion)
 *   -> EVALUATE CHANGE        (change_detector: score + debounced event)
 *   -> ADAPTIVE SCHEDULER     (state, next interval, upload decision)
 *   -> PUBLISH if requested   (and record whether the MQTT client took it)
 *   -> DETERMINE NEXT WAKE
 *
 * The layers (sensor / change detection / adaptive scheduler / communication /
 * power management) are wired here and never know about each other directly. The
 * configuration is built by policy_config.c so that the device and the host-side
 * parity test run the identical policy configuration.
 *
 * Three behaviours are deliberate and are what an earlier revision got wrong:
 *
 *  1. A failed sensor read never becomes a sample. The detector's EMA baseline
 *     would be poisoned by zeros, so the node retries after `min_interval`
 *     instead. If the sensor has never come up at all, the supervisor limits
 *     re-initialisation to one attempt per `min_interval` — the node recovers by
 *     itself instead of failing forever.
 *  2. `upload_requested` (the policy decision) and `publish_call_ok` (whether the
 *     MQTT client accepted the request) are logged and counted separately.
 *  3. Sleeping goes through the ESP-IDF power manager rather than a direct
 *     `esp_light_sleep_start()`, so the Wi-Fi driver participates in the
 *     sleep decision. See power_mgmt.h.
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
#include "config_include.h"
#include "policy_config.h"
#include "power_mgmt.h"
#include "sensor.h"
#include "sensor_supervisor.h"

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

    /* Power management first: arm automatic light sleep before anything starts
     * acquiring PM locks, so the Wi-Fi driver sees the final configuration. */
    if (power_init() != 0) {
        ESP_LOGW(TAG, "power management not configured; the node will stay awake "
                      "between samples");
    }

    policy_build_detector_config(&g_cd_cfg);
    policy_build_scheduler_config(&g_sched_cfg);

    /* Sensor bring-up is retried, not attempted once. */
    sensor_supervisor_t sensor_sup;
    sensor_sup_init(&sensor_sup, CONFIG_AS_MIN_INTERVAL_S,
                    (double)esp_timer_get_time() / 1e6);

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

        /* ---- IDLE until the scheduled sample time ----------------------- */
        double now = (double)esp_timer_get_time() / 1e6;
        if (next_wake > now) {
            power_set_phase(PM_SLEEP);
            power_sleep(next_wake - now);
            now = (double)esp_timer_get_time() / 1e6;
        }
        const double t_sample = now;

        /* ---- SENSOR: (re)initialise if it has never come up ------------- */
        if (!sensor_sup_ready(&sensor_sup) &&
            sensor_sup_should_attempt(&sensor_sup, t_sample)) {
            const bool ok = (sensor_init() == 0);
            sensor_sup_note_attempt(&sensor_sup, t_sample, ok);
            if (ok) {
                ESP_LOGI(TAG, "sensor initialised after %u attempt(s)",
                         sensor_sup.init_attempts);
            } else {
                ESP_LOGW(TAG, "sensor init failed (%u attempt(s)); retrying no "
                              "sooner than %ds from now",
                         sensor_sup.init_attempts, (int)CONFIG_AS_MIN_INTERVAL_S);
            }
        }

        /* ---- READ SENSOR ------------------------------------------------ */
        power_set_phase(PM_SAMPLE);
        sensor_read_t reading;
        if (!sensor_sup_ready(&sensor_sup) || sensor_read(&reading) != 0) {
            ESP_LOGW(TAG, "no usable reading at cycle %lu (sensor %s); next "
                          "attempt in %ds",
                     cycle, sensor_sup_state_name(&sensor_sup),
                     (int)CONFIG_AS_MIN_INTERVAL_S);
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
        bool publish_call_ok = false;
        if (decision.upload_requested) {
            power_set_phase(PM_TRANSMIT);
            publish_call_ok =
                communication_publish(t_sample * 1000.0, g_values, g_valid,
                                      (comm_state_t)decision.state,
                                      decision.interval_s,
                                      decision.detected_event) == 0;
        } else {
            power_set_phase(PM_ACTIVE);
        }

        ESP_LOGI(TAG,
                 "cycle=%lu t=%.1f state=%d interval=%.1fs score=%.2f "
                 "event=%d upload_requested=%d publish_call_ok=%d "
                 "temp=%.2f hum=%.2f",
                 cycle, t_sample, (int)decision.state, decision.interval_s,
                 (double)score, (int)decision.detected_event,
                 (int)decision.upload_requested, (int)publish_call_ok,
                 (double)g_values[0], (double)g_values[1]);

        /* ---- DETERMINE NEXT WAKE ---------------------------------------- */
        next_wake = t_sample + (double)decision.interval_s;

        if (cycle % STATS_LOG_EVERY_CYCLES == 0) {
            const power_stats_t *pw = power_get_stats();
            communication_log_stats();
            ESP_LOGI(TAG,
                     "duty cycle: idle_requests=%lu scheduled_idle=%.1fs "
                     "light_sleep_entries=%lu light_sleep=%.1fs",
                     pw->sleep_requests, pw->scheduled_idle_s,
                     pw->light_sleep_entries, pw->light_sleep_s);
        }
    }
}
