/*
 * policy_config.c — see policy_config.h.
 *
 * Every value below is a CONFIG_AS_* macro from config.h, which in turn mirrors
 * experiments/experiment_config.yaml one-for-one
 * (scripts/check_config_parity.py verifies this in CI).
 */

#include "policy_config.h"

#include <string.h>

/* Includes the device configuration, falling back to the committed example in a
 * host-side build. See config_include.h. */
#include "config_include.h"

/* ------------------------------------------------------------------ */
/* Channel configuration (mirrors adaptive.channels in the YAML)       */
/* ------------------------------------------------------------------ */
static const cd_channel_cfg_t CHANNEL_CFG[CD_NUM_CHANNELS] = {
    { .use = true,                         .noise_floor = CONFIG_AS_NOISE_FLOOR_TEMP  },
    { .use = (bool)CONFIG_AS_USE_HUMIDITY, .noise_floor = CONFIG_AS_NOISE_FLOOR_HUM   },
    { .use = (bool)CONFIG_AS_USE_PRESSURE, .noise_floor = CONFIG_AS_NOISE_FLOOR_PRESS },
    { .use = (bool)CONFIG_AS_USE_LIGHT,    .noise_floor = CONFIG_AS_NOISE_FLOOR_LIGHT },
};

/* ------------------------------------------------------------------ */
/* Interval ladders (mirrors adaptive.ladders in the YAML)             */
/* ------------------------------------------------------------------ */
static const float LADDER_STABLE[] = CONFIG_AS_LADDER_STABLE;
static const float LADDER_ACTIVE[] = CONFIG_AS_LADDER_ACTIVE;
static const float LADDER_ALERT[]  = CONFIG_AS_LADDER_ALERT;

#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))

static const float *const LADDERS[AS_NUM_STATES] = {
    LADDER_STABLE, LADDER_ACTIVE, LADDER_ALERT
};
static const size_t LADDER_LENS[AS_NUM_STATES] = {
    ARRAY_LEN(LADDER_STABLE), ARRAY_LEN(LADDER_ACTIVE), ARRAY_LEN(LADDER_ALERT)
};
static const int LADDER_CONFIRM[AS_NUM_STATES] = {
    CONFIG_AS_LADDER_CONFIRM_STABLE,
    CONFIG_AS_LADDER_CONFIRM_ACTIVE,
    CONFIG_AS_LADDER_CONFIRM_ALERT,
};

static const float DELTA_NOISE_FLOOR[AS_NUM_CHANNELS] = {
    CONFIG_AS_NOISE_FLOOR_TEMP,
    CONFIG_AS_NOISE_FLOOR_HUM,
    CONFIG_AS_NOISE_FLOOR_PRESS,
    CONFIG_AS_NOISE_FLOOR_LIGHT,
};

void policy_build_detector_config(cd_config_t *out)
{
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));

    memcpy(out->channels, CHANNEL_CFG, sizeof(CHANNEL_CFG));

    out->variety_window_s = CONFIG_AS_VARIETY_WINDOW_S;
    out->roc_window_s = CONFIG_AS_ROC_WINDOW_S;
    out->baseline_tau_s = CONFIG_AS_BASELINE_TAU_S;
    out->min_interval_s = CONFIG_AS_MIN_INTERVAL_S;
    out->event_threshold = CONFIG_AS_EVENT_THRESHOLD;
    out->event_min_duration_s = CONFIG_AS_EVENT_MIN_DURATION_S;
}

void policy_build_scheduler_config(as_config_t *out)
{
    if (out == NULL) {
        return;
    }
    memset(out, 0, sizeof(*out));

    out->min_interval = CONFIG_AS_MIN_INTERVAL_S;
    out->default_interval = CONFIG_AS_DEFAULT_INTERVAL_S;
    out->max_interval = CONFIG_AS_MAX_INTERVAL_S;

    out->stable_threshold = CONFIG_AS_STABLE_THRESHOLD;
    out->active_threshold = CONFIG_AS_ACTIVE_THRESHOLD;
    out->hysteresis_fraction = CONFIG_AS_HYSTERESIS_FRACTION;

    memcpy(out->ladders, LADDERS, sizeof(LADDERS));
    memcpy(out->ladder_len, LADDER_LENS, sizeof(LADDER_LENS));
    memcpy(out->ladder_confirm, LADDER_CONFIRM, sizeof(LADDER_CONFIRM));

    out->up_first_sample = (bool)CONFIG_AS_UP_FIRST_SAMPLE;
    out->up_on_event = (bool)CONFIG_AS_UP_ON_EVENT;
    out->up_on_state_change = (bool)CONFIG_AS_UP_ON_STATE_CHANGE;
    out->up_on_interval_change = (bool)CONFIG_AS_UP_ON_INTERVAL_CHANGE;
    out->heartbeat_s = CONFIG_AS_UP_HEARTBEAT_S;
    out->delta_threshold = CONFIG_AS_UP_DELTA_THRESHOLD;

    /* Normalized delta: each participating channel is divided by its own noise
     * floor, mirroring simulator/adaptive.py (spec section 8). */
    out->delta_channel_use[0] = true;
    out->delta_channel_use[1] = (bool)CONFIG_AS_USE_HUMIDITY;
    out->delta_channel_use[2] = (bool)CONFIG_AS_USE_PRESSURE;
    out->delta_channel_use[3] = (bool)CONFIG_AS_USE_LIGHT;
    memcpy(out->delta_noise_floor, DELTA_NOISE_FLOOR, sizeof(DELTA_NOISE_FLOOR));
}
