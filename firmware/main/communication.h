/*
 * communication.h — Communication Layer.
 *
 * Wi-Fi bring-up, the modem-sleep power save, and a thin wrapper around the
 * built-in esp-mqtt component that publishes one JSON payload per requested
 * upload.
 *
 * What "publish succeeded" means here
 * -----------------------------------
 * `esp_mqtt_client_publish()` is asynchronous and, at QoS 0, returns as soon as
 * the MQTT client has **accepted the request into its outbound queue**. It does
 * not mean the broker received the packet, and it certainly does not mean an
 * application on the far side processed it. The counters below therefore say
 * `publish_call_ok` / `publish_call_failed`: the outcome of the call into the
 * MQTT client. Confirming delivery would need QoS 1 plus `MQTT_EVENT_PUBLISHED`
 * plus server-side receipt validation, which this version does not implement
 * (see the README's Future work).
 *
 * This distinction matters because the policy's own "should I upload?" decision
 * and the transport outcome are different things, and an earlier revision
 * discarded the transport outcome entirely, so a dead broker looked exactly like
 * an idle node.
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

/* Transport bookkeeping. Every field is an observation of this node's own calls. */
typedef struct {
    unsigned long publish_requested;   /* upload_requested == true            */
    unsigned long publish_call_ok;     /* MQTT client accepted the request    */
    unsigned long publish_call_failed; /* not connected, or the call rejected */
    bool          mqtt_connected;
} comm_stats_t;

/* Start Wi-Fi + MQTT and enable the Wi-Fi modem-sleep power save. */
int communication_start(void);

/* True once the MQTT broker is connected and ready to accept a publish call. */
bool communication_ready(void);

/*
 * Publish one sensor+decision record.
 *
 * `valid` marks which channels carry a real reading; an invalid channel is sent
 * as JSON `null` (plus an explicit `valid` map) rather than as a plausible 0.
 *
 * Returns 0 if the MQTT client accepted the request, -1 otherwise. Every call is
 * counted in `communication_stats()`.
 */
int communication_publish(double timestamp_ms,
                          const float values[4],
                          const bool valid[4],
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
