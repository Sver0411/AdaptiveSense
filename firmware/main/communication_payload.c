/*
 * communication_payload.c — see communication_payload.h.
 *
 * Pure C99, no ESP-IDF dependency, host-testable.
 */

#include "communication_payload.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static const char *const CHANNEL_NAMES[COMM_PAYLOAD_CHANNELS] = {
    "temperature", "humidity", "pressure", "light"
};

/* Format specifier per channel, matching the sensor layer's units. */
static const char *const CHANNEL_FORMATS[COMM_PAYLOAD_CHANNELS] = {
    "%.2f", "%.2f", "%.2f", "%.1f"
};

/* Append formatted text, tracking the write offset. Returns false if truncated. */
static bool append(char *buf, size_t len, size_t *offset, const char *fmt, ...)
{
    if (*offset >= len) {
        return false;
    }
    va_list args;
    va_start(args, fmt);
    const int written = vsnprintf(buf + *offset, len - *offset, fmt, args);
    va_end(args);

    if (written < 0 || (size_t)written >= (len - *offset)) {
        return false;
    }
    *offset += (size_t)written;
    return true;
}

int comm_build_payload(char *buf, size_t len,
                       const char *device_id,
                       double timestamp_ms,
                       const float values[COMM_PAYLOAD_CHANNELS],
                       const bool valid[COMM_PAYLOAD_CHANNELS],
                       const char *state_str,
                       float interval_s,
                       bool event)
{
    if (buf == NULL || len == 0 || device_id == NULL || state_str == NULL ||
        values == NULL || valid == NULL) {
        return -1;
    }

    size_t offset = 0;

    if (!append(buf, len, &offset, "{\"device_id\":\"%s\",\"timestamp\":%.0f",
                device_id, timestamp_ms)) {
        return -1;
    }

    /* Readings. An unavailable channel is `null`, never a plausible-looking 0. */
    for (int c = 0; c < COMM_PAYLOAD_CHANNELS; c++) {
        if (!append(buf, len, &offset, ",\"%s\":", CHANNEL_NAMES[c])) {
            return -1;
        }
        if (valid[c]) {
            if (!append(buf, len, &offset, CHANNEL_FORMATS[c], (double)values[c])) {
                return -1;
            }
        } else if (!append(buf, len, &offset, "null")) {
            return -1;
        }
    }

    if (!append(buf, len, &offset,
                ",\"sampling_interval\":%.1f,\"state\":\"%s\",\"event\":%s",
                (double)interval_s, state_str, event ? "true" : "false")) {
        return -1;
    }

    /* Explicit validity map so a consumer never has to infer it. */
    if (!append(buf, len, &offset, ",\"valid\":{")) {
        return -1;
    }
    for (int c = 0; c < COMM_PAYLOAD_CHANNELS; c++) {
        if (!append(buf, len, &offset, "%s\"%s\":%s", (c == 0) ? "" : ",",
                    CHANNEL_NAMES[c], valid[c] ? "true" : "false")) {
            return -1;
        }
    }
    if (!append(buf, len, &offset, "}}")) {
        return -1;
    }

    return (int)offset;
}
