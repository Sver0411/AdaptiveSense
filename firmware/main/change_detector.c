/*
 * change_detector.c — implementation of the change/event detection layer.
 *
 * Normative implementation of `docs/change_score_spec.md` sections 1-5, kept
 * line-for-line comparable with the Python implementation in
 * `simulator/scoring.py`:
 *
 *   baseline:  ref_i = ema_(i-1);  ema_i = ema_(i-1) + alpha * (x_i - ema_(i-1))
 *              alpha = dt / (dt + baseline_tau_s)
 *   std:       population standard deviation over the variety window
 *   roc:       MEAN |dx/dt| over the roc window
 *   score_c:   max(dev, std, roc) / noise_floor_c
 *   score:     max over participating channels
 *   event:     score > event_threshold held for >= event_min_duration_s
 *
 * v0.1 used a max-reducer for the rate of change here, a mean over the variety
 * window in `simulator/adaptive.py`, and a max over the variety window in
 * `simulator/events.py` — three different definitions of the same score.
 */
#include "change_detector.h"

#include <math.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* history ring                                                        */
/* ------------------------------------------------------------------ */
static void hist_push(cd_t *d, int ch, double t, float value)
{
    cd_sample_t *hist = d->hist[ch];
    size_t *len = &d->hist_len[ch];
    const size_t cap = d->hist_cap[ch];

    if (*len < cap) {
        hist[*len].t = t;
        hist[*len].value = value;
        (*len)++;
        return;
    }

    /* Window overflow: drop the oldest sample. With the documented
     * configuration this never happens (7 slots are needed out of 64), but the
     * behaviour is defined and observable via `hist_truncated`. */
    memmove(hist, hist + 1, (cap - 1) * sizeof(cd_sample_t));
    hist[cap - 1].t = t;
    hist[cap - 1].value = value;
    d->hist_truncated = true;
}

static void hist_prune(cd_t *d, int ch, double now)
{
    cd_sample_t *hist = d->hist[ch];
    size_t *len = &d->hist_len[ch];
    const double limit = now - (double)d->cfg->variety_window_s;

    size_t keep = 0;
    for (size_t i = 0; i < *len; i++) {
        if (hist[i].t >= limit) {
            if (keep != i) {
                hist[keep] = hist[i];
            }
            keep++;
        }
    }
    *len = keep;
}

/* ------------------------------------------------------------------ */
/* indicators                                                          */
/* ------------------------------------------------------------------ */
static float hist_std(const cd_t *d, int ch)
{
    const size_t len = d->hist_len[ch];
    if (len < 2) {
        return 0.0f;
    }
    const cd_sample_t *hist = d->hist[ch];

    double sum = 0.0;
    for (size_t i = 0; i < len; i++) {
        sum += (double)hist[i].value;
    }
    const double mean = sum / (double)len;

    double var = 0.0;
    for (size_t i = 0; i < len; i++) {
        const double dv = (double)hist[i].value - mean;
        var += dv * dv;
    }
    var /= (double)len;
    return (float)sqrt(var > 0.0 ? var : 0.0);
}

/*
 * Mean |dx/dt| over the ROC window. Each adjacent pair contributes exactly
 * once; the current sample is the right-hand element of the last pair.
 */
static float hist_roc(const cd_t *d, int ch, double now)
{
    const size_t len = d->hist_len[ch];
    if (len < 2) {
        return 0.0f;
    }
    const cd_sample_t *hist = d->hist[ch];
    const double limit = now - (double)d->cfg->roc_window_s;

    double sum = 0.0;
    size_t count = 0;
    for (size_t i = 1; i < len; i++) {
        const double dt = hist[i].t - hist[i - 1].t;
        if (dt > 0.0 && hist[i].t >= limit) {
            sum += fabs((double)hist[i].value - (double)hist[i - 1].value) / dt;
            count++;
        }
    }
    if (count == 0) {
        return 0.0f;
    }
    return (float)(sum / (double)count);
}

