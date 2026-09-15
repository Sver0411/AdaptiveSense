/*
 * config.example.h — Firmware configuration template.
 *
 * This file is committed as an EXAMPLE with safe, public default values.
 * To build the project for your own hardware/network, copy it to config.h:
 *
 *     cp firmware/main/config.example.h firmware/main/config.h
 *
 * config.h is deliberately ignored by git (see .gitignore) so that real
 * Wi-Fi passwords and MQTT credentials are never committed.
 *
 * NOTE: these constants mirror experiments/experiment_config.yaml so that
 * on-device behaviour matches the offline replay simulator.
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
#define CONFIG_AS_WIFI_MAX_RETRY  20

/* ------------------------------------------------------------------ */
/* MQTT broker (REPLACE with your own; never commit live credentials)  */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_MQTT_URI        "mqtt://localhost:1883"
#define CONFIG_AS_MQTT_USER       ""          /* leave empty if none    */
#define CONFIG_AS_MQTT_PASS       ""          /* leave empty if none    */
#define CONFIG_AS_MQTT_TOPIC      "adaptivesense/data"
#define CONFIG_AS_MQTT_QOS        0

/* ------------------------------------------------------------------ */
/* Sensor (BME280 over I2C) / mock                                     */
/* CONFIG_AS_USE_MOCK_SENSOR forces a deterministic fake sensor that    */
/* mirrors dataset/ so the firmware logic can be exercised without HW. */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_USE_MOCK_SENSOR     0
#define CONFIG_AS_SENSOR_SDA_GPIO     4
#define CONFIG_AS_SENSOR_SCL_GPIO     5
#define CONFIG_AS_I2C_FREQ_HZ         400000
#define CONFIG_AS_BME280_I2C_ADDR     0x76   /* or 0x77 */

/* ------------------------------------------------------------------ */
/* Adaptive sampling parameters (mirror experiments/experiment_config) */
/* ------------------------------------------------------------------ */
#define CONFIG_AS_MIN_INTERVAL_S      5
#define CONFIG_AS_DEFAULT_INTERVAL_S  20
#define CONFIG_AS_MAX_INTERVAL_S      60

/* Channel noise floors (used to normalise the instability score).    */
#define CONFIG_AS_NOISE_FLOOR_TEMP    0.15f  /* deg C */
#define CONFIG_AS_NOISE_FLOOR_HUM     0.80f  /* %RH   */
#define CONFIG_AS_NOISE_FLOOR_PRESS   0.30f  /* hPa   */
#define CONFIG_AS_NOISE_FLOOR_LIGHT   30.0f  /* lux   */
#define CONFIG_AS_USE_HUMIDITY        1
#define CONFIG_AS_USE_PRESSURE        0
#define CONFIG_AS_USE_LIGHT           1

/* Analyzer windows (seconds).                                       */
#define CONFIG_AS_VARIETY_WINDOW_S    30
#define CONFIG_AS_ROC_WINDOW_S        10
#define CONFIG_AS_BASELINE_TAU_S      60

/* State-machine thresholds (multiples of noise floor) + hysteresis.  */
#define CONFIG_AS_STABLE_THRESHOLD    3.0f
#define CONFIG_AS_ACTIVE_THRESHOLD    8.0f
#define CONFIG_AS_HYSTERESIS_FRACTION 0.5f

/* Interval ladders per state (seconds).                             */
#define CONFIG_AS_LADDER_STABLE       { 20, 40, 60 }
#define CONFIG_AS_LADDER_ACTIVE       { 60, 30, 15, 5 }
#define CONFIG_AS_LADDER_ALERT        { 5 }

/* Event detection.                                                  */
#define CONFIG_AS_EVENT_THRESHOLD     8.0f
#define CONFIG_AS_EVENT_MIN_DURATION_S 10

/* Upload policy.                                                    */
#define CONFIG_AS_UP_ON_EVENT         1
#define CONFIG_AS_UP_ON_STATE_CHANGE  1
#define CONFIG_AS_UP_ON_INTERVAL_CHANGE 1
#define CONFIG_AS_UP_HEARTBEAT_S      60
#define CONFIG_AS_UP_DELTA_THRESHOLD  2.0f

/* ------------------------------------------------------------------ */
/* Power management                                                    */
/* ------------------------------------------------------------------ */
/* Wake = sample = evaluate = maybe transmit, then deep sleep for the  */
/* remaining time until the next scheduled sample.                     */
#define CONFIG_AS_DEEP_SLEEP_ENABLE   1
#define CONFIG_AS_WAKE_GPIO_ENABLE    0    /* external wake source */
#define CONFIG_AS_WORK_CYCLE_NOTE     "see docs/power_management.md"

/* Max channel count handled by the fixed-size analyzers.             */
#define CONFIG_AS_MAX_CHANNELS        4

#endif /* ADAPTIVESENSE_CONFIG_H */