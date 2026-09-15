/*
 * sensor.c — BME280 (I2C) driver + optional mock sensor.
 *
 * The BME280 part is a compact register-level driver: it reads the chip's
 * calibration constants at init and compensates raw sensor registers per the
 * BME280 datasheet equations. If CONFIG_AS_USE_MOCK_SENSOR is set, a
 * deterministic mock is substituted instead (never presented as real data).
 */
#include <math.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "sensor.h"
#include "config.h"

static const char *TAG = "sensor";

#if CONFIG_AS_USE_MOCK_SENSOR

/* ------------------------------------------------------------------ */
/* Mock sensor: reproducible synthetic sequence (documented in README). */
/* ------------------------------------------------------------------ */
static void mock_fill(sensor_read_t *out)
{
    static uint32_t step = 0;
    float t = 24.0f + 0.08f * sinf((float)step * 0.1f);
    float h = 45.0f + 0.4f * sinf((float)step * 0.07f);
    for (int i = 0; i < SEN_CH_COUNT; i++) out->valid[i] = true;
    out->value[SEN_CH_TEMPERATURE] = t;
    out->value[SEN_CH_HUMIDITY] = h;
    out->value[SEN_CH_PRESSURE] = 1012.4f;
    out->value[SEN_CH_LIGHT] = 320.0f + 3.0f * sinf((float)step * 0.02f);
    step++;
}

#endif /* MOCK */

/* ------------------------------------------------------------------ */
/* BME280 registers                                                    */
/* ------------------------------------------------------------------ */
#define BME280_ID_REG         0xD0
#define BME280_RESET_REG      0xE0
#define BME280_CTRL_HUM       0xF2
#define BME280_STATUS         0xF3
#define BME280_CTRL_MEAS      0xF4
#define BME280_CONFIG         0xF5
#define BME280_PRESS_DATA     0xF7
#define BME280_CHIPID         0x60
#define BME280_RESET_CMD      0xB6

typedef struct {
    uint16_t dig_T1; int16_t dig_T2, dig_T3;
    uint16_t dig_P1; int16_t dig_P2, dig_P3, dig_P4, dig_P5, dig_P6, dig_P7, dig_P8, dig_P9;
    uint8_t  dig_H1; int16_t dig_H2; uint8_t dig_H3;
    int16_t  dig_H4, dig_H5; int8_t dig_H6;
    int32_t  t_fine;
} bme280_cal_t;

static bme280_cal_t bme_cal;
static uint8_t i2c_addr = CONFIG_AS_BME280_I2C_ADDR;

