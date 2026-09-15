/*
 * config.example.h — Firmware configuration template.
 *
 * This file is committed as an EXAMPLE with safe, public default values.
 * To build the project for your own hardware/network, copy it to config.h:
 *
 *     cp firmware/main/config.example.h firmware/main/config.h
 *
 * (firmware/main/CMakeLists.txt does this automatically if config.h is
 * missing.) config.h is ignored by git so that real Wi-Fi passwords and MQTT
 * credentials are never committed.
 *
 * IMPORTANT: the algorithm constants below mirror
 * `experiments/experiment_config.yaml` value for value. They are the ONLY
 * definition of the on-device parameters; nothing is hardcoded in the C sources.
 * `scripts/check_config_parity.py` fails if the two files drift apart, and it
 * runs in CI.
 *
 * The behaviour these values drive is specified in docs/change_score_spec.md.
 */
#ifndef ADAPTIVESENSE_CONFIG_H
#define ADAPTIVESENSE_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

/* ------------------------------------------------------------------ */
/* Device identity                                                     */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_DEVICE_ID       "node-01"   /* reported in MQTT payload */
#define CONFIG_AS_WIFI_SSID       "your-wifi" /* REPLACE */
#define CONFIG_AS_WIFI_PASS       "your-pass" /* REPLACE */

/* ------------------------------------------------------------------ */
/* MQTT broker (REPLACE with your own; never commit live credentials)   */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_MQTT_URI        "mqtt://localhost:1883"
#define CONFIG_AS_MQTT_USER       ""          /* leave empty if none    */
#define CONFIG_AS_MQTT_PASS       ""          /* leave empty if none    */
#define CONFIG_AS_MQTT_TOPIC      "adaptivesense/data"
#define CONFIG_AS_MQTT_QOS        0

/*
 * Keepalive.
 *
 * The published keepalive is what bounds how long the broker will wait for any
 * traffic before declaring the session dead. It must comfortably exceed the
 * longest idle window, or the node can be asleep when the broker decides the
 * session is gone — hence the static constraint below: keepalive >= 2 x the
 * maximum sampling interval.
 *
 * Note what this does NOT mean. Keepalive is itself traffic: the MQTT client
 * task sends PINGREQ and the broker replies PINGRESP, and in this architecture
 * that activity wakes the chip out of light sleep through the Wi-Fi driver's
 * PM locks. The value therefore trades session robustness against wake-ups, and
 * it is one of the reasons the reported `application_upload_reduction` is a
 * payload metric and not a total radio-traffic metric. See the README.
 */
#define CONFIG_AS_MQTT_KEEPALIVE_S 120  /* >= 2 x CONFIG_AS_MAX_INTERVAL_S */

/* ------------------------------------------------------------------ */
/* Sensor (BME280 over I2C) / mock                                     */
/* ------------------------------------------------------------------ */
/*
 * CONFIG_AS_USE_MOCK_SENSOR forces a deterministic fake sensor that mirrors
 * dataset/ so the firmware logic can be exercised without hardware. It is
 * guarded so a host-side test can override it on the compiler command line
 * (-DCONFIG_AS_USE_MOCK_SENSOR=1) and exercise the driver's public contract
 * without an ESP32 attached.
 */
#ifndef CONFIG_AS_USE_MOCK_SENSOR
#define CONFIG_AS_USE_MOCK_SENSOR     0
#endif
#define CONFIG_AS_SENSOR_SDA_GPIO     4
#define CONFIG_AS_SENSOR_SCL_GPIO     5
#define CONFIG_AS_I2C_FREQ_HZ         400000
#define CONFIG_AS_BME280_I2C_ADDR     0x76   /* or 0x77 */

/* Forced-mode measurement timing (see firmware/main/sensor.c).
 * At oversampling x1/x1/x1 the datasheet's worst-case conversion time is well
 * under 20 ms; MEAS_SETTLE_MS lets the `measuring` status bit be asserted
 * before polling starts, and MEAS_TIMEOUT_MS is a hard upper bound on the
 * wait. The driver never blocks indefinitely. */
#define CONFIG_AS_BME280_MEAS_SETTLE_MS  2
#define CONFIG_AS_BME280_MEAS_TIMEOUT_MS 50
/* Hard timeout for every I2C transaction. */
#define CONFIG_AS_I2C_TIMEOUT_MS         100

/* ------------------------------------------------------------------ */
/* Adaptive sampling parameters (mirror experiments/experiment_config) */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_MIN_INTERVAL_S      5
#define CONFIG_AS_DEFAULT_INTERVAL_S  20
#define CONFIG_AS_MAX_INTERVAL_S      60

/* Channel noise floors. Used both to normalise the instability score and to
 * normalise the upload delta. */
#define CONFIG_AS_NOISE_FLOOR_TEMP    0.15f  /* deg C */
#define CONFIG_AS_NOISE_FLOOR_HUM     0.80f  /* %RH   */
#define CONFIG_AS_NOISE_FLOOR_PRESS   0.30f  /* hPa   */
#define CONFIG_AS_NOISE_FLOOR_LIGHT   30.0f  /* lux   */
#define CONFIG_AS_USE_HUMIDITY        1
#define CONFIG_AS_USE_PRESSURE        0
#define CONFIG_AS_USE_LIGHT           1

/* Analyzer windows (seconds), matching adaptive.analyzer in the YAML.
 * roc_window_s must not exceed variety_window_s. */
#define CONFIG_AS_VARIETY_WINDOW_S    30
#define CONFIG_AS_ROC_WINDOW_S        10
#define CONFIG_AS_BASELINE_TAU_S      60

/* State-machine thresholds (multiples of the noise floor) + hysteresis. */
#define CONFIG_AS_STABLE_THRESHOLD    3.0f
#define CONFIG_AS_ACTIVE_THRESHOLD    8.0f
#define CONFIG_AS_HYSTERESIS_FRACTION 0.5f

/* Interval ladders per state (seconds).
 *
 *   STABLE ascends (sample less when nothing happens)
 *   ACTIVE descends (sample faster while the environment is moving)
 *   ALERT  is fixed at the minimum interval
 *
 * An earlier revision had `{ 60, 30, 15, 5 }` for ACTIVE, so the node waited
 * 60 s immediately after detecting change. Do not restore that ordering:
 * scripts/check_config_parity.py checks the ladder values, and
 * simulator/config.py rejects a ladder that is not monotone in the right
 * direction.
 */
#define CONFIG_AS_LADDER_STABLE       { 20, 40, 60 }
#define CONFIG_AS_LADDER_ACTIVE       { 15, 10, 5 }
#define CONFIG_AS_LADDER_ALERT        { 5 }

/* Evaluations spent on a ladder rung before advancing to the next one. */
#define CONFIG_AS_LADDER_CONFIRM_STABLE 2
#define CONFIG_AS_LADDER_CONFIRM_ACTIVE 1
#define CONFIG_AS_LADDER_CONFIRM_ALERT  1

/* Event detection. */
#define CONFIG_AS_EVENT_THRESHOLD      8.0f
#define CONFIG_AS_EVENT_MIN_DURATION_S 10

/* Upload policy. */
#define CONFIG_AS_UP_FIRST_SAMPLE      1
#define CONFIG_AS_UP_ON_EVENT          1
#define CONFIG_AS_UP_ON_STATE_CHANGE   1
#define CONFIG_AS_UP_ON_INTERVAL_CHANGE 1
#define CONFIG_AS_UP_HEARTBEAT_S       60
#define CONFIG_AS_UP_DELTA_THRESHOLD   2.0f

/* ------------------------------------------------------------------ */
/* Power management                                                    */
/* ------------------------------------------------------------------ */
/*
 * 0 = none        : vTaskDelay only; the radio and CPU stay on. Bench/debug use.
 * 1 = auto light  : vTaskDelay, and the ESP-IDF power manager enters light sleep
 *                   automatically while the scheduler is idle (FreeRTOS tickless
 *                   idle + Wi-Fi modem sleep). DEFAULT.
 * 2 = deep        : ESP deep sleep. Rejected below: it reboots the chip, so the
 *                   detector's EMA baselines, the ladder position and the event
 *                   debounce state would not survive. Experimental, not enabled.
 *
 * The application never calls esp_light_sleep_start() — see
 * docs/power_management.md.
 */
#define CONFIG_AS_SLEEP_MODE 1

/* Wi-Fi modem sleep (WIFI_PS_MIN_MODEM) is enabled once at start-up by
 * communication_start(). It is a radio power-save mode, NOT an energy
 * measurement: the node's energy is "Not measured yet." until a current monitor
 * is attached. The station is never stopped between samples. */
#define CONFIG_AS_WIFI_MODEM_SLEEP_ENABLE 1

#if CONFIG_AS_SLEEP_MODE == 2
#error "CONFIG_AS_SLEEP_MODE=2 (deep sleep) is experimental: the adaptive scheduler state is not persisted across the reboot. Use 0 (none) or 1 (auto light sleep)."
#endif

#if CONFIG_AS_SLEEP_MODE != 0 && CONFIG_AS_SLEEP_MODE != 1
#error "CONFIG_AS_SLEEP_MODE must be 0 (none) or 1 (auto light sleep); 2 is rejected above"
#endif

/* ------------------------------------------------------------------ */
/* Cross-checks                                                        */
/* ------------------------------------------------------------------ */
/* The MQTT keepalive must outlast the longest idle window by a margin, so a
 * sleeping node is never declared gone by the broker. */
#if CONFIG_AS_MQTT_KEEPALIVE_S < (2 * CONFIG_AS_MAX_INTERVAL_S)
#error "CONFIG_AS_MQTT_KEEPALIVE_S must be at least twice CONFIG_AS_MAX_INTERVAL_S"
#endif

#endif /* ADAPTIVESENSE_CONFIG_H */
