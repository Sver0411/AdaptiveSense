/*
 * bme280_math.c — see bme280_math.h.
 *
 * Pure C99, no ESP-IDF dependency, host-testable.
 *
 * Attribution: the three compensation equations and their clamps are taken from
 * the vendor driver `BoschSensortec/BME280_SensorAPI` (`bme280.c`,
 * `compensate_temperature` / `compensate_pressure` / `compensate_humidity`,
 * BSD-3-Clause). They are reproduced here in the same form so that they can be
 * diffed against upstream instead of being re-derived.
 */

#include "bme280_math.h"

#include <math.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* little-endian field helpers                                         */
/* ------------------------------------------------------------------ */
static uint16_t rd_u16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static int16_t rd_s16(const uint8_t *p)
{
    return (int16_t)rd_u16(p);
}

int16_t bme280_sign_extend_12(uint16_t value)
{
    /* 12-bit two's complement: bit 11 is the sign bit. */
    value &= 0x0FFFu;
    if (value & 0x0800u) {
        return (int16_t)(value - 0x1000u);
    }
    return (int16_t)value;
}

/* ------------------------------------------------------------------ */
/* calibration                                                         */
/* ------------------------------------------------------------------ */
bool bme280_parse_calibration(const uint8_t block1[BME280_CALIB_BLOCK1_LEN],
                              const uint8_t block2[BME280_CALIB_BLOCK2_LEN],
                              bme280_calib_t *out)
{
    if (block1 == NULL || block2 == NULL || out == NULL) {
        return false;
    }

    memset(out, 0, sizeof(*out));

    /* dig_H1 lives at 0xA1 and shares the 26-byte burst read that starts at
     * 0x88, so it is the last byte of block1. The humidity block at 0xE1 does
     * NOT contain it — taking it from there was a v0.1 defect. */
    out->dig_H1 = block1[25];

    out->dig_T1 = rd_u16(&block1[0]);
    out->dig_T2 = rd_s16(&block1[2]);
    out->dig_T3 = rd_s16(&block1[4]);

    out->dig_P1 = rd_u16(&block1[6]);
    out->dig_P2 = rd_s16(&block1[8]);
    out->dig_P3 = rd_s16(&block1[10]);
    out->dig_P4 = rd_s16(&block1[12]);
    out->dig_P5 = rd_s16(&block1[14]);
    out->dig_P6 = rd_s16(&block1[16]);
    out->dig_P7 = rd_s16(&block1[18]);
    out->dig_P8 = rd_s16(&block1[20]);
    out->dig_P9 = rd_s16(&block1[22]);

    /* Humidity 0xE1..0xE7, offsets 0..6 (never touched past the declared length) */
    out->dig_H2 = rd_s16(&block2[0]);                       /* 0xE1/0xE2 */
    out->dig_H3 = block2[2];                                /* 0xE3      */
    out->dig_H4 = bme280_sign_extend_12(                   /* 0xE4, 0xE5[3:0] */
        (uint16_t)(((uint16_t)block2[3] << 4) | (block2[4] & 0x0Fu)));
    out->dig_H5 = bme280_sign_extend_12(                   /* 0xE5[7:4], 0xE6 */
        (uint16_t)(((uint16_t)block2[5] << 4) | ((block2[4] >> 4) & 0x0Fu)));
    out->dig_H6 = (int8_t)block2[6];                        /* 0xE7      */

    return true;
}

void bme280_parse_raw(const uint8_t data[8], bme280_uncomp_t *out)
{
    if (data == NULL || out == NULL) {
        return;
    }
    out->pressure =
        (int32_t)((((uint32_t)data[0] << 16) | ((uint32_t)data[1] << 8) |
                   (uint32_t)data[2]) >> 4);
    out->temperature =
        (int32_t)((((uint32_t)data[3] << 16) | ((uint32_t)data[4] << 8) |
                   (uint32_t)data[5]) >> 4);
    out->humidity = (int32_t)(((uint32_t)data[6] << 8) | (uint32_t)data[7]);
}

