/*
 * communication.h — Communication Layer.
 *
 * Thin wrapper around the built-in esp-mqtt component. It publishes one JSON
 * payload per upload. Broker URI and credentials come from build-time
 * configuration (config.h); the committed template contains placeholders only.
 *
 * The return value of `communication_publish()` is meaningful: it distinguishes
 * "the policy asked for an upload" from "the packet was handed to a connected
 * MQTT client". v0.1 ignored the return value, so a dead broker produced no
 * error, no log and no counter — the node looked healthy while sending nothing.
 */
#ifndef ADAPTIVESENSE_COMMUNICATION_H
#define ADAPTIVESENSE_COMMUNICATION_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    COMM_STABLE = 0, /* STABLE  */
    COMM_ACTIVE,     /* ACTIVE  */
    COMM_ALERT       /* ALERT   */
} comm_state_t;

/* Transport outcome bookkeeping (see docs/methodology.md). */
typedef struct {
    unsigned long publish_requested; /* upload_requested == true          */
    unsigned long publish_ok;        /* accepted by a connected client    */
    unsigned long publish_failed;    /* not connected, or broker rejected */
    bool          mqtt_connected;
} comm_stats_t;

/* Start Wi-Fi + MQTT and enable the Wi-Fi modem-sleep power save. */
int communication_start(void);

/* True once the MQTT broker is connected and ready to publish. */
bool communication_ready(void);

/*
 * Publish one sensor+decision record.
 *
 * Values are the four channels; state, interval and event describe the
 * AdaptiveSense decision for this sample. Payload follows the JSON schema in
 * docs/methodology.md.
 *
 * Returns 0 if the packet was handed to a connected broker, -1 otherwise.
 * Every call is counted in `communication_stats()`.
 */
int communication_publish(double timestamp_ms,
                          const float values[4],
                          comm_state_t state,
                          float interval_s,
                          bool event);

/* Snapshot of the publish counters. */
const comm_stats_t *communication_stats(void);

/* Log the accumulated counters at INFO level (called periodically by main). */
void communication_log_stats(void);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_COMMUNICATION_H */
