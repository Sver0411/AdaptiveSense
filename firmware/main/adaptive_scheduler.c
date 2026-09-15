/*
 * adaptive_scheduler.c — implementation of the adaptive sampling policy.
 *
 * Mirrors simulator/adaptive.py:
 *   - relative hysteresis on each boundary of the state machine,
 *   - per-state interval ladders (STABLE ascends, ACTIVE descends, ALERT fixed),
 *   - upload decision = event | state change | interval change | heartbeat |
 *     delta threshold.
 */
#include <math.h>
#include <string.h>

#include "adaptive_scheduler.h"

static void reset_ladders(as_t *s)
{
    for (int i = 0; i < AS_NUM_STATES; i++) s->ladder_pos[i] = 0;
}

static float advance_ladder(as_t *s)
{
    const as_config_t *cfg = s->cfg;
    const float *ladder = cfg->ladders[s->state];
    size_t len = cfg->ladder_len[s->state];
    int pos = s->ladder_pos[s->state];
    if (pos >= (int)len) pos = (int)len - 1;
    float inter = ladder[(size_t)pos];
    s->ladder_pos[s->state] = pos + 1;
    return inter;
}

static void apply_state_machine(as_t *s, float score)
{
    const as_config_t *cfg = s->cfg;
    float h = cfg->hysteresis_fraction;
    float stable_high = cfg->stable_threshold * (1.0f + h);
    float stable_low  = cfg->stable_threshold * (1.0f - h);
    float active_high = cfg->active_threshold * (1.0f + h);
    float active_low  = cfg->active_threshold * (1.0f - h);

    switch (s->state) {
    case AS_STABLE:
        if (score > stable_high) s->state = AS_ACTIVE;
        break;
    case AS_ACTIVE:
        if (score < stable_low) {
            s->state = AS_STABLE;
        } else if (score > active_high) {
            s->state = AS_ALERT;
        }
        break;
    case AS_ALERT:
        if (score < active_low) s->state = AS_ACTIVE;
        break;
    default:
        s->state = AS_STABLE;
        break;
    }
}

void as_init(as_t *s, const as_config_t *cfg)
{
    memset(s, 0, sizeof(*s));
    s->cfg = cfg;
    s->state = AS_STABLE;
    s->interval = cfg->default_interval;
    s->last_interval = cfg->default_interval;
    s->last_state = AS_STABLE;
    reset_ladders(s);
    s->last_upload_t = -1.0;
    s->has_uploaded = false;
}

void as_update(as_t *s, double timestamp,
               const float values[4],
               const float channel_scores[4],
               float score,
               bool event,
               as_decision_t *out)
{
    (void)channel_scores;

    /* state machine */
    as_state_t prev_state = s->state;
    apply_state_machine(s, score);
    bool state_changed = (s->state != prev_state);
    if (state_changed) reset_ladders(s);

    /* pick interval for the next sample */
    float new_interval = advance_ladder(s);
    s->interval = new_interval;
    bool interval_changed = fabsf(new_interval - s->last_interval) > 1e-3f;
    s->last_interval = new_interval;

    /* upload decision */
    bool upload = false;
    const as_config_t *cfg = s->cfg;
    if (cfg->up_on_event && event) upload = true;

    as_state_t new_state = s->state;
    if (!upload && cfg->up_on_state_change && state_changed) upload = true;
    if (!upload && cfg->up_on_interval_change && interval_changed) upload = true;

    /* heartbeat guarantee */
    if (!upload && cfg->heartbeat_s > 0.0f && s->has_uploaded) {
        if ((timestamp - s->last_upload_t) >= cfg->heartbeat_s) upload = true;
    }

    /* change-driven delta report */
    if (!upload && cfg->delta_threshold > 0.0f) {
        for (int c = 0; c < 4; c++) {
            if (!cfg->delta_channel_use[c]) continue;
            if (s->has_uploaded) {
                float nd = fabsf(values[c] - s->last_upload_values[c]);
                if (nd >= cfg->delta_threshold) { upload = true; break; }
            }
        }
    }

    if (upload) {
        memcpy(s->last_upload_values, values, sizeof(float) * 4);
        s->last_upload_t = timestamp;
        s->has_uploaded = true;
    }

    s->last_state = s->state;
    s->event_potential = event;

    out->state = new_state;
    out->interval_s = new_interval;
    out->detected_event = event;
    out->upload = upload;
    out->score = score;
}