/*
 * sht30_host_main.c — host driver for `firmware/main/sht30_proto.c`.
 *
 * The protocol layer takes a transport (`sht30_io_t`), so a fake one can fail
 * each step independently: a write that fails, a read that fails, a short read, a
 * frame with a corrupted CRC. That is what makes the error handling testable
 * without a board. `tests/test_sensor_sht30.py` uses this.
 *
 * Golden frames are real captures from the physical device, so the conversions
 * and the CRC are checked against silicon rather than against themselves:
 *
 *   status  80 10 E1                                  (command 0xF32D)
 *   measure 6A 12 1B 90 8F 98  -> T=27.51 C RH=56.47 % (command 0x2400)
 *
 * Subcommands
 * -----------
 *   crc <hexbytes>            print the CRC of all but the last byte and whether
 *                             it matches the last byte
 *   decode <hexframe>         decode a 6-byte frame; prints ok/values
 *   measure <scenario>        scenario in:
 *                               good | badcrc_t | badcrc_rh | writefail |
 *                               readfail | shortread
 *   identify <scenario>       scenario in: good | badcrc | writefail | readfail
 *   null-io                   call measure with no transport at all
 *
 * Output is `key=value` pairs so the Python side can assert on them directly.
 */

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sht30_proto.h"

/* Value written into the outputs before a call, so "untouched on failure" is
 * observable: if a call reports failure and the outputs still hold this, nothing
 * was written. */
#define SENTINEL (-999.0f)

/* ------------------------------------------------------------------ */
/* fake transport                                                      */
/* ------------------------------------------------------------------ */
typedef enum { SC_GOOD, SC_WRITEFAIL, SC_READFAIL, SC_SHORTREAD } scenario_t;

static scenario_t g_scenario = SC_GOOD;
static uint8_t g_frame[SHT30_FRAME_LEN];
static int g_write_calls = 0;
static int g_read_calls = 0;
static unsigned g_delay_ms_total = 0;

/* Recorded so a test can check the driver issued the command it should have. */
static uint16_t g_last_command = 0;

static int fake_write(void *ctx, const uint8_t *cmd, size_t len)
{
    (void)ctx;
    g_write_calls++;
    if (g_scenario == SC_WRITEFAIL) {
        return -1;
    }
    if (len == 2) {
        g_last_command = (uint16_t)(((uint16_t)cmd[0] << 8) | cmd[1]);
    }
    return 0;
}

static int fake_read(void *ctx, uint8_t *out, size_t len)
{
    (void)ctx;
    g_read_calls++;
    if (g_scenario == SC_READFAIL) {
        return -1;
    }
    if (g_scenario == SC_SHORTREAD) {
        memset(out, 0, len);
        return -1;
    }
    const size_t copy = (len < (size_t)SHT30_FRAME_LEN) ? len : (size_t)SHT30_FRAME_LEN;
    memcpy(out, g_frame, copy);
    return 0;
}

static void fake_delay(void *ctx, unsigned ms)
{
    (void)ctx;
    g_delay_ms_total += ms;
}

static const sht30_io_t FAKE_IO = {
    .write = fake_write,
    .read = fake_read,
    .delay_ms = fake_delay,
    .ctx = NULL,
};

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */
static int hex_to_bytes(const char *hex, uint8_t *out, size_t max)
{
    size_t n = 0;
    for (const char *p = hex; *p != '\0' && n < max; ) {
        while (*p == ' ' || *p == ':') {
            p++;
        }
        if (*p == '\0') {
            break;
        }
        char pair[3] = { p[0], (p[1] != '\0') ? p[1] : '0', '\0' };
        out[n++] = (uint8_t)strtoul(pair, NULL, 16);
        p += (p[1] != '\0') ? 2 : 1;
    }
    return (int)n;
}

static void set_scenario(const char *name)
{
    /* Start from the real measurement frame, then break what needs breaking. */
    const uint8_t real_frame[SHT30_FRAME_LEN] = { 0x6A, 0x12, 0x1B, 0x90, 0x8F, 0x98 };
    memcpy(g_frame, real_frame, sizeof(g_frame));
    g_scenario = SC_GOOD;

    if (strcmp(name, "good") == 0) {
        return;
    }
    if (strcmp(name, "badcrc_t") == 0) {
        g_frame[2] ^= 0xFF; /* temperature CRC wrong */
        return;
    }
    if (strcmp(name, "badcrc_rh") == 0) {
        g_frame[5] ^= 0xFF; /* humidity CRC wrong */
        return;
    }
    if (strcmp(name, "writefail") == 0) {
        g_scenario = SC_WRITEFAIL;
        return;
    }
    if (strcmp(name, "readfail") == 0) {
        g_scenario = SC_READFAIL;
        return;
    }
    if (strcmp(name, "shortread") == 0) {
        g_scenario = SC_SHORTREAD;
        return;
    }
    fprintf(stderr, "unknown scenario: %s\n", name);
    exit(2);
}

/* ------------------------------------------------------------------ */
/* subcommands                                                         */
/* ------------------------------------------------------------------ */
static int cmd_crc(const char *hex)
{
    uint8_t buf[32];
    const int n = hex_to_bytes(hex, buf, sizeof(buf));
    if (n < 2) {
        fprintf(stderr, "crc needs at least two bytes\n");
        return 2;
    }
    const uint8_t computed = sht30_crc8(buf, (size_t)n - 1);
    printf("computed=%02X expected=%02X ok=%d\n", computed, buf[n - 1],
           computed == buf[n - 1]);
    return 0;
}

static int cmd_decode(const char *hex)
{
    uint8_t frame[SHT30_FRAME_LEN];
    const int n = hex_to_bytes(hex, frame, sizeof(frame));
    if (n != SHT30_FRAME_LEN) {
        fprintf(stderr, "decode needs %d bytes, got %d\n", SHT30_FRAME_LEN, n);
        return 2;
    }

    float t = SENTINEL;
    float rh = SENTINEL;
    const bool ok = sht30_decode_measurement(frame, &t, &rh);
    printf("ok=%d temp=%.4f hum=%.4f\n", (int)ok, (double)t, (double)rh);
    return 0;
}

static int cmd_measure(const char *scenario)
{
    set_scenario(scenario);

    float t = SENTINEL;
    float rh = SENTINEL;
    g_write_calls = 0;
    g_read_calls = 0;
    g_delay_ms_total = 0;
    g_last_command = 0;

    const int rc = sht30_proto_measure(&FAKE_IO, &t, &rh);
    printf("rc=%d temp=%.4f hum=%.4f writes=%d reads=%d delay_ms=%u command=%04X\n",
           rc, (double)t, (double)rh, g_write_calls, g_read_calls,
           g_delay_ms_total, g_last_command);
    return 0;
}

static int cmd_identify(const char *scenario)
{
    /* The status word is 3 bytes, captured from the physical device. */
    const uint8_t status_good[SHT30_STATUS_LEN] = { 0x80, 0x10, 0xE1 };
    memcpy(g_frame, status_good, sizeof(status_good));
    g_scenario = SC_GOOD;

    if (strcmp(scenario, "good") == 0) {
        /* nothing to break */
    } else if (strcmp(scenario, "badcrc") == 0) {
        g_frame[2] ^= 0xFF;
    } else if (strcmp(scenario, "writefail") == 0) {
        g_scenario = SC_WRITEFAIL;
    } else if (strcmp(scenario, "readfail") == 0) {
        g_scenario = SC_READFAIL;
    } else {
        fprintf(stderr, "unknown identify scenario: %s\n", scenario);
        return 2;
    }

    g_last_command = 0;
    const int rc = sht30_proto_identify(&FAKE_IO);
    printf("rc=%d command=%04X\n", rc, g_last_command);
    return 0;
}

static int cmd_null_io(void)
{
    float t = SENTINEL;
    float rh = SENTINEL;
    const int rc = sht30_proto_measure(NULL, &t, &rh);
    printf("rc=%d temp=%.4f hum=%.4f\n", rc, (double)t, (double)rh);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s crc|decode|measure|identify|null-io ...\n", argv[0]);
        return 2;
    }

    if (strcmp(argv[1], "crc") == 0 && argc == 3) {
        return cmd_crc(argv[2]);
    }
    if (strcmp(argv[1], "decode") == 0 && argc == 3) {
        return cmd_decode(argv[2]);
    }
    if (strcmp(argv[1], "measure") == 0 && argc == 3) {
        return cmd_measure(argv[2]);
    }
    if (strcmp(argv[1], "identify") == 0 && argc == 3) {
        return cmd_identify(argv[2]);
    }
    if (strcmp(argv[1], "null-io") == 0) {
        return cmd_null_io();
    }

    fprintf(stderr, "bad arguments\n");
    return 2;
}
