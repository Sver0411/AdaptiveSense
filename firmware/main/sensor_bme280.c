/*
 * sensor_bme280.c — BME280 backend.
 *
 * BME280 only. A BMP280 is a different part: it reports chip id 0x58 instead of
 * 0x60, has no humidity registers, and is therefore rejected by the chip-id check
 * below rather than silently mis-driven. Do not describe this backend as
 * supporting BMP280 — it does not, and it will refuse one.
 *
 * Register-level driver: calibration parsing and the compensation equations live
 * in bme280_math.c (pure C, host-tested), and this file owns the transport and the
 * forced-mode measurement sequence.
 *
 * This backend is unchanged in behaviour from when it lived inside sensor.c — it
 * was moved, not rewritten, so that a second backend could exist alongside it.
 * It remains the only backend that can produce a pressure channel.
 *
 * Measurement model — FORCED MODE (see docs/hardware.md):
 *
 *   wake -> trigger one conversion -> wait for it (bounded) -> read ->
 *   evaluate -> sleep
 *
 * The sensor is never left converting while the MCU sleeps, and it is never in
 * normal mode sampling in the background. `ctrl_meas` bits [1:0] are therefore
 * always written with 0b01 (forced).
 *
 * The bus is owned by sensor_bus.c and shared with the other devices on the same
 * two wires; this backend only registers its own device handle on it.
 */
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "bme280_math.h"
#include "config_include.h"
#include "sensor_backend.h"
#include "sensor_bus.h"

static const char *TAG = "bme280";

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

static i2c_master_dev_handle_t s_dev = NULL;
static bme280_calib_t s_cal;

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

/* ------------------------------------------------------------------ */
/* backend interface                                                   */
/* ------------------------------------------------------------------ */
static void bme280_teardown(void)
{
    if (s_dev != NULL) {
        i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
}

static int bme280_init(void)
{
    i2c_master_bus_handle_t bus = sensor_bus_acquire();
    if (bus == NULL) {
        return -1;
    }

    /* A retry must not inherit the previous attempt's device handle. */
    bme280_teardown();

    const i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = CONFIG_AS_BME280_I2C_ADDR,
        .scl_speed_hz = CONFIG_AS_I2C_FREQ_HZ,
    };
    if (i2c_master_bus_add_device(bus, &dev_config, &s_dev) != ESP_OK) {
        s_dev = NULL;
        ESP_LOGE(TAG, "i2c device add failed");
        return -1;
    }

    esp_err_t err = i2c_master_probe(bus, CONFIG_AS_BME280_I2C_ADDR,
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

    ESP_LOGI(TAG, "BME280 ready at 0x%02x (forced measurement mode, timeout %d ms; "
                  "channels: temperature, humidity, pressure)",
             (unsigned)CONFIG_AS_BME280_I2C_ADDR,
             (int)CONFIG_AS_BME280_MEAS_TIMEOUT_MS);
    return 0;

fail:
    bme280_teardown();
    return -1;
}

static int bme280_read(sensor_read_t *out)
{
    if (out == NULL || s_dev == NULL) {
        return -1;
    }

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
}

const sensor_backend_t sensor_backend_bme280 = {
    .name = "BME280 (temperature + humidity + pressure)",
    .init = bme280_init,
    .read = bme280_read,
    .teardown = bme280_teardown,
};
