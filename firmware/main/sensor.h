/*
 * sensor.h — Sensor Layer.
 *
 * Abstracts the physical transducer(s) behind a small interface so the rest of
 * the system never depends on the specific chip. Primary sensor: BME280
 * (temperature / humidity / pressure) over I2C. An optional therma/light input
 * (BH1750) shares the same read struct.
 *
 * When CONFIG_AS_USE_MOCK_SENSOR is enabled a deterministic mock is used
 * instead, which lets the firmware logic be exercised without hardware. A mock
 * is clearly labeled: it is never reported as a real measurement.
 */
#ifndef ADAPTIVESENSE_SENSOR_H
#define ADAPTIVESENSE_SENSOR_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SEN_CH_TEMPERATURE = 0,
    SEN_CH_HUMIDITY,
    SEN_CH_PRESSURE,
    SEN_CH_LIGHT,
    SEN_CH_COUNT
} sen_channel_t;

typedef struct {
    float value[SEN_CH_COUNT];   /* temperature C, humidity %RH, pressure hPa,
                                      light lux (BH1750) */
    bool  valid[SEN_CH_COUNT];
} sensor_read_t;

/* Initialise the sensor subsystem (I2C, BME280 or mock). */
int sensor_init(void);

/* Take a single measurement. Returns 0 on success (partial validity is
 * signalled via sensor_read_t.valid). */
int sensor_read(sensor_read_t *out);

/* Human-readable name of a channel, for logging/MQTT build-up. */
const char *sensor_channel_name(sen_channel_t c);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_SENSOR_H */