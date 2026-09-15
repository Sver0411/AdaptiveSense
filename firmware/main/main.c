/*
 * main.c — AdaptiveSense node main loop.
 *
 * Enforces the documented duty cycle:
 *
 *   WAKE
 *   -> READ SENSOR
 *   -> EVALUATE CHANGE (change_detector) / decide event
 *   -> ADAPTIVE SCHEDULER (state, interval, upload decision)
 *   -> DECIDE UPLOAD (publish MQTT if upload)
 *   -> DETERMINE NEXT INTERVAL
 *   -> SLEEP for the remaining time until the next sample
 *
 * The layers (sensor / change detection / adaptive scheduler / communication /
 * power management) are wired here and never know about each other directly.
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
#include "power_mgmt.h"
#include "sensor.h"

static const char *TAG = "main";

/* ------------------------------------------------------------------ */
/* Channel configuration (mirrors experiments/experiment_config.yaml)  */
/* ------------------------------------------------------------------ */
static const cd_channel_cfg_t CHANNEL_CFG[CD_NUM_CHANNELS] = {
    { .use = true,  .noise_floor = CONFIG_AS_NOISE_FLOOR_TEMP  },
    { .use = (bool)CONFIG_AS_USE_HUMIDITY,  .noise_floor = CONFIG_AS_NOISE_FLOOR_HUM    },
    { .use = (bool)CONFIG_AS_USE_PRESSURE,  .noise_floor = CONFIG_AS_NOISE_FLOOR_PRESS  },
    { .use = (bool)CONFIG_AS_USE_LIGHT,     .noise_floor = CONFIG_AS_NOISE_FLOOR_LIGHT  },
};

/* Interval ladders, indexed by as_state_t. */
static const float LADDER_STABLE[3] = CONFIG_AS_LADDER_STABLE;
static const float LADDER_ACTIVE[4] = CONFIG_AS_LADDER_ACTIVE;
static const float LADDER_ALERT[1]  = CONFIG_AS_LADDER_ALERT;

static const float *LADDERS[AS_NUM_STATES] = {
    LADDER_STABLE, LADDER_ACTIVE, LADDER_ALERT
};
static const size_t LADDER_LENS[AS_NUM_STATES] = { 3, 4, 1 };

static as_config_t g_sched_cfg;
static cd_config_t g_cd_cfg;
static float g_values[CD_NUM_CHANNELS];
static bool  g_valid[CD_NUM_CHANNELS];

/* Map sensor channels -> change-detector channels (both 0..3 in same order). */
static cd_channel_t to_cd[CD_NUM_CHANNELS] = {
    CD_CH_TEMPERATURE, CD_CH_HUMIDITY, CD_CH_PRESSURE, CD_CH_LIGHT
};

static void build_configs(void)
{
    g_sched_cfg.min_interval = CONFIG_AS_MIN_INTERVAL_S;
    g_sched_cfg.default_interval = CONFIG_AS_DEFAULT_INTERVAL_S;
    g_sched_cfg.max_interval = CONFIG_AS_MAX_INTERVAL_S;
    g_sched_cfg.stable_threshold = CONFIG_AS_STABLE_THRESHOLD;
    g_sched_cfg.active_threshold = CONFIG_AS_ACTIVE_THRESHOLD;
    g_sched_cfg.hysteresis_fraction = CONFIG_AS_HYSTERESIS_FRACTION;
    memcpy(g_sched_cfg.ladders, LADDERS, sizeof(LADDERS));
    memcpy(g_sched_cfg.ladder_len, LADDER_LENS, sizeof(LADDER_LENS));
    g_sched_cfg.up_on_event = (bool)CONFIG_AS_UP_ON_EVENT;
    g_sched_cfg.up_on_state_change = (bool)CONFIG_AS_UP_ON_STATE_CHANGE;
    g_sched_cfg.up_on_interval_change = (bool)CONFIG_AS_UP_ON_INTERVAL_CHANGE;
    g_sched_cfg.heartbeat_s = CONFIG_AS_UP_HEARTBEAT_S;
    g_sched_cfg.delta_threshold = CONFIG_AS_UP_DELTA_THRESHOLD;
    g_sched_cfg.delta_channel_use[0] = true;
    g_sched_cfg.delta_channel_use[1] = (bool)CONFIG_AS_USE_HUMIDITY;
    g_sched_cfg.delta_channel_use[2] = (bool)CONFIG_AS_USE_PRESSURE;
    g_sched_cfg.delta_channel_use[3] = (bool)CONFIG_AS_USE_LIGHT;

    memcpy(g_cd_cfg.channels, CHANNEL_CFG, sizeof(CHANNEL_CFG));
    g_cd_cfg.baseline_tau_s = CONFIG_AS_BASELINE_TAU_S;
    g_cd_cfg.event_threshold = CONFIG_AS_EVENT_THRESHOLD;
    g_cd_cfg.event_min_duration_s = CONFIG_AS_EVENT_MIN_DURATION_S;
}

