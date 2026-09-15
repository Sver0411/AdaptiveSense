/*
 * bme280_math.h — BME280 calibration parsing and compensation maths.
 *
 * This translation unit has NO dependency on ESP-IDF: it is pure C99 and can be
 * compiled and unit-tested on the host. `sensor.c` owns the I2C transport and
 * calls into these functions.
 *
 * The compensation equations are the vendor's double-precision implementations
 * from `BoschSensortec/BME280_SensorAPI` (`bme280.c`): `compensate_temperature`,
 * `compensate_pressure` and `compensate_humidity`, including their physical
 * range clamps. They are used verbatim rather than re-transcribed into the
 * integer/Q-format form, because that transcription is exactly where v0.1 went
 * wrong (see docs/audit_v0.2.md issues #1 and #1b).
 *
 * Register map used here (BME280 datasheet, Table 16):
 *
 *   0x88 .. 0xA1  26 bytes: dig_T1..dig_P9, then dig_H1 at offset 25
 *   0xE1 .. 0xE7   7 bytes: dig_H2 (2), dig_H3 (1), dig_H4 (1.5), dig_H5 (1.5), dig_H6 (1)
 */

#ifndef ADAPTIVESENSE_BME280_MATH_H
#define ADAPTIVESENSE_BME280_MATH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Sizes of the two calibration register blocks. */
#define BME280_CALIB_BLOCK1_ADDR 0x88u
#define BME280_CALIB_BLOCK1_LEN  26u /* 0x88..0xA1 inclusive */
#define BME280_CALIB_BLOCK2_ADDR 0xE1u
#define BME280_CALIB_BLOCK2_LEN  7u  /* 0xE1..0xE7 inclusive */
#define BME280_CALIB_BLOCK2_PAD  9u  /* so a single 9-byte buffer is always safe */

/* Physical range clamps used by the vendor driver. */
#define BME280_TEMP_MIN_C  (-40.0)
#define BME280_TEMP_MAX_C  (85.0)
#define BME280_PRESS_MIN_PA (30000.0)
#define BME280_PRESS_MAX_PA (110000.0)
#define BME280_HUM_MIN_PCT (0.0)
#define BME280_HUM_MAX_PCT (100.0)

/* Calibration coefficients (all values as described by the datasheet). */
typedef struct {
    uint16_t dig_T1;
    int16_t  dig_T2;
    int16_t  dig_T3;

    uint16_t dig_P1;
    int16_t  dig_P2;
    int16_t  dig_P3;
    int16_t  dig_P4;
    int16_t  dig_P5;
    int16_t  dig_P6;
    int16_t  dig_P7;
    int16_t  dig_P8;
    int16_t  dig_P9;

    uint8_t  dig_H1;
    int16_t  dig_H2;
    uint8_t  dig_H3;
    int16_t  dig_H4;   /* 12-bit signed */
    int16_t  dig_H5;   /* 12-bit signed */
    int8_t   dig_H6;
} bme280_calib_t;

/* Raw 20-bit uncompensated values, plus the raw 16-bit humidity. */
typedef struct {
    int32_t temperature; /* 20-bit */
    int32_t pressure;    /* 20-bit */
    int32_t humidity;    /* 16-bit */
} bme280_uncomp_t;

/* Sign-extend a 12-bit two's-complement field. */
int16_t bme280_sign_extend_12(uint16_t value);

/*
 * Parse the two calibration blocks into `out`.
 *
 * `block1` must hold BME280_CALIB_BLOCK1_LEN bytes read from 0x88 and `block2`
 * BME280_CALIB_BLOCK2_LEN bytes read from 0xE1. `block2` is declared with
 * BME280_CALIB_BLOCK2_PAD bytes of storage so that the compiler can verify any
 * access; nothing past BME280_CALIB_BLOCK2_LEN is read.
 *
 * Returns false if either pointer is NULL.
 */
bool bme280_parse_calibration(const uint8_t block1[BME280_CALIB_BLOCK1_LEN],
                              const uint8_t block2[BME280_CALIB_BLOCK2_LEN],
                              bme280_calib_t *out);

/* Extract the uncompensated values from the 8-byte burst read at 0xF7..0xFE. */
void bme280_parse_raw(const uint8_t data[8], bme280_uncomp_t *out);

/* Vendor compensation equations. Return values are in degC, Pa and %RH. */
double bme280_compensate_temperature(const bme280_calib_t *cal, int32_t adc_T,
                                     int32_t *t_fine_out);
double bme280_compensate_pressure(const bme280_calib_t *cal, int32_t adc_P,
                                  int32_t t_fine);
double bme280_compensate_humidity(const bme280_calib_t *cal, int32_t adc_H,
                                  int32_t t_fine);

#ifdef __cplusplus
}
#endif

#endif /* ADAPTIVESENSE_BME280_MATH_H */
