/*
 * change_detector.h — Change / Event Detection Layer.
 *
 * Computes a per-channel "instability score" from a stream of measurements
 * and debounces a sustained EVENT flag. This is the change-aware core of
 * AdaptiveSense. It is fully decoupled from any sensor driver and from the
 * scheduling policy: it only turns a stream of (timestamp, value) into a
 * normalised score + event bit.
 *
 * The maths intentionally mirrors simulator/adaptive.py so offline replay and
 * on-device behaviour are comparable.
 */
#ifndef ADAPTIVESENSE_CHANGE_DETECTOR_H
#define ADAPTIVESENSE_CHANGE_DETECTOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Fixed window capacities (seconds). The variety window is the largest
 * window used, so slot counts are sized from it + the 1 Hz reference. */
#define CD_VARIETY_WINDOW_S 30
#define CD_ROC_WINDOW_S     10
#define CD_MAX_SLOTS        64   /* samples kept per channel (ring) */

typedef enum {
    CD_CH_TEMPERATURE = 0,
    CD_CH_HUMIDITY,
    CD_CH_PRESSURE,
    CD_CH_LIGHT,
    CD_NUM_CHANNELS
} cd_channel_t;

typedef struct {
    bool    use;              /* participate in the overall score    */
    float   noise_floor;      /* typical noise amplitude (units)     */
} cd_channel_cfg_t;

typedef struct {
    cd_channel_cfg_t channels[CD_NUM_CHANNELS];
    float baseline_tau_s;     /* EMA time constant for change baseline */
    float event_threshold;    /* score above which a potential event   */
    float event_min_duration_s; /* debounce: score must persist this long */
} cd_config_t;

/* One observed sample (time-sorted arrival). */
typedef struct {
    double t;
    float  value;
} cd_sample_t;

typedef struct {
    const cd_config_t *cfg;
    /* per-channel ring buffers of recent samples (time-ordered) */
    cd_sample_t hist[CD_NUM_CHANNELS][CD_MAX_SLOTS];
    size_t      hist_len[CD_NUM_CHANNELS];
    /* per-channel persistent EMA baseline */
    float       ema[CD_NUM_CHANNELS];
    bool        ema_valid[CD_NUM_CHANNELS];
    /* event debounce state */
    double      event_potential_start;   /* -1 when not tracking */
    bool        event_active;
    /* last seen timestamp, for ROC/EMA bookkeeping */
    double      last_t;
} cd_t;

void cd_init(cd_t *d, const cd_config_t *cfg);

/* Feed a full measurement vector. score_out receives the max normalised
 * score over enabled channels; event_out is set when a sustained event is
 * active at this sample. Returns the overall score. */
float cd_update(cd_t *d, double t,
                const float values[CD_NUM_CHANNELS],
                const bool valid[CD_NUM_CHANNELS],
                bool *event_out);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_CHANGE_DETECTOR_H */