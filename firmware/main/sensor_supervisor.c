/*
 * sensor_supervisor.c — see sensor_supervisor.h.
 *
 * Pure C99, no ESP-IDF dependency, host-testable.
 */

#include "sensor_supervisor.h"

#include <string.h>

void sensor_sup_init(sensor_supervisor_t *s, double retry_interval_s, double now)
{
    if (s == NULL) {
        return;
    }
    memset(s, 0, sizeof(*s));
    s->retry_interval_s = (retry_interval_s > 0.0) ? retry_interval_s : 0.0;
    /* Allow the first attempt immediately: a node that cannot read its sensor
     * has nothing useful to do, so there is no reason to wait. */
    s->next_attempt_t = now;
}

bool sensor_sup_ready(const sensor_supervisor_t *s)
{
    return (s != NULL) && s->initialized;
}

bool sensor_sup_should_attempt(const sensor_supervisor_t *s, double now)
{
    if (s == NULL) {
        return false;
    }
    if (s->initialized) {
        return false; /* already up: no reason to re-init */
    }
    if (!s->attempted) {
        return true; /* first attempt */
    }
    return now >= s->next_attempt_t;
}

void sensor_sup_note_attempt(sensor_supervisor_t *s, double now, bool ok)
{
    if (s == NULL) {
        return;
    }
    s->attempted = true;
    s->init_attempts++;
    if (ok) {
        s->initialized = true;
        return;
    }
    s->init_failures++;
    s->next_attempt_t = now + s->retry_interval_s;
}

const char *sensor_sup_state_name(const sensor_supervisor_t *s)
{
    if (s == NULL) {
        return "unknown";
    }
    if (s->initialized) {
        return "ready";
    }
    if (!s->attempted) {
        return "not attempted yet";
    }
    return "unavailable (will retry)";
}
