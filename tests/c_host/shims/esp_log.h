/*
 * Test-only shim for the host build of `firmware/main/sensor.c`.
 *
 * `sensor.c` keeps its `#include "esp_log.h"` unconditionally so that the mock
 * sensor path still logs on the device. On the host there is no ESP-IDF, so
 * `tests/test_sensor_contract.py` compiles with `-Itests/c_host/shims` ahead of
 * `-Ifirmware/main` and this file is picked up instead.
 *
 * The macros expand to a no-op *that references the tag*, so a translation unit
 * built for the host does not trip -Wunused-variable on its `TAG` constant.
 */
#ifndef ADAPTIVESENSE_HOST_SHIM_ESP_LOG_H
#define ADAPTIVESENSE_HOST_SHIM_ESP_LOG_H

#define AS_HOST_LOG_NOOP(tag, fmt, ...) ((void)(tag), (void)0)

#define ESP_LOGE(tag, fmt, ...) AS_HOST_LOG_NOOP(tag, fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) AS_HOST_LOG_NOOP(tag, fmt, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) AS_HOST_LOG_NOOP(tag, fmt, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) AS_HOST_LOG_NOOP(tag, fmt, ##__VA_ARGS__)
#define ESP_LOGV(tag, fmt, ...) AS_HOST_LOG_NOOP(tag, fmt, ##__VA_ARGS__)

#endif /* ADAPTIVESENSE_HOST_SHIM_ESP_LOG_H */