static float ema_update(cd_t *d, int ch, double t, float value)
{
    if (!d->ema_valid[ch]) {
        d->ema[ch] = value;
        d->ema_valid[ch] = true;
        d->last_t[ch] = t;
        d->has_last[ch] = true;
        return value;
    }

    const double dt = (t > d->last_t[ch]) ? (t - d->last_t[ch]) : 0.0;
    const double tau = (double)d->cfg->baseline_tau_s;
    const double alpha = (dt > 0.0 && tau > 0.0) ? dt / (dt + tau) : 0.0;

    const float ref = d->ema[ch]; /* the reference BEFORE this sample */
    d->ema[ch] = (float)((double)ref + alpha * ((double)value - (double)ref));
    d->last_t[ch] = t;
    d->has_last[ch] = true;
    return ref;
}

static float channel_score(const cd_t *d, int ch, float value, float ref)
{
    const float noise = d->cfg->channels[ch].noise_floor;
    if (noise <= 0.0f) {
        return 0.0f;
    }
    const float dev = fabsf(value - ref);
    const float std = hist_std(d, ch);
    const float roc = hist_roc(d, ch, d->last_t[ch]);

    float m = (dev > std) ? dev : std;
    if (roc > m) {
        m = roc;
    }
    return m / noise;
}

/* ------------------------------------------------------------------ */
/* public API                                                          */
/* ------------------------------------------------------------------ */
void cd_init(cd_t *d, const cd_config_t *cfg)
{
    memset(d, 0, sizeof(*d));
    d->cfg = cfg;
    d->event_potential_start = -1.0;

    /*
     * Size the history ring from the configuration instead of guessing:
     * within a variety window of `variety_window_s` at the shortest permitted
     * interval there can be at most
     *     floor(variety_window_s / min_interval_s) + 1
     * samples. If that exceeds CD_MAX_SLOTS the ring is capped and
     * `hist_truncated` is raised on the first overflow, so the condition is
     * observable rather than silent.
     */
    size_t needed = CD_MAX_SLOTS;
    if (cfg->min_interval_s > 0.0f && cfg->variety_window_s > 0.0f) {
        needed = (size_t)(cfg->variety_window_s / cfg->min_interval_s) + 2u;
    }
    if (needed > CD_MAX_SLOTS) {
        needed = CD_MAX_SLOTS;
    }
    for (int ch = 0; ch < CD_NUM_CHANNELS; ch++) {
        d->hist_cap[ch] = needed;
    }
}

float cd_update(cd_t *d, double t,
                const float values[CD_NUM_CHANNELS],
                const bool valid[CD_NUM_CHANNELS],
                bool *event_out)
{
    if (event_out != NULL) {
        *event_out = false;
    }

    /* 1. ingest + advance the baseline, remembering the reference used */
    float refs[CD_NUM_CHANNELS];
    for (int ch = 0; ch < CD_NUM_CHANNELS; ch++) {
        if (!valid[ch]) {
            refs[ch] = d->ema[ch];
            continue;
        }
        refs[ch] = ema_update(d, ch, t, values[ch]);
        hist_push(d, ch, t, values[ch]);
        hist_prune(d, ch, t);
    }

    /* 2. overall score = max over participating channels */
    float overall = 0.0f;
    for (int ch = 0; ch < CD_NUM_CHANNELS; ch++) {
        if (!d->cfg->channels[ch].use || !valid[ch]) {
            continue;
        }
        const float sc = channel_score(d, ch, values[ch], refs[ch]);
        if (sc > overall) {
            overall = sc;
        }
    }

    /* 3. event debounce */
    if (overall > d->cfg->event_threshold) {
        if (d->event_potential_start < 0.0) {
            d->event_potential_start = t;
        }
        d->event_active =
            (t - d->event_potential_start) >= (double)d->cfg->event_min_duration_s;
    } else {
        d->event_potential_start = -1.0;
        d->event_active = false;
    }

    if (event_out != NULL) {
        *event_out = d->event_active;
    }
    return overall;
}