/* ------------------------------------------------------------------ */
/* compensation (vendor double-precision equations)                    */
/* ------------------------------------------------------------------ */
double bme280_compensate_temperature(const bme280_calib_t *cal, int32_t adc_T,
                                     int32_t *t_fine_out)
{
    double var1;
    double var2;
    double temperature;

    var1 = ((double)adc_T) / 16384.0 - ((double)cal->dig_T1) / 1024.0;
    var1 = var1 * ((double)cal->dig_T2);
    var2 = ((double)adc_T) / 131072.0 - ((double)cal->dig_T1) / 8192.0;
    var2 = (var2 * var2) * ((double)cal->dig_T3);

    if (t_fine_out != NULL) {
        *t_fine_out = (int32_t)(var1 + var2);
    }

    temperature = (var1 + var2) / 5120.0;
    if (temperature < BME280_TEMP_MIN_C) {
        temperature = BME280_TEMP_MIN_C;
    } else if (temperature > BME280_TEMP_MAX_C) {
        temperature = BME280_TEMP_MAX_C;
    }
    return temperature;
}

double bme280_compensate_pressure(const bme280_calib_t *cal, int32_t adc_P,
                                  int32_t t_fine)
{
    double var1;
    double var2;
    double var3;
    double pressure;
    double pressure_min = BME280_PRESS_MIN_PA;
    double pressure_max = BME280_PRESS_MAX_PA;

    var1 = ((double)t_fine / 2.0) - 64000.0;
    var2 = var1 * var1 * ((double)cal->dig_P6) / 32768.0;
    var2 = var2 + var1 * ((double)cal->dig_P5) * 2.0;
    var2 = (var2 / 4.0) + (((double)cal->dig_P4) * 65536.0);
    var3 = ((double)cal->dig_P3) * var1 * var1 / 524288.0;
    var1 = (var3 + ((double)cal->dig_P2) * var1) / 524288.0;
    var1 = (1.0 + var1 / 32768.0) * ((double)cal->dig_P1);

    if (var1 == 0.0) {
        return pressure_min; /* avoid a division by zero */
    }

    pressure = 1048576.0 - (double)adc_P;
    pressure = (pressure - (var2 / 4096.0)) * 6250.0 / var1;
    var1 = ((double)cal->dig_P9) * pressure * pressure / 2147483648.0;
    var2 = pressure * ((double)cal->dig_P8) / 32768.0;
    pressure = pressure + (var1 + var2 + ((double)cal->dig_P7)) / 16.0;

    if (pressure < pressure_min) {
        pressure = pressure_min;
    } else if (pressure > pressure_max) {
        pressure = pressure_max;
    }
    return pressure;
}

double bme280_compensate_humidity(const bme280_calib_t *cal, int32_t adc_H,
                                  int32_t t_fine)
{
    double humidity;
    double var1;
    double var2;
    double var3;
    double var4;
    double var5;
    double var6;

    var1 = ((double)t_fine) - 76800.0;
    var2 = (((double)cal->dig_H4) * 64.0) + (((double)cal->dig_H5) / 16384.0) * var1;
    var3 = ((double)adc_H) - var2;
    var4 = ((double)cal->dig_H2) / 65536.0;
    var5 = 1.0 + (((double)cal->dig_H3) / 67108864.0) * var1;
    var6 = 1.0 + (((double)cal->dig_H6) / 67108864.0) * var1 * var5;
    var6 = var3 * var4 * (var5 * var6);
    humidity = var6 * (1.0 - ((double)cal->dig_H1) * var6 / 524288.0);

    if (humidity > BME280_HUM_MAX_PCT) {
        humidity = BME280_HUM_MAX_PCT;
    } else if (humidity < BME280_HUM_MIN_PCT) {
        humidity = BME280_HUM_MIN_PCT;
    }
    return humidity;
}
