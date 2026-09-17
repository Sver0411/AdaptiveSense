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

/*
 * Initialise the sensor subsystem. Must succeed before sensor_read() will produce
 * a reading.
 *
 * Which chip this drives is a configuration choice
 * (CONFIG_AS_SENSOR_BACKEND): a BME280/BMP280 or an SHT30/SHT3x, or the mock
 * when CONFIG_AS_USE_MOCK_SENSOR is set. The caller does not need to know which:
 * a backend reports the channels it cannot measure by leaving them invalid.
 */
int sensor_init(void);

/* True once sensor_init() has succeeded. */
bool sensor_is_initialized(void);

/*
 * Take a single measurement. Returns 0 on success, -1 on failure.
 *
 * On success, read `valid[]` to see which channels carry a real value: a backend
 * fills in only what its chip can measure (for example an SHT30 has no pressure
 * channel). A channel with `valid == false` must be ignored by the caller —
 * its `value` is zero and carries no meaning.
 *
 * On failure the return value is -1 and **no** channel is marked valid, so a
 * caller that ignores the return value cannot mistake the buffer for a
 * measurement. Before a successful init the buffer is not written to at all.
 */
int sensor_read(sensor_read_t *out);

/* Human-readable name of the active backend, for logs and the boot banner. */
const char *sensor_backend_name(void);

/* Human-readable name of a channel, for logging/MQTT build-up. */
const char *sensor_channel_name(sen_channel_t c);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_SENSOR_H */