/*
 * bme280_host_main.c — host harness for `firmware/main/bme280_math.c`.
 *
 * `bme280_math.c` has no ESP-IDF dependency, so the calibration parsing and the
 * compensation equations can be exercised on the host. This harness prints the
 * parsed coefficients and the compensated values for a supplied register image,
 * and `tests/test_bme280_math.py` compares them against an independent Python
 * transcription of the vendor (`BoschSensortec/BME280_SensorAPI`) equations.
 *
 * Usage:
 *   bme280_host <hex_block1_26bytes> <hex_block2_7bytes> <adc_T> <adc_P> <adc_H>
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bme280_math.h"

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int parse_hex(const char *text, uint8_t *out, size_t len)
{
    if (strlen(text) != len * 2u) {
        return -1;
    }
    for (size_t i = 0; i < len; i++) {
        const int hi = hex_nibble(text[i * 2u]);
        const int lo = hex_nibble(text[i * 2u + 1u]);
        if (hi < 0 || lo < 0) {
            return -1;
        }
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 6) {
        fprintf(stderr,
                "usage: %s <hex1> <hex2> <adc_T> <adc_P> <adc_H>\n", argv[0]);
        return 2;
    }

    uint8_t block1[BME280_CALIB_BLOCK1_LEN];
    uint8_t block2[BME280_CALIB_BLOCK2_PAD];
    memset(block2, 0xAA, sizeof(block2)); /* poison: parser must not read past 7 */

    if (parse_hex(argv[1], block1, sizeof(block1)) != 0) {
        fprintf(stderr, "block1 must be %u hex bytes\n", BME280_CALIB_BLOCK1_LEN);
        return 2;
    }
    if (parse_hex(argv[2], block2, BME280_CALIB_BLOCK2_LEN) != 0) {
        fprintf(stderr, "block2 must be %u hex bytes\n", BME280_CALIB_BLOCK2_LEN);
        return 2;
    }

    const int32_t adc_T = (int32_t)strtol(argv[3], NULL, 10);
    const int32_t adc_P = (int32_t)strtol(argv[4], NULL, 10);
    const int32_t adc_H = (int32_t)strtol(argv[5], NULL, 10);

    bme280_calib_t cal;
    if (!bme280_parse_calibration(block1, block2, &cal)) {
        fprintf(stderr, "calibration parse failed\n");
        return 1;
    }

    printf("dig_T1,%u\n", (unsigned)cal.dig_T1);
    printf("dig_T2,%d\n", (int)cal.dig_T2);
    printf("dig_T3,%d\n", (int)cal.dig_T3);
    printf("dig_P1,%u\n", (unsigned)cal.dig_P1);
    printf("dig_P2,%d\n", (int)cal.dig_P2);
    printf("dig_P3,%d\n", (int)cal.dig_P3);
    printf("dig_P4,%d\n", (int)cal.dig_P4);
    printf("dig_P5,%d\n", (int)cal.dig_P5);
    printf("dig_P6,%d\n", (int)cal.dig_P6);
    printf("dig_P7,%d\n", (int)cal.dig_P7);
    printf("dig_P8,%d\n", (int)cal.dig_P8);
    printf("dig_P9,%d\n", (int)cal.dig_P9);
    printf("dig_H1,%u\n", (unsigned)cal.dig_H1);
    printf("dig_H2,%d\n", (int)cal.dig_H2);
    printf("dig_H3,%u\n", (unsigned)cal.dig_H3);
    printf("dig_H4,%d\n", (int)cal.dig_H4);
    printf("dig_H5,%d\n", (int)cal.dig_H5);
    printf("dig_H6,%d\n", (int)cal.dig_H6);

    int32_t t_fine = 0;
    const double temperature = bme280_compensate_temperature(&cal, adc_T, &t_fine);
    const double pressure = bme280_compensate_pressure(&cal, adc_P, t_fine);
    const double humidity = bme280_compensate_humidity(&cal, adc_H, t_fine);

    printf("t_fine,%d\n", (int)t_fine);
    printf("temperature_c,%.9f\n", temperature);
    printf("pressure_pa,%.9f\n", pressure);
    printf("humidity_pct,%.9f\n", humidity);
    return 0;
}