static esp_err_t bme_write(uint8_t reg, uint8_t data)
{
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (i2c_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_write_byte(cmd, data, true);
    i2c_master_stop(cmd);
    esp_err_t err = i2c_master_cmd_begin(I2C_NUM_0, cmd, pdMS_TO_TICKS(100));
    i2c_cmd_link_delete(cmd);
    return err;
}

static esp_err_t bme_read(uint8_t reg, uint8_t *buf, size_t len)
{
    i2c_cmd_handle_t cmd = i2c_cmd_link_create();
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (i2c_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_write_byte(cmd, reg, true);
    i2c_master_start(cmd);
    i2c_master_write_byte(cmd, (i2c_addr << 1) | I2C_MASTER_READ, true);
    if (len > 1) {
        i2c_master_read(cmd, buf, len - 1, I2C_MASTER_ACK);
    }
    i2c_master_read_byte(cmd, &buf[len - 1], I2C_MASTER_NACK);
    i2c_master_stop(cmd);
    esp_err_t err = i2c_master_cmd_begin(I2C_NUM_0, cmd, pdMS_TO_TICKS(100));
    i2c_cmd_link_delete(cmd);
    return err;
}

static void bme_read_calibration(void)
{
    uint8_t buf[26];
    if (bme_read(0x88, buf, 26) != ESP_OK) return;
    bme_cal.dig_T1 = (uint16_t)(buf[0] | (buf[1] << 8));
    bme_cal.dig_T2 = (int16_t)(buf[2] | (buf[3] << 8));
    bme_cal.dig_T3 = (int16_t)(buf[4] | (buf[5] << 8));
    bme_cal.dig_P1 = (uint16_t)(buf[6] | (buf[7] << 8));
    bme_cal.dig_P2 = (int16_t)(buf[8] | (buf[9] << 8));
    bme_cal.dig_P3 = (int16_t)(buf[10] | (buf[11] << 8));
    bme_cal.dig_P4 = (int16_t)(buf[12] | (buf[13] << 8));
    bme_cal.dig_P5 = (int16_t)(buf[14] | (buf[15] << 8));
    bme_cal.dig_P6 = (int16_t)(buf[16] | (buf[17] << 8));
    bme_cal.dig_P7 = (int16_t)(buf[18] | (buf[19] << 8));
    bme_cal.dig_P8 = (int16_t)(buf[20] | (buf[21] << 8));
    bme_cal.dig_P9 = (int16_t)(buf[22] | (buf[23] << 8));

    bme_read(0xE1, buf, 7);
    bme_cal.dig_H1 = buf[0];
    bme_cal.dig_H2 = (int16_t)(buf[1] | (buf[2] << 8));
    bme_cal.dig_H3 = buf[3];
    bme_cal.dig_H4 = (int16_t)((buf[4] << 4) | (buf[5] & 0x0F));
    bme_cal.dig_H5 = (int16_t)((buf[6] << 4) | ((buf[5] >> 4) & 0x0F));
    bme_cal.dig_H6 = (int8_t)buf[7];
    (void)buf[8];
}

static int32_t bme_compensate_temp(int32_t adc_t)
{
    int32_t var1 = ((((adc_t >> 3) - ((int32_t)bme_cal.dig_T1 << 1))) *
                    (int32_t)bme_cal.dig_T2) >> 11;
    int32_t var2 = (((((adc_t >> 4) - (int32_t)bme_cal.dig_T1) *
                      ((adc_t >> 4) - (int32_t)bme_cal.dig_T1)) >> 12) *
                    (int32_t)bme_cal.dig_T3) >> 14;
    bme_cal.t_fine = var1 + var2;
    return (bme_cal.t_fine * 5 + 128) >> 8;   /* degC * 100 */
}

/* Returns pressure in Pa (float) using the BME280 Q1 equation with int64
 * intermediates to avoid overflow. */
static float bme_compensate_press(int32_t adc_p)
{
    int64_t var1 = (int64_t)bme_cal.t_fine - 128000;
    int64_t var2 = var1 * var1 * bme_cal.dig_P6;
    var2 = var2 + ((var1 * (int64_t)bme_cal.dig_P5) << 17);
    var2 = var2 + (((int64_t)bme_cal.dig_P4) << 35);
    var1 = ((var1 * var1 * bme_cal.dig_P3) >> 8) +
           ((var1 * (int64_t)bme_cal.dig_P2) << 12);
    var1 = (((((int64_t)1) << 47) + var1)) * bme_cal.dig_P1 >> 33;
    if (var1 == 0) return 0.0f;

    int64_t p = 1048576 - adc_p;
    p = (((p << 31) - var2) * 3125) / var1;
    var1 = ((int64_t)bme_cal.dig_P9 * (p >> 13) * (p >> 13)) >> 25;
    var2 = ((int64_t)bme_cal.dig_P8 * p) >> 19;
    p = ((p + var1 + var2) >> 8) + ((int64_t)bme_cal.dig_P7 << 4);
    return (float)p / 256.0f;   /* Pa */
}

/* Returns humidity in %RH*1024 (int). */
static int32_t bme_compensate_hum(int32_t adc_h)
{
    int32_t x1 = bme_cal.t_fine - 76800;
    x1 = ((((adc_h << 14) - (bme_cal.dig_H4 << 20) -
            (bme_cal.dig_H5 * x1)) + 16384) >> 15) *
         (((((((x1 * bme_cal.dig_H6) >> 10) *
              (((x1 * bme_cal.dig_H3) >> 11) + 32768)) >> 10)
            + 2097152) * bme_cal.dig_H2 + 8192) >> 14);
    x1 = x1 - (((((x1 >> 15) * (x1 >> 15)) >> 7) * bme_cal.dig_H1) >> 4);
    if (x1 < 0) x1 = 0;
    if (x1 > 419430400) x1 = 419430400;
    return x1;
}

int sensor_init(void)
{
#if CONFIG_AS_USE_MOCK_SENSOR
    ESP_LOGW(TAG, "MOCK sensor enabled - results are NOT real measurements");
    return 0;
#else
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = CONFIG_AS_SENSOR_SDA_GPIO,
        .scl_io_num = CONFIG_AS_SENSOR_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = CONFIG_AS_I2C_FREQ_HZ,
    };
    esp_err_t err = i2c_param_config(I2C_NUM_0, &conf);
    if (err != ESP_OK) { ESP_LOGE(TAG, "i2c config failed: %s", esp_err_to_name(err)); return -1; }
    err = i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK) { ESP_LOGE(TAG, "i2c install failed: %s", esp_err_to_name(err)); return -1; }

    i2c_cmd_handle_t probe = i2c_cmd_link_create();
    i2c_master_start(probe);
    i2c_master_write_byte(probe, (i2c_addr << 1) | I2C_MASTER_WRITE, true);
    i2c_master_stop(probe);
    err = i2c_master_cmd_begin(I2C_NUM_0, probe, pdMS_TO_TICKS(100));
    i2c_cmd_link_delete(probe);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "BME280 not found at 0x%02x: %s", i2c_addr, esp_err_to_name(err));
        return -1;
    }

    esp_err_t r;
    uint8_t id = 0;
    r = bme_read(BME280_ID_REG, &id, 1);
    ESP_LOGI(TAG, "BME280 chip id=0x%02x (read err=%s)", id, esp_err_to_name(r));
    if (id != BME280_CHIPID) ESP_LOGW(TAG, "unexpected chip id (is this a BME280?)");

    bme_write(BME280_RESET_REG, BME280_RESET_CMD);
    vTaskDelay(pdMS_TO_TICKS(10));

    bme_read_calibration();
    /* ctrl_meas: osrs_t=1<<5 | osrs_p=1<<2 | mode=01 (normal); ctrl_hum osrs_h=1 */
    bme_write(BME280_CTRL_HUM, 0x01);
    bme_write(BME280_CTRL_MEAS, (0x01 << 5) | (0x01 << 2) | 0x01);
    bme_write(BME280_CONFIG, (4 << 5)); /* t_sb=0 -> cycling ~ see datasheet */
    return 0;
