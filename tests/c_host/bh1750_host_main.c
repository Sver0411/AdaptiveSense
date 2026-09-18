/*
 * bh1750_host_main.c — host driver for the BH1750 protocol and its availability
 * policy (`firmware/main/bh1750_proto.c`).
 *
 * Both halves are pure C99, so both can be exercised on a workstation with a
 * fake transport instead of an I2C bus. `tests/test_bh1750.py` uses it.
 *
 * Subcommands
 * -----------
 *   lux <raw>
 *       Print the lux value for a raw 16-bit count.
 *
 *   lux-table
 *       Print the boundary cases in one go: dark, the value observed on the real
 *       module, a normal room, bright, and the register maximum.
 *
 *   measure <scenario> [wait_ms]
 *       Run one full measurement against a fake transport:
 *         ok           two bytes arrive, conversion wait recorded
 *         write-fail   the command write fails
 *         short-read   only one byte arrives
 *         read-error   the read fails outright
 *         dark         frame 0x0000
 *         max          frame 0xFFFF
 *       Prints rc, the resulting lux, and how the transport was used — including
 *       the conversion wait, which is how "the wait is bounded" is checked.
 *
 *   policy <probe_interval_ms> <t:ok|fail,t:ok|fail,...>
 *       Replay the availability policy over a timeline. Each step is a sample at
 *       time t (ms) whose outcome is given; a step may also be `-` to mean "this
 *       sample happened, report what the policy would do". Prints one line per
 *       step with the policy's state after it.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bh1750_proto.h"

/* ------------------------------------------------------------------ */
/* fake transport                                                      */
/* ------------------------------------------------------------------ */
typedef enum {
    FAKE_OK = 0,
    FAKE_WRITE_FAIL,
    FAKE_SHORT_READ,
    FAKE_READ_ERROR
} fake_mode_t;

typedef struct {
    fake_mode_t mode;
    int writes;
    int reads;
    int delays;
    unsigned delay_total_ms;
    uint8_t frame[BH1750_FRAME_LEN];
} fake_io_t;

static int fake_write(void *ctx, const uint8_t *cmd, size_t len)
{
    fake_io_t *f = (fake_io_t *)ctx;
    f->writes++;
    (void)cmd;
    (void)len;
    return (f->mode == FAKE_WRITE_FAIL) ? -1 : 0;
}

static int fake_read(void *ctx, uint8_t *out, size_t len)
{
    fake_io_t *f = (fake_io_t *)ctx;
    f->reads++;
    if (f->mode == FAKE_READ_ERROR) {
        return -1;
    }
    if (f->mode == FAKE_SHORT_READ) {
        /* One byte only: a short frame must not be mistaken for a reading. */
        if (len > 0) {
            out[0] = f->frame[0];
        }
        return 1;
    }
    if (len != BH1750_FRAME_LEN) {
        return -1;
    }
    out[0] = f->frame[0];
    out[1] = f->frame[1];
    return (int)BH1750_FRAME_LEN;
}

static void fake_delay(void *ctx, unsigned ms)
{
    fake_io_t *f = (fake_io_t *)ctx;
    f->delays++;
    f->delay_total_ms += ms;
}

/* ------------------------------------------------------------------ */
/* subcommands                                                         */
/* ------------------------------------------------------------------ */
static int cmd_lux(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: lux <raw>\n");
        return 2;
    }
    const unsigned long raw = strtoul(argv[2], NULL, 0);
    printf("lux,raw=%lu,lux=%.4f\n", raw,
           (double)bh1750_lux_from_raw((uint16_t)raw));
    return 0;
}

static int cmd_lux_table(void)
{
    /* Boundary and reference values. 66 is the raw count the physical module
     * answered in the first hardware session, which is how the 1.2 divisor was
     * confirmed against real light rather than taken from a datasheet alone. */
    static const unsigned long raws[] = {0u, 1u, 66u, 100u, 1000u, 30000u,
                                         65534u, 65535u};
    for (size_t i = 0; i < sizeof(raws) / sizeof(raws[0]); i++) {
        printf("lux,raw=%lu,lux=%.4f\n", raws[i],
               (double)bh1750_lux_from_raw((uint16_t)raws[i]));
    }
    return 0;
}

