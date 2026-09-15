/*
 * adaptive_scheduler.h — Adaptive Sampling Layer.
 *
 * Pure policy: given an instability score and an event flag (produced by the
 * change-detection layer) it maintains the STABLE / ACTIVE / ALERT state
 * machine, selects the interval for the NEXT sample, and decides whether THIS
 * sample should be uploaded.
 *
 * Normative implementation of `docs/change_score_spec.md` sections 6-8. The
 * Python counterpart is `simulator/adaptive.py`.
 *
 * The scheduler holds no reference to any sensor, radio or timing driver, so it
 * can be unit-tested on the host and reused under any power policy.
 */
#ifndef ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H
#define ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AS_NUM_CHANNELS 4

typedef enum {
    AS_STABLE = 0,
    AS_ACTIVE,
    AS_ALERT,
    AS_NUM_STATES
} as_state_t;

typedef struct {
    float min_interval;
    float default_interval;
    float max_interval;

    float stable_threshold;      /* multiples of the noise floor */
    float active_threshold;
    float hysteresis_fraction;   /* relative band on each boundary */

    /* Per-state interval ladders and how many consecutive evaluations are
     * spent on a rung before advancing to the next one. */
    const float *ladders[AS_NUM_STATES];
    size_t       ladder_len[AS_NUM_STATES];
    int          ladder_confirm[AS_NUM_STATES];

    /* upload policy */
    bool  up_first_sample;
    bool  up_on_event;
    bool  up_on_state_change;
    bool  up_on_interval_change;
    float heartbeat_s;
    float delta_threshold;

    /* normalized-delta reporting: per channel, which participate and the
     * noise floor each delta is divided by (spec section 8). */
    bool  delta_channel_use[AS_NUM_CHANNELS];
    float delta_noise_floor[AS_NUM_CHANNELS];
} as_config_t;

typedef struct {
    as_state_t state;
    float interval_s;       /* chosen interval for the NEXT sample */
    bool  detected_event;   /* event active at this sample */
    bool  upload_requested; /* this sample should be transmitted */
    float score;            /* normalised instability score */
} as_decision_t;

typedef struct {
    const as_config_t *cfg;
    as_state_t state;
    float interval;

    int ladder_pos[AS_NUM_STATES];
    int rung_count[AS_NUM_STATES];

    unsigned long n_samples;

    double last_upload_t;
    bool   has_uploaded;
    float  last_upload_values[AS_NUM_CHANNELS];

    float last_interval;
    bool  prev_event_active;
} as_t;

void as_init(as_t *s, const as_config_t *cfg);

/*
 * Feed one scored sample.
 *
 * `values` / `valid` supply the channel readings used by the normalized-delta
 * upload rule; a channel with `valid[ch] == false` is skipped, which mirrors the
 * Python policy's "channel not present in the sample" condition.
 *
 * `score` and `event` come from `cd_update()`. `timestamp` is used for the
 * heartbeat check; interval selection itself is time-independent.
 */
void as_update(as_t *s, double timestamp,
               const float values[AS_NUM_CHANNELS],
               const bool valid[AS_NUM_CHANNELS],
               float score,
               bool event,
               as_decision_t *out);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H */
