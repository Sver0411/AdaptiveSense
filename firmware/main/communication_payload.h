/*
 * communication_payload.h — MQTT payload construction.
 *
 * Pure C99 with no ESP-IDF dependency, so the payload format is unit-tested on
 * the host (`tests/test_communication_payload.py` compiles this file together
 * with a small driver).
 *
 * Why a per-channel validity map
 * ------------------------------
 * Which channels carry a value depends on what the build's sensors can measure
 * (see sensor.h): an SHT30 provides temperature and humidity, an optional BH1750
 * provides light, and pressure has nothing behind it on an SHT30 build. Reporting
 * a missing channel as `0` would be indistinguishable from a genuine reading of
 * zero, so an unavailable channel is sent as JSON `null` and the payload
 * additionally carries an explicit `valid` map. A consumer can therefore tell
 * "the value is 0" from "there is no value" without knowing which sensors this
 * build has.
 */
#ifndef ADAPTIVESENSE_COMMUNICATION_PAYLOAD_H
#define ADAPTIVESENSE_COMMUNICATION_PAYLOAD_H

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Channel order used by the sensor layer and by the payload. */
#define COMM_PAYLOAD_CHANNELS 4
#define COMM_PAYLOAD_CH_TEMPERATURE 0
#define COMM_PAYLOAD_CH_HUMIDITY    1
#define COMM_PAYLOAD_CH_PRESSURE    2
#define COMM_PAYLOAD_CH_LIGHT       3

/* Worst-case payload size, for sizing the caller's buffer. */
#define COMM_PAYLOAD_MAX_LEN 320

/*
 * Build one JSON payload.
 *
 * `values[c]` is only written when `valid[c]` is true; an invalid channel is
 * emitted as `null`, and every channel appears in the `valid` map.
 *
 * Returns the number of bytes written, excluding the terminating NUL, or -1 if
 * the payload does not fit in `buf` (in which case nothing usable is left in the
 * buffer and the caller must not transmit it).
 */
int comm_build_payload(char *buf, size_t len,
                       const char *device_id,
                       double timestamp_ms,
                       const float values[COMM_PAYLOAD_CHANNELS],
                       const bool valid[COMM_PAYLOAD_CHANNELS],
                       const char *state_str,
                       float interval_s,
                       bool event);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_COMMUNICATION_PAYLOAD_H */