static int cmd_measure(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: measure <scenario> [wait_ms]\n");
        return 2;
    }
    const char *scenario = argv[2];
    const unsigned wait_ms = (argc >= 4) ? (unsigned)strtoul(argv[3], NULL, 0)
                                         : BH1750_MEAS_WAIT_MS;

    fake_io_t fake;
    memset(&fake, 0, sizeof(fake));
    /* 66 counts = 55.0 lx, the frame seen on the real module. */
    fake.frame[0] = 0x00u;
    fake.frame[1] = 0x42u;

    if (strcmp(scenario, "ok") == 0) {
        fake.mode = FAKE_OK;
    } else if (strcmp(scenario, "write-fail") == 0) {
        fake.mode = FAKE_WRITE_FAIL;
    } else if (strcmp(scenario, "short-read") == 0) {
        fake.mode = FAKE_SHORT_READ;
    } else if (strcmp(scenario, "read-error") == 0) {
        fake.mode = FAKE_READ_ERROR;
    } else if (strcmp(scenario, "dark") == 0) {
        fake.mode = FAKE_OK;
        fake.frame[0] = 0x00u;
        fake.frame[1] = 0x00u;
    } else if (strcmp(scenario, "max") == 0) {
        fake.mode = FAKE_OK;
        fake.frame[0] = 0xFFu;
        fake.frame[1] = 0xFFu;
    } else {
        fprintf(stderr, "unknown scenario: %s\n", scenario);
        return 2;
    }

    const bh1750_io_t io = {
        .write = fake_write,
        .read = fake_read,
        .delay_ms = fake_delay,
        .ctx = &fake,
    };

    float lux = -1.0f;
    const int rc = bh1750_proto_measure(&io, wait_ms, &lux);
    printf("measure,%s,rc=%d,lux=%.4f,writes=%d,reads=%d,delays=%d,wait_ms=%u\n",
           scenario, rc, (double)lux, fake.writes, fake.reads, fake.delays,
           fake.delay_total_ms);
    return 0;
}

static int cmd_policy(int argc, char **argv)
{
    if (argc < 4) {
        fprintf(stderr, "usage: policy <probe_interval_ms> <t:result,...>\n");
        return 2;
    }
    const unsigned long probe_ms = strtoul(argv[2], NULL, 0);

    bh1750_state_t s;
    bh1750_state_init(&s, probe_ms, 0UL);
    printf("policy,init,available=%d,next_probe=%lu\n", (int)s.available,
           s.next_probe_ms);

    /* strtok() mutates its input, so work on a copy. malloc + memcpy rather than
     * strdup: strdup is POSIX, not ISO C, and glibc hides it under -std=c11. */
    const size_t n = strlen(argv[3]) + 1u;
    char *steps = malloc(n);
    if (steps == NULL) {
        return 2;
    }
    memcpy(steps, argv[3], n);

    for (char *tok = strtok(steps, ","); tok != NULL; tok = strtok(NULL, ",")) {
        char *colon = strchr(tok, ':');
        if (colon == NULL) {
            continue;
        }
        *colon = '\0';
        const unsigned long t = strtoul(tok, NULL, 0);
        const char *result = colon + 1;

        const int should = (int)bh1750_should_attempt(&s, t);
        if (strcmp(result, "-") == 0) {
            /* Just report what the policy would do, without changing it. */
        } else if (should) {
            bh1750_note_result(&s, strcmp(result, "ok") == 0, t);
        } else {
            bh1750_note_skip(&s);
        }
        printf("policy,t=%lu,should=%d,available=%d,consec=%u,reads=%u,fails=%u,"
               "skips=%u,events=%u\n",
               t, should, (int)s.available, s.consecutive_failures, s.reads,
               s.failures, s.skips, s.unavailability_events);
    }
    free(steps);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <lux|lux-table|measure|policy ...>\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "lux") == 0) {
        return cmd_lux(argc, argv);
    }
    if (strcmp(argv[1], "lux-table") == 0) {
        return cmd_lux_table();
    }
    if (strcmp(argv[1], "measure") == 0) {
        return cmd_measure(argc, argv);
    }
    if (strcmp(argv[1], "policy") == 0) {
        return cmd_policy(argc, argv);
    }
    fprintf(stderr, "unknown subcommand: %s\n", argv[1]);
    return 2;
}
