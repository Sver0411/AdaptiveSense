/*
 * sensor.c — BME280 (I2C) driver + optional mock sensor.
 *
 * Transport only: register access, chip bring-up, the forced-mode measurement
 * sequence, and mapping the compensated values into a `sensor_read_t`. All
 * calibration parsing and compensation maths live in `bme280_math.c`, which has
 * no ESP-IDF dependency and is unit-tested on the host.
 *
 * Measurement model — FORCED MODE (see docs/hardware.md):
 *
 *   wake -> trigger one BME280 conversion -> wait for it (bounded) -> read ->
 *   evaluate -> sleep
 *
 * The sensor is never left converting while the MCU sleeps, and it is never in
 * normal mode continuously sampling in the background. `ctrl_meas` bits [1:0] are
 * therefore always written with 0b01 (forced).
 *
 * Initialisation contract
 * -----------------------
 * `sensor_read()` refuses to do anything until `sensor_init()` has reported
 * success, so a detached sensor can never produce a reading of zeros that the
 * change detector would treat as a real measurement. The decision of when to
 * retry init lives in `sensor_supervisor.c` (pure C, host-tested); this file only
 * enforces the contract and owns the hardware state.
 *
 * Uses the current ESP-IDF I2C master driver (`driver/i2c_master.h`); the legacy
 * `driver/i2c.h` API is deprecated from ESP-IDF v5.2 onwards.
 *
 * Host build: with CONFIG_AS_USE_MOCK_SENSOR=1 the I2C paths are compiled out
 * entirely, so the file builds against the test-only `esp_log.h` shim in
 * tests/c_host/shims and its public contract can be tested on a workstation.
 * See tests/test_sensor_contract.py.
 */
#include <math.h>
#include <string.h>

#include "esp_log.h"

#include "bme280_math.h"
#include "config_include.h"
#include "sensor.h"

#if !CONFIG_AS_USE_MOCK_SENSOR
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c_master.h"
#endif

static const char *TAG = "sensor";

/* Set to true only by a sensor_init() that actually succeeded. */
static bool s_sensor_initialized = false;

/* ------------------------------------------------------------------ */
/* Mock sensor: reproducible synthetic sequence (documented in README). */
/* ------------------------------------------------------------------ */
#if CONFIG_AS_USE_MOCK_SENSOR
static void mock_fill(sensor_read_t *out)
{
    static uint32_t step = 0;
    const float t = 24.0f + 0.08f * sinf((float)step * 0.1f);
    const float h = 45.0f + 0.4f * sinf((float)step * 0.07f);
    for (int i = 0; i < SEN_CH_COUNT; i++) {
        out->valid[i] = true;
    }
    out->value[SEN_CH_TEMPERATURE] = t;
    out->value[SEN_CH_HUMIDITY] = h;
    out->value[SEN_CH_PRESSURE] = 1012.4f;
    out->value[SEN_CH_LIGHT] = 320.0f + 3.0f * sinf((float)step * 0.02f);
    step++;
}
#endif /* CONFIG_AS_USE_MOCK_SENSOR */

#if !CONFIG_AS_USE_MOCK_SENSOR
/* ------------------------------------------------------------------ */
/* BME280 registers                                                    */
/* ------------------------------------------------------------------ */
#define BME280_REG_ID        0xD0u
#define BME280_REG_RESET     0xE0u
#define BME280_REG_CTRL_HUM  0xF2u
#define BME280_REG_STATUS    0xF3u
#define BME280_REG_CTRL_MEAS 0xF4u
#define BME280_REG_CONFIG    0xF5u
#define BME280_REG_DATA      0xF7u  /* 0xF7..0xFE, 8 bytes */

#define BME280_CHIP_ID       0x60u
#define BME280_RESET_CMD     0xB6u

#define BME280_STATUS_MEASURING 0x08u  /* status bit 3 */

/* Oversampling x1 for temperature, pressure and humidity (osrs_* = 001). */
#define BME280_OSRS_T 0x01u
#define BME280_OSRS_P 0x01u
#define BME280_OSRS_H 0x01u

/* ctrl_meas: osrs_t[7:5] | osrs_p[4:2] | mode[1:0]; mode 0b01 = forced. */
#define BME280_MODE_FORCED 0x01u
#define BME280_CTRL_MEAS_VALUE \
    ((uint8_t)((BME280_OSRS_T << 5) | (BME280_OSRS_P << 2) | BME280_MODE_FORCED))

/* config: t_sb[7:5]=000 (irrelevant in forced mode), filter[4:2]=000 (off),
 * spi3w_en[0]=0. Written once to clear the reset defaults. */
#define BME280_CONFIG_VALUE 0x00u

static i2c_master_bus_handle_t s_bus = NULL;
static i2c_master_dev_handle_t s_dev = NULL;
static bme280_calib_t s_cal;

