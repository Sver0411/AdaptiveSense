/*
 * sensor.c — the sensor API: contract, backend selection, mock override.
 *
 * AdaptiveSense is sensor-agnostic. This file knows nothing about any particular
 * chip; it owns
 *
 *   * the public read contract (nothing is read before a successful init),
 *   * the choice of backend (CONFIG_AS_SENSOR_BACKEND), and
 *   * the mock override used when no hardware is attached.
 *
 * and delegates the actual work to a `sensor_backend_t`. Two backends ship:
 * BME280 (register-level, with pressure) and SHT30/SHT3x (command-based,
 * temperature and humidity). See sensor_backend.h.
 *
 * Backends report what they cannot measure by leaving the channel invalid, which
 * is exactly how the change detector already decides which channels to score — so
 * swapping the sensor changes which numbers arrive, and never how they are used.
 *
 * Initialisation contract
 * -----------------------
 * `sensor_read()` refuses to do anything until `sensor_init()` has reported
 * success. On a failure after init, the return value is -1 and no channel is
 * marked valid, so a caller that ignores the return value still cannot mistake
 * the buffer for a measurement. This is what stops a missing or mis-wired sensor
 * from feeding zeros into the change detector's EMA baseline.
 *
 * The decision of *when* to retry bring-up lives in sensor_supervisor.c
 * (pure C, host-tested); this file only enforces the contract.
 *
 * Host build: with CONFIG_AS_USE_MOCK_SENSOR=1 the ESP-IDF paths are compiled out
 * entirely, so this file builds against the test-only `esp_log.h` shim in
 * tests/c_host/shims and its contract can be tested on a workstation
 * (tests/test_sensor_contract.py).
 */
#include <math.h>
#include <string.h>

#include "esp_log.h"

#include "config_include.h"
#include "sensor.h"
#include "sensor_backend.h"

#if !CONFIG_AS_USE_MOCK_SENSOR
#include "sensor_bus.h"
#endif

static const char *TAG = "sensor";

/* Set to true only by a sensor_init() that actually succeeded. Invariant:
 * `s_sensor_initialized == (s_active != NULL)` in non-mock builds. */
static bool s_sensor_initialized = false;

#if CONFIG_AS_USE_MOCK_SENSOR
/* Test seam, present only in the mock configuration: make the next init fail so
 * the contract's failure path can be exercised without an ESP32 attached. */
static bool s_mock_init_fails = false;

/*
 * Stand-ins for the state a real build keeps in `s_active`. A device build's
 * invariant is `s_sensor_initialized == (s_active != NULL)`; `s_mock_backend_live`
 * is the second half of that pair here, so a host test can assert the invariant
 * instead of taking it on trust — in particular that a *failed* re-init leaves no
 * handle behind for a later read to use.
 */
static bool s_mock_backend_live = false;
static unsigned s_mock_init_calls = 0;
static unsigned s_mock_init_failures = 0;
static unsigned s_mock_read_calls = 0;

void sensor_mock_set_init_failure(bool should_fail)
{
    s_mock_init_fails = should_fail;
}

void sensor_mock_stats(sensor_mock_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    out->init_calls = s_mock_init_calls;
    out->init_failures = s_mock_init_failures;
    out->read_calls = s_mock_read_calls;
    out->backend_live = s_mock_backend_live;
}
#endif

#if !CONFIG_AS_USE_MOCK_SENSOR
/* The backend that is currently brought up, or NULL. */
static const sensor_backend_t *s_active = NULL;

/*
 * Which backend this build uses. Both are always compiled (so neither rots), and
 * the choice is a configuration value rather than a code change — moving between
 * a BME280 and an SHT30 is one line in config.h.
 */
static const sensor_backend_t *selected_backend(void)
{
#if CONFIG_AS_SENSOR_BACKEND == 1
    return &sensor_backend_bme280;
#elif CONFIG_AS_SENSOR_BACKEND == 2
    return &sensor_backend_sht30;
#else
#error "CONFIG_AS_SENSOR_BACKEND must be 1 (BME280) or 2 (SHT30)"
#endif
}
#endif /* !CONFIG_AS_USE_MOCK_SENSOR */

/* ------------------------------------------------------------------ */
/* mock sensor: reproducible synthetic sequence (documented in README) */
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

/* ------------------------------------------------------------------ */
/* public API                                                          */
/* ------------------------------------------------------------------ */
int sensor_init(void)
{
#if CONFIG_AS_USE_MOCK_SENSOR
    s_mock_init_calls++;
    if (s_mock_init_fails) {
        /* Mock-only seam so the contract's failure path is testable on a host.
         * Not compiled into a device build; see sensor.h. */
        ESP_LOGW(TAG, "MOCK sensor: init failure requested by the test seam");
        s_sensor_initialized = false;
        s_mock_backend_live = false; /* the equivalent of teardown() in a real build */
        s_mock_init_failures++;
        return -1;
    }
    ESP_LOGW(TAG, "MOCK sensor enabled - results are NOT real measurements");
    s_sensor_initialized = true;
    s_mock_backend_live = true;
    return 0;
#else
    const sensor_backend_t *backend = selected_backend();

    /*
     * A bring-up attempt begins by declaring the sensor unusable, and only the
     * success path at the bottom puts it back. The invariant this maintains is
     *
     *     s_sensor_initialized == (s_active != NULL)
     *
     * which matters because a *re*-init can fail. Before this, that left the
     * previous success's flag set while the backend had already been torn down:
     * `sensor_is_initialized()` returned true with no active backend, and callers
     * that trust it would talk to a driver that no longer exists.
     */
    s_sensor_initialized = false;
    if (s_active != NULL) {
        s_active->teardown();
        s_active = NULL;
    }

    if (backend == NULL) {
        return -1;
    }

    /* The bus is created once and shared with every other device on it; a
     * backend never makes its own. See sensor_bus.h. */
    if (sensor_bus_acquire() == NULL) {
        ESP_LOGE(TAG, "i2c bus unavailable; cannot bring up %s", backend->name);
        return -1;
    }

    if (backend->init() != 0) {
        backend->teardown();
        return -1; /* already not initialized, and no active backend */
    }

    s_active = backend;
    s_sensor_initialized = true;
    ESP_LOGI(TAG, "sensor backend ready: %s", backend->name);
    return 0;
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
    /* Counted here, i.e. only once the contract has allowed the read, so the
     * counter shows whether a rejected call ever reached a backend. */
    s_mock_read_calls++;
    mock_fill(out);
    return 0;
#else
    if (s_active == NULL || s_active->read == NULL) {
        return -1;
    }
    /* The backend fills in only the channels it can measure; everything else
     * stays zero with valid == false, which is how the detector is told to
     * ignore it. On failure no channel is left valid. */
    return s_active->read(out);
#endif /* CONFIG_AS_USE_MOCK_SENSOR */
}

bool sensor_is_initialized(void)
{
    return s_sensor_initialized;
}

const char *sensor_backend_name(void)
{
#if CONFIG_AS_USE_MOCK_SENSOR
    return "mock (no hardware)";
#else
    return selected_backend()->name;
#endif
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
