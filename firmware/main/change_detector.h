/*
 * change_detector.h — Change / Event Detection Layer.
 *
 * Normative implementation of `docs/change_score_spec.md` sections 1-5. The
 * Python counterpart is `simulator/scoring.py`; `tests/test_parity_python_c.py`
 * compiles this file for the host and compares the two on a shared fixture.
 *
 * This layer turns a stream of (timestamp, value-vector) into a normalised
 * instability score plus a debounced event bit. It is pure computation: it
 * holds no reference to any sensor, radio or timing driver, and it is the same
 * code that runs on the device and in the host parity test.
 *
 * Every window and threshold comes from the configuration. v0.1 hardcoded
 * `CD_VARIETY_WINDOW_S` and `CD_ROC_WINDOW_S` in this header, which is how the
 * ROC window silently drifted away from the YAML value.
 */
#ifndef ADAPTIVESENSE_CHANGE_DETECTOR_H
#define ADAPTIVESENSE_CHANGE_DETECTOR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CD_NUM_CHANNELS 4

/*
 * Per-channel history capacity, in samples.
 *
 * The number of samples inside the variety window is at most
 * floor(variety_window_s / min_interval) + 1; with the documented defaults
 * (30 s window, 5 s minimum interval) that is 7. 64 leaves a wide margin and
 * still keeps `cd_t` small enough to live in static storage.
 */
#define CD_MAX_SLOTS 64

typedef enum {
    CD_CH_TEMPERATURE = 0,
    CD_CH_HUMIDITY,
    CD_CH_PRESSURE,
    CD_CH_LIGHT
} cd_channel_t;

typedef struct {
    bool  use;         /* participate in the overall score */
    float noise_floor; /* typical noise amplitude, same units as the channel */
} cd_channel_cfg_t;

typedef struct {
    cd_channel_cfg_t channels[CD_NUM_CHANNELS];

    float variety_window_s;     /* window for the rolling standard deviation */
    float roc_window_s;         /* window for the mean absolute rate of change */
    float baseline_tau_s;       /* EMA time constant for the change baseline */

    /*
     * Shortest interval the scheduler may select. Only used to size the
     * history ring: the variety window holds at most
     * floor(variety_window_s / min_interval_s) + 1 samples.
     */
    float min_interval_s;

    float event_threshold;      /* score above which a potential event starts */
    float event_min_duration_s; /* debounce duration */
} cd_config_t;

typedef struct {
    double t;
    float  value;
} cd_sample_t;

typedef struct {
    const cd_config_t *cfg;

    cd_sample_t hist[CD_NUM_CHANNELS][CD_MAX_SLOTS];
    size_t      hist_len[CD_NUM_CHANNELS];
    size_t      hist_cap[CD_NUM_CHANNELS]; /* effective cap, <= CD_MAX_SLOTS */

    float  ema[CD_NUM_CHANNELS];
    bool   ema_valid[CD_NUM_CHANNELS];
    double last_t[CD_NUM_CHANNELS];
    bool   has_last[CD_NUM_CHANNELS];

    double event_potential_start; /* < 0 when not tracking */
    bool   event_active;
    bool   hist_truncated;        /* set if the window ever needed more slots */
} cd_t;

/* Initialise the detector with a validated configuration. */
void cd_init(cd_t *d, const cd_config_t *cfg);

/*
 * Feed one measurement vector.
 *
 * `values[c]` is only meaningful where `valid[c]` is true. Returns the overall
 * normalised score (max over participating channels) and sets `*event_out` when
 * a sustained event is active at this sample. `event_out` may be NULL.
 */
float cd_update(cd_t *d, double t,
                const float values[CD_NUM_CHANNELS],
                const bool valid[CD_NUM_CHANNELS],
                bool *event_out);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_CHANGE_DETECTOR_H */