void app_main(void)
{
    ESP_LOGI(TAG, "AdaptiveSense node booting (device=%s)", CONFIG_AS_DEVICE_ID);

    build_configs();

    if (sensor_init() != 0) {
        ESP_LOGE(TAG, "sensor init failed; continuing with mock data");
    }

    if (communication_start() != 0) {
        ESP_LOGW(TAG, "communication start failed; will retry publishing");
    }

    cd_t detector;
    cd_init(&detector, &g_cd_cfg);

    as_t scheduler;
    as_init(&scheduler, &g_sched_cfg);

    /* reference clock in seconds since boot */
    double now = (double)esp_timer_get_time() / 1e6;
    double next_wake = now;
    uint32_t cycle = 0;

    while (1) {
        cycle++;

        now = (double)esp_timer_get_time() / 1e6;
        if (now < next_wake - 0.2) {
            /* arrived early; sleep for the remainder */
            double remaining = next_wake - now;
            power_set_phase(PM_SLEEP);
            double slept = power_sleep(remaining);
            now += slept;
        }

        /* ---- WAKE: sample the sensor -------------------------------- */
        power_set_phase(PM_SAMPLE);
        sensor_read_t sr;
        if (sensor_read(&sr) != 0) {
            ESP_LOGW(TAG, "sensor read failed at cycle %lu", (unsigned long)cycle);
        }
        memcpy(g_valid, sr.valid, sizeof(g_valid));
        for (int i = 0; i < CD_NUM_CHANNELS; i++) {
            g_values[i] = sr.value[i];
        }

        /* ---- EVALUATE CHANGE ---------------------------------------- */
        now = (double)esp_timer_get_time() / 1e6;
        bool event = false;
        float score = cd_update(&detector, now, g_values, g_valid, &event);

        /* ---- ADAPTIVE SCHEDULER -------------------------------------- */
        as_decision_t dec;
        as_update(&scheduler, now, g_values, NULL, score, event, &dec);

        /* ---- DECIDE UPLOAD ------------------------------------------- */
        if (dec.upload) {
            power_set_phase(PM_TRANSMIT);
            communication_publish(now * 1000.0, g_values,
                                  (comm_state_t)dec.state,
                                  dec.interval_s, dec.detected_event);
        } else {
            power_set_phase(PM_ACTIVE);
        }

        ESP_LOGD(TAG, "cycle=%lu t=%.1f state=%d interval=%.1fs score=%.2f "
                      "upload=%d event=%d temp=%.2f hum=%.2f",
                 (unsigned long)cycle, now, (int)dec.state, dec.interval_s,
                 (double)score, dec.upload, dec.detected_event,
                 (double)g_values[0], (double)g_values[1]);

        /* ---- DETERMINE NEXT INTERVAL, then SLEEP ----------------------- */
        next_wake = now + dec.interval_s;
    }
}