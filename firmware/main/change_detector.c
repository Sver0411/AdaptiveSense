/*
 * change_detector.c — implementation of the change/event detection layer.
 *
 * Mirrors simulator/adaptive.py:
 *   score_c = max( |v - ema_ref| / noise, std(window) / noise,
 *                  mean_roc(window) / noise )
 *   overall  = max over enabled channels
 * Events are debounced: a sustained score above event_threshold for at least
 * event_min_duration seconds latches the event bit.
 */
#include <math.h>
#include <string.h>

#include "change_detector.h"

/* nearest larger window in slots: window_s seconds at up to 1 sample / s.
 * Sizes are doubled for safety margin. */
#define SLOTS_FOR(window_s)  (2 * CD_VARIETY_WINDOW_S + 2)

static void push(cd_t *d, cd_channel_t ch, double t, float v)
{
    cd_sample_t *hist = d->hist[ch];
    size_t *len = &d->hist_len[ch];
    size_t cap = SLOTS_FOR(CD_VARIETY_WINDOW_S);
    if (*len < cap) {
        hist[*len].t = t;
        hist[*len].value = v;
        (*len)++;
    } else {
        /* shift left by one (ring kept time-ordered) */
        memmove(hist, hist + 1, (cap - 1) * sizeof(cd_sample_t));
        hist[cap - 1].t = t;
        hist[cap - 1].value = v;
    }
}

static void prune(cd_t *d, cd_channel_t ch, double now)
{
    cd_sample_t *hist = d->hist[ch];
    size_t *len = &d->hist_len[ch];
    size_t keep = 0;
    for (size_t i = 0; i < *len; i++) {
        if (hist[i].t >= now - CD_VARIETY_WINDOW_S) {
            if (keep != i) hist[keep] = hist[i];
            keep++;
        }
    }
    *len = keep;
}

static float ema_ref(cd_t *d, cd_channel_t ch, float value, double dt)
{
    if (!d->ema_valid[ch]) {
        d->ema[ch] = value;
        d->ema_valid[ch] = true;
        return value;
    }
    float ref = d->ema[ch];
    float alpha = (dt > 0.0f) ? (float)(dt / (dt + d->cfg->baseline_tau_s)) : 0.0f;
    d->ema[ch] = ref + alpha * (value - ref);
    return ref;
}

static float channel_score(cd_t *d, cd_channel_t ch, float value, float ref)
{
    const cd_sample_t *hist = d->hist[ch];
    size_t len = d->hist_len[ch];
    float noise = d->cfg->channels[ch].noise_floor;
    if (len < 1 || noise <= 0.0f) return 0.0f;

    /* deviation from EMA baseline (the reference BEFORE this sample) */
    float dev = fabsf(value - ref);

    /* standard deviation over the variety window */
    float std = 0.0f;
    if (len >= 2) {
        float mean = 0.0f;
        for (size_t i = 0; i < len; i++) mean += hist[i].value;
        mean /= (float)len;
        float var = 0.0f;
        for (size_t i = 0; i < len; i++) {
            float d_ = hist[i].value - mean;
            var += d_ * d_;
        }
        var /= (float)len;
        std = sqrtf(var > 0.0f ? var : 0.0f);
    }

    /* mean |delta/dt| over recent window (rate of change) */
    float roc = 0.0f;
    size_t roc_cnt = 0;
    for (size_t i = 1; i < len; i++) {
        float dt = (float)(hist[i].t - hist[i - 1].t);
        if (dt > 0.0f &&
            hist[i].t >= d->last_t - CD_ROC_WINDOW_S) {
            roc += fabsf(hist[i].value - hist[i - 1].value) / dt;
            roc_cnt++;
        }
    }
    if (roc_cnt > 0) roc /= (float)roc_cnt;

    float s_dev = dev / noise;
    float s_std = std / noise;
    float s_roc = roc / noise;
    float m = s_dev > s_std ? s_dev : s_std;
    return m > s_roc ? m : s_roc;
}

void cd_init(cd_t *d, const cd_config_t *cfg)
{
    memset(d, 0, sizeof(*d));
    d->cfg = cfg;
    d->event_potential_start = -1.0;
}

float cd_update(cd_t *d, double t,
                const float values[CD_NUM_CHANNELS],
                const bool valid[CD_NUM_CHANNELS],
                bool *event_out)
{
    if (event_out) *event_out = false;

    double dt = (d->last_t > 0.0) ? (t - d->last_t) : 0.0;
    d->last_t = t;

    /* ingest + advance EMA baseline (deviation is read against the OLD ema) */
    float refs[CD_NUM_CHANNELS];
    for (cd_channel_t c = CD_CH_TEMPERATURE; c < CD_NUM_CHANNELS; c++) {
        if (!valid[c]) {
            refs[c] = d->ema[c];
            continue;
        }
        push(d, c, t, values[c]);
        prune(d, c, t);
        refs[c] = ema_ref(d, c, values[c], dt);
    }

    /* compute overall score */
    float overall = 0.0f;
    for (cd_channel_t c = CD_CH_TEMPERATURE; c < CD_NUM_CHANNELS; c++) {
        if (!d->cfg->channels[c].use || !valid[c]) continue;
        float sc = channel_score(d, c, values[c], refs[c]);
        if (sc > overall) overall = sc;
    }

    /* event debounce */
    if (overall > d->cfg->event_threshold) {
        if (d->event_potential_start < 0.0) d->event_potential_start = t;
        if ((t - d->event_potential_start) >= d->cfg->event_min_duration_s) {
            d->event_active = true;
            if (event_out) *event_out = true;
        }
    } else {
        d->event_potential_start = -1.0;
        d->event_active = false;
    }

    return overall;
}