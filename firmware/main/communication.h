/*
 * communication.h — Communication Layer.
 *
 * Thin wrapper around the esp-mqtt client. It publishes a single JSON payload
 * per upload. Broker URI and credentials come from build-time configuration
 * (config.h); the committed template contains placeholders only.
 */
#ifndef ADAPTIVESENSE_COMMUNICATION_H
#define ADAPTIVESENSE_COMMUNICATION_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    COMM_STATE = 0,   /* STABLE */
    COMM_ACTIVE,
    COMM_ALERT,
} comm_state_t;

/* Start Wi-Fi + MQTT. Non-blocking event-driven setup. */
int communication_start(void);

/* True once the MQTT broker is connected and ready to publish. */
bool communication_ready(void);

/*
 * Publish one sensor+decision record. Values are the four channels; state,
 * interval and event describe the AdaptiveSense decision for this sample.
 * Payload follows the JSON schema in docs/methodology.md.
 */
int communication_publish(double timestamp_ms,
                          const float values[4],
                          comm_state_t state,
                          float interval_s,
                          bool event);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_COMMUNICATION_H */