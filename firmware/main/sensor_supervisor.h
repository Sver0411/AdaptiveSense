/*
 * sensor_supervisor.h — sensor bring-up policy.
 *
 * Decides *when* the application may talk to the sensor and *when* it should try
 * to initialise it again. Keeping this decision in a pure-C unit (no ESP-IDF
 * dependency) means the retry policy is unit-tested on the host rather than being
 * buried in the main loop.
 *
 * The behaviour it replaces: the main loop called `sensor_init()` once and then
 * went on calling `sensor_read()` forever, whether or not the sensor had ever come
 * up. A board with a detached BME280 therefore produced an endless stream of
 * failed reads and a permanently unusable node, with no path back.
 *
 * The policy here is deliberately simple — retry at most once every
 * `retry_interval_s` seconds — because an exponential backoff would only delay
 * recovery on a device that is already idle most of the time.
 */
#ifndef ADAPTIVESENSE_SENSOR_SUPERVISOR_H
#define ADAPTIVESENSE_SENSOR_SUPERVISOR_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool     initialized;      /* the sensor answered and is usable          */
    bool     attempted;        /* at least one init attempt has been noted   */
    double   retry_interval_s; /* minimum spacing between init attempts      */
    double   next_attempt_t;   /* earliest time for the next init attempt    */
    unsigned init_attempts;    /* total attempts                             */
    unsigned init_failures;    /* attempts that did not succeed              */
} sensor_supervisor_t;

/* Arm the supervisor. The first init attempt is allowed at `now`. */
void sensor_sup_init(sensor_supervisor_t *s, double retry_interval_s, double now);

/* May the application read the sensor? Only after a successful init. */
bool sensor_sup_ready(const sensor_supervisor_t *s);

/* Should the application attempt `sensor_init()` now? */
bool sensor_sup_should_attempt(const sensor_supervisor_t *s, double now);

/*
 * Record the outcome of an init attempt made at time `now`.
 * On failure the next attempt is scheduled `retry_interval_s` later.
 */
void sensor_sup_note_attempt(sensor_supervisor_t *s, double now, bool ok);

/* Human-readable state, for the log. */
const char *sensor_sup_state_name(const sensor_supervisor_t *s);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_SENSOR_SUPERVISOR_H */