#endif
}

int sensor_read(sensor_read_t *out)
{
    memset(out, 0, sizeof(*out));
#if CONFIG_AS_USE_MOCK_SENSOR
    mock_fill(out);
    return 0;
#else
    uint8_t buf[8];
    if (bme_read(BME280_PRESS_DATA, buf, 8) != ESP_OK) return -1;

    int32_t adc_p = (int32_t)(((buf[0] << 16) | (buf[1] << 8) | buf[2]) >> 4);
    int32_t adc_t = (int32_t)(((buf[3] << 16) | (buf[4] << 8) | buf[5]) >> 4);
    int32_t adc_h = (int32_t)((buf[6] << 8) | buf[7]);

    int32_t t100 = bme_compensate_temp(adc_t);   /* degC * 100 */
    float press_pa = bme_compensate_press(adc_p);/* Pa */
    int32_t h1024 = bme_compensate_hum(adc_h);   /* %RH * 1024 */

    out->value[SEN_CH_TEMPERATURE] = (float)t100 / 100.0f;
    out->value[SEN_CH_HUMIDITY] = (float)h1024 / 1024.0f;
    out->value[SEN_CH_PRESSURE] = press_pa / 100.0f;   /* hPa */
    out->value[SEN_CH_LIGHT] = 0.0f;

    out->valid[SEN_CH_TEMPERATURE] = true;
    out->valid[SEN_CH_HUMIDITY] = true;
    out->valid[SEN_CH_PRESSURE] = true;
    out->valid[SEN_CH_LIGHT] = false;   /* BH1750 optional / not present */
    return 0;
#endif
}

const char *sensor_channel_name(sen_channel_t c)
{
    static const char *names[SEN_CH_COUNT] = {"temperature", "humidity", "pressure", "light"};
    return names[c];
}