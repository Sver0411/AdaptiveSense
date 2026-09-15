/*
 * adaptive_scheduler.c — implementation of the adaptive sampling policy.
 *
 * Normative implementation of `docs/change_score_spec.md` sections 6-8, kept
 * structurally identical to `simulator/adaptive.py`:
 *
 *   state machine   escalation is immediate (a very large score goes
 *                   STABLE -> ALERT in one sample); retention uses the lower
 *                   hysteresis bound so a score inside the band cannot flip the
 *                   state every sample.
 *
 *   interval ladder each state has its own ladder; a rung is held for
 *                   `ladder_confirm[state]` consecutive evaluations before
 *                   advancing. STABLE ascends [20, 40, 60], ACTIVE descends
 *                   [15, 10, 5], ALERT is fixed at [5]. v0.1 used
 *                   active: [60, 30, 15, 5], i.e. entering ACTIVE after
 *                   observing change returned the MAXIMUM interval.
 *
 *   upload policy   first sample | event onset | state change | interval change
 *                   | heartbeat | normalized delta >= delta_threshold.
 */
#include "adaptive_scheduler.h"

#include <math.h>
#include <string.h>

static void reset_ladders(as_t *s)
{
    for (int i = 0; i < AS_NUM_STATES; i++) {
        s->ladder_pos[i] = 0;
        s->rung_count[i] = 0;
    }
}

static float advance_ladder(as_t *s)
{
    const as_config_t *cfg = s->cfg;
    const float *ladder = cfg->ladders[s->state];
    const size_t len = cfg->ladder_len[s->state];
    if (ladder == NULL || len == 0) {
        /* Defensive: a malformed configuration must not read out of bounds.
         * The config validator rejects empty ladders, so this is unreachable
         * in practice. */
        return cfg->default_interval;
    }

    int pos = s->ladder_pos[s->state];
    if (pos >= (int)len) {
        pos = (int)len - 1;
    }
    const float interval = ladder[(size_t)pos];

    s->rung_count[s->state]++;
    if (s->rung_count[s->state] >= cfg->ladder_confirm[s->state]) {
        s->ladder_pos[s->state] = (pos + 1 < (int)len) ? pos + 1 : pos;
        s->rung_count[s->state] = 0;
    }
    return interval;
}

/*
 * Spec section 6. Asymmetric on purpose:
 *
 *   escalation   immediate: a score above a state's entry bound reaches that
 *                state in a single sample, so STABLE can go straight to ALERT.
 *   de-escalation moves ONE level at a time, and only once the score has fallen
 *                below the current state's retention bound. The node is quick to
 *                react and cautious to relax: ALERT does not drop straight to
 *                STABLE (and to a 20 s interval) while change is still present.
 */
static as_state_t next_state(const as_config_t *cfg, as_state_t state, float score)
{
    const float h = cfg->hysteresis_fraction;
    const float stable_high = cfg->stable_threshold * (1.0f + h);
    const float stable_low = cfg->stable_threshold * (1.0f - h);
    const float active_high = cfg->active_threshold * (1.0f + h);
    const float active_low = cfg->active_threshold * (1.0f - h);

    if (score >= active_high) {
        return AS_ALERT;
    }
    if (state == AS_ALERT) {
        return (score >= active_low) ? AS_ALERT : AS_ACTIVE;
    }
    if (score >= stable_high) {
        return AS_ACTIVE;
    }
    if (state == AS_ACTIVE) {
        return (score >= stable_low) ? AS_ACTIVE : AS_STABLE;
    }
    return AS_STABLE;
}

void as_init(as_t *s, const as_config_t *cfg)
{
    memset(s, 0, sizeof(*s));
    s->cfg = cfg;
    s->state = AS_STABLE;
    s->interval = cfg->default_interval;
    s->last_interval = cfg->default_interval;
    s->last_upload_t = -1.0;
    s->has_uploaded = false;
    reset_ladders(s);
}

void as_update(as_t *s, double timestamp,
               const float values[AS_NUM_CHANNELS],
               const bool valid[AS_NUM_CHANNELS],
               float score,
               bool event,
               as_decision_t *out)
{
    if (out == NULL) {
        return;
    }
    const as_config_t *cfg = s->cfg;
    s->n_samples++;

    const bool event_onset = event && !s->prev_event_active;

    /* ---- state machine ---- */
    const as_state_t prev_state = s->state;
    s->state = next_state(cfg, prev_state, score);
    const bool state_changed = (s->state != prev_state);
    if (state_changed) {
        reset_ladders(s);
    }

    /* ---- interval for the next sample ---- */
    const float new_interval = advance_ladder(s);
    s->interval = new_interval;
    const bool interval_changed = fabsf(new_interval - s->last_interval) > 1e-3f;
    s->last_interval = new_interval;

    /* ---- upload policy (spec section 8) ---- */
    bool upload = false;

    if (cfg->up_first_sample && s->n_samples == 1) {
        upload = true;
    }
    if (!upload && cfg->up_on_event && event_onset) {
        upload = true;
    }
    if (!upload && cfg->up_on_state_change && state_changed) {
        upload = true;
    }
    if (!upload && cfg->up_on_interval_change && interval_changed) {
        upload = true;
    }
    if (!upload && cfg->heartbeat_s > 0.0f && s->has_uploaded &&
        (timestamp - s->last_upload_t) >= (double)cfg->heartbeat_s) {
        upload = true;
    }
    if (!upload && cfg->delta_threshold > 0.0f) {
        for (int c = 0; c < AS_NUM_CHANNELS; c++) {
            if (!cfg->delta_channel_use[c]) {
                continue;
            }
            if (valid != NULL && !valid[c]) {
                continue; /* mirrors "channel not present in the sample" */
            }
            const float noise = cfg->delta_noise_floor[c];
            if (noise <= 0.0f) {
                continue;
            }
            if (!s->has_uploaded) {
                continue;
            }
            /* Normalised delta, exactly as in simulator/adaptive.py. v0.1
             * compared the raw absolute delta against the same threshold, so
             * the constant meant "2 noise floors" offline and "2 degC / 2 lux"
             * on the device. */
            const float normalized =
                fabsf(values[c] - s->last_upload_values[c]) / noise;
            if (normalized >= cfg->delta_threshold) {
                upload = true;
                break;
            }
        }
    }

    if (upload) {
        memcpy(s->last_upload_values, values,
               sizeof(float) * (size_t)AS_NUM_CHANNELS);
        s->last_upload_t = timestamp;
        s->has_uploaded = true;
    }

    s->prev_event_active = event;

    out->state = s->state;
    out->interval_s = new_interval;
    out->detected_event = event;
    out->upload_requested = upload;
    out->score = score;
}