/*
 * Release anything a previous attempt left behind.
 *
 * `sensor_init()` is called again by the bring-up supervisor
 * (sensor_supervisor.c), and `i2c_new_master_bus()` fails with
 * ESP_ERR_INVALID_STATE while the port is still owned:
 *
 *     E i2c.common: I2C bus id(0) has already been acquired
 *     E i2c.master: i2c_new_master_bus(993): I2C bus acquire failed
 *
 * Observed on hardware on the second bring-up attempt, and it made every retry
 * fail — so a node whose sensor was connected after boot could never recover.
 * Idempotent, and all failure paths below funnel through it.
 */
static void sensor_i2c_teardown(void)
{
    if (s_dev != NULL) {
        i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
    if (s_bus != NULL) {
        i2c_del_master_bus(s_bus);
        s_bus = NULL;
    }
}

/* ------------------------------------------------------------------ */
/* register transport                                                  */
/* ------------------------------------------------------------------ */
static esp_err_t bme_write_u8(uint8_t reg, uint8_t value)
{
    const uint8_t frame[2] = { reg, value };
    return i2c_master_transmit(s_dev, frame, sizeof(frame),
                               CONFIG_AS_I2C_TIMEOUT_MS);
}

static esp_err_t bme_read_block(uint8_t reg, uint8_t *buffer, size_t len)
{
    if (buffer == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_transmit_receive(s_dev, &reg, 1, buffer, len,
                                       CONFIG_AS_I2C_TIMEOUT_MS);
}

static esp_err_t bme_read_u8(uint8_t reg, uint8_t *value)
{
    return bme_read_block(reg, value, 1);
}

/* ------------------------------------------------------------------ */
/* measurement sequence (forced mode)                                  */
/* ------------------------------------------------------------------ */
static esp_err_t bme_trigger_measurement(void)
{
    /* ctrl_hum must be written before ctrl_meas for the humidity
     * oversampling to take effect (datasheet section 5.4.3). */
    esp_err_t err = bme_write_u8(BME280_REG_CTRL_HUM, BME280_OSRS_H);
    if (err != ESP_OK) {
        return err;
    }
    return bme_write_u8(BME280_REG_CTRL_MEAS, BME280_CTRL_MEAS_VALUE);
}

/*
 * Wait until the chip clears the `measuring` bit, with a hard timeout.
 *
 * At oversampling x1/x1/x1 the datasheet's worst-case conversion time is well
 * under 20 ms; CONFIG_AS_BME280_MEAS_TIMEOUT_MS is the upper bound and the
 * function never blocks longer than that. The initial short delay makes sure we
 * do not sample a status register that has not yet been updated.
 */
static esp_err_t bme_wait_measurement(void)
{
    vTaskDelay(pdMS_TO_TICKS(CONFIG_AS_BME280_MEAS_SETTLE_MS));

    const int64_t remaining_us =
        (int64_t)CONFIG_AS_BME280_MEAS_TIMEOUT_MS * 1000 -
        (int64_t)CONFIG_AS_BME280_MEAS_SETTLE_MS * 1000;
    int64_t waited_us = 0;

    while (waited_us < remaining_us) {
        uint8_t status = 0;
        esp_err_t err = bme_read_u8(BME280_REG_STATUS, &status);
        if (err != ESP_OK) {
            return err;
        }
        if ((status & BME280_STATUS_MEASURING) == 0) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
        waited_us += 1000;
    }

    /* Bounded failure, never an infinite wait: the caller marks the sample
     * invalid rather than reusing a stale register. */
    return ESP_ERR_TIMEOUT;
}
#endif /* !CONFIG_AS_USE_MOCK_SENSOR */

/* ------------------------------------------------------------------ */
/* public API                                                          */
/* ------------------------------------------------------------------ */
int sensor_init(void)
{
#if CONFIG_AS_USE_MOCK_SENSOR
    ESP_LOGW(TAG, "MOCK sensor enabled - results are NOT real measurements");
    s_sensor_initialized = true;
    return 0;
#else
    /* A retry must not inherit the previous attempt's bus. See
     * sensor_i2c_teardown(). */
    sensor_i2c_teardown();

    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_AS_SENSOR_SDA_GPIO,
        .scl_io_num = CONFIG_AS_SENSOR_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &s_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c bus init failed: %s", esp_err_to_name(err));
        goto fail;
    }

    const i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = CONFIG_AS_BME280_I2C_ADDR,
        .scl_speed_hz = CONFIG_AS_I2C_FREQ_HZ,
    };
    err = i2c_master_bus_add_device(s_bus, &dev_config, &s_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c device add failed: %s", esp_err_to_name(err));
        goto fail;
    }

    err = i2c_master_probe(s_bus, CONFIG_AS_BME280_I2C_ADDR,
                           CONFIG_AS_I2C_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BME280 not responding at 0x%02x: %s",
                 (unsigned)CONFIG_AS_BME280_I2C_ADDR, esp_err_to_name(err));
        goto fail;
    }

    uint8_t chip_id = 0;
    err = bme_read_u8(BME280_REG_ID, &chip_id);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "chip id read failed: %s", esp_err_to_name(err));
        goto fail;
    }
    if (chip_id != BME280_CHIP_ID) {
        ESP_LOGE(TAG, "unexpected chip id 0x%02x (expected 0x%02x)",
                 (unsigned)chip_id, (unsigned)BME280_CHIP_ID);
        goto fail;
    }

    err = bme_write_u8(BME280_REG_RESET, BME280_RESET_CMD);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "reset failed: %s", esp_err_to_name(err));
        goto fail;
    }
    vTaskDelay(pdMS_TO_TICKS(10)); /* datasheet: 2 ms start-up, 10 ms is safe */

    /* Calibration: 26 bytes from 0x88 (which ends at 0xA1 = dig_H1) and
     * 7 bytes from 0xE1. block2 is padded so no access can exceed it. */
    uint8_t block1[BME280_CALIB_BLOCK1_LEN];
    uint8_t block2[BME280_CALIB_BLOCK2_PAD];
    memset(block2, 0, sizeof(block2));

    err = bme_read_block(BME280_CALIB_BLOCK1_ADDR, block1, sizeof(block1));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "calibration block 1 read failed: %s", esp_err_to_name(err));
        goto fail;
    }
    err = bme_read_block(BME280_CALIB_BLOCK2_ADDR, block2, BME280_CALIB_BLOCK2_LEN);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "calibration block 2 read failed: %s", esp_err_to_name(err));
        goto fail;
    }
    if (!bme280_parse_calibration(block1, block2, &s_cal)) {
        ESP_LOGE(TAG, "calibration parse failed");
        goto fail;
    }

    err = bme_write_u8(BME280_REG_CONFIG, BME280_CONFIG_VALUE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "config write failed: %s", esp_err_to_name(err));
        goto fail;
    }

    s_sensor_initialized = true;
    ESP_LOGI(TAG, "BME280 ready at 0x%02x (forced measurement mode, timeout %d ms)",
             (unsigned)CONFIG_AS_BME280_I2C_ADDR,
             (int)CONFIG_AS_BME280_MEAS_TIMEOUT_MS);
    return 0;

