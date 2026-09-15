/*
 * adaptive_scheduler.h — Adaptive Sampling Layer.
 *
 * Pure policy: given an instability score + event flag (produced by the
 * change-detection layer), it maintains the STABLE/ACTIVE/ALERT state machine,
 * selects the interval for the NEXT sample, and decides whether THIS sample
 * should be uploaded.
 *
 * The scheduler holds no reference to any sensor or communication driver, so
 * it can be unit-tested and reused under any power policy.
 */
#ifndef ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H
#define ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

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

    float stable_threshold;      /* multiples of noise floor */
    float active_threshold;
    float hysteresis_fraction;

    const float *ladders[AS_NUM_STATES]; /* per-state interval ladders */
    size_t       ladder_len[AS_NUM_STATES];

    /* upload policy */
    bool up_on_event;
    bool up_on_state_change;
    bool up_on_interval_change;
    float heartbeat_s;
    float delta_threshold;

    bool delta_channel_use[4];   /* which channels take part in delta report */
} as_config_t;

typedef struct {
    as_state_t state;
    float interval_s;            /* chosen interval for the NEXT sample */
    bool detected_event;         /* event active at this sample */
    bool upload;                 /* transmit this sample */
    float score;                 /* normalised instability score */
} as_decision_t;

typedef struct {
    const as_config_t *cfg;
    as_state_t state;
    float interval;
    int  ladder_pos[AS_NUM_STATES];

    /* upload / change bookkeeping */
    double last_upload_t;
    bool   has_uploaded;
    float  last_upload_values[4];

    as_state_t last_state;
    float      last_interval;
    bool       event_potential;  /* reserved for future per-sample events */
} as_t;

void as_init(as_t *s, const as_config_t *cfg);

/* Feed scored sample. caller supplies the measurement values (for delta
 * change-reporting), the instabilities across channels, the overall score, and
 * the event flag. timestamp is used for the heartbeat check and interval
 * selection is time-independent. */
void as_update(as_t *s, double timestamp,
               const float values[4],
               const float channel_scores[4],
               float score,
               bool event,
               as_decision_t *out);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_ADAPTIVE_SCHEDULER_H */