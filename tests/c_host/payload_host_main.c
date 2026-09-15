/*
 * payload_host_main.c — host driver for `firmware/main/communication_payload.c`.
 *
 * `communication_payload.c` has no ESP-IDF dependency, so the MQTT payload
 * format can be built and inspected on the host. `tests/test_communication_payload.py`
 * uses this to check that an unavailable channel is not reported as a real
 * reading.
 *
 * Usage:
 *   payload_host <device_id> <timestamp_ms> <temp> <hum> <press> <light> \
 *                <valid4 e.g. 1110> <state> <interval_s> <event 0|1> [max_len]
 *
 * Prints the payload on stdout, or `ERROR:<code>` if it did not fit.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "communication_payload.h"

int main(int argc, char **argv)
{
    if (argc < 11 || argc > 12) {
        fprintf(stderr,
                "usage: %s <device_id> <timestamp_ms> <temp> <hum> <press> <light> "
                "<valid4> <state> <interval_s> <event> [max_len]\n", argv[0]);
        return 2;
    }

    const char *device_id = argv[1];
    const double timestamp_ms = strtod(argv[2], NULL);

    float values[COMM_PAYLOAD_CHANNELS] = {
        (float)strtod(argv[3], NULL),
        (float)strtod(argv[4], NULL),
        (float)strtod(argv[5], NULL),
        (float)strtod(argv[6], NULL),
    };

    const char *valid_flags = argv[7];
    if (strlen(valid_flags) != COMM_PAYLOAD_CHANNELS) {
        fprintf(stderr, "valid4 must be %d characters\n", COMM_PAYLOAD_CHANNELS);
        return 2;
    }
    bool valid[COMM_PAYLOAD_CHANNELS];
    for (int c = 0; c < COMM_PAYLOAD_CHANNELS; c++) {
        valid[c] = (valid_flags[c] == '1');
    }

    const char *state_str = argv[8];
    const float interval_s = (float)strtod(argv[9], NULL);
    const bool event = (atoi(argv[10]) != 0);

    size_t max_len = COMM_PAYLOAD_MAX_LEN;
    if (argc == 12) {
        max_len = (size_t)strtoul(argv[11], NULL, 10);
    }
    if (max_len == 0 || max_len > 4096) {
        fprintf(stderr, "max_len out of range\n");
        return 2;
    }

    char *buf = calloc(max_len + 1, 1);
    if (buf == NULL) {
        fprintf(stderr, "allocation failed\n");
        return 2;
    }

    const int written = comm_build_payload(buf, max_len, device_id, timestamp_ms,
                                           values, valid, state_str, interval_s, event);
    if (written < 0) {
        printf("ERROR:%d\n", written);
    } else {
        printf("%s\n", buf);
    }

    free(buf);
    return 0;
}