fail:
    /* Every failure releases the bus, so the next bring-up attempt starts from
     * the same state as the first one. */
    sensor_i2c_teardown();
    return -1;
#endif /* CONFIG_AS_USE_MOCK_SENSOR */
}

int sensor_read(sensor_read_t *out)
{
    if (out == NULL) {
        return -1;
    }
    /* Contract: nothing is read before a successful init. Returning here leaves
     * `out` untouched, so a caller that ignores the return value cannot mistake
     * zeros for a measurement. */
    if (!s_sensor_initialized) {
        return -1;
    }
    memset(out, 0, sizeof(*out));

#if CONFIG_AS_USE_MOCK_SENSOR
    mock_fill(out);
    return 0;
#else
    esp_err_t err = bme_trigger_measurement();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "measurement trigger failed: %s", esp_err_to_name(err));
        return -1;
    }

    err = bme_wait_measurement();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "measurement did not complete: %s", esp_err_to_name(err));
        return -1;
    }

    uint8_t data[8];
    err = bme_read_block(BME280_REG_DATA, data, sizeof(data));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "data read failed: %s", esp_err_to_name(err));
        return -1;
    }

    bme280_uncomp_t raw;
    bme280_parse_raw(data, &raw);

    int32_t t_fine = 0;
    const double temperature =
        bme280_compensate_temperature(&s_cal, raw.temperature, &t_fine);
    const double pressure_pa =
        bme280_compensate_pressure(&s_cal, raw.pressure, t_fine);
    const double humidity =
        bme280_compensate_humidity(&s_cal, raw.humidity, t_fine);

    out->value[SEN_CH_TEMPERATURE] = (float)temperature;
    out->value[SEN_CH_HUMIDITY] = (float)humidity;
    out->value[SEN_CH_PRESSURE] = (float)(pressure_pa / 100.0); /* hPa */

    out->valid[SEN_CH_TEMPERATURE] = true;
    out->valid[SEN_CH_HUMIDITY] = true;
    out->valid[SEN_CH_PRESSURE] = true;

    /* The light channel needs a separate BH1750, which this build does NOT
     * implement. The channel is reported invalid so the change detector excludes
     * it, and the MQTT payload sends `null` rather than a plausible 0. */
    out->value[SEN_CH_LIGHT] = 0.0f;
    out->valid[SEN_CH_LIGHT] = false;
    return 0;
#endif /* CONFIG_AS_USE_MOCK_SENSOR */
}

bool sensor_is_initialized(void)
{
    return s_sensor_initialized;
}

const char *sensor_channel_name(sen_channel_t c)
{
    static const char *const names[SEN_CH_COUNT] = {
        "temperature", "humidity", "pressure", "light"
    };
    if ((int)c < 0 || (int)c >= (int)SEN_CH_COUNT) {
        return "unknown";
    }
    return names[c];
}
