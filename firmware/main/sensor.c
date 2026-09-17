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
 * BME280/BMP280 (register-level, with pressure) and SHT30/SHT3x (command-based,
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

/* Set to true only by a sensor_init() that actually succeeded. */
static bool s_sensor_initialized = false;

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
    ESP_LOGW(TAG, "MOCK sensor enabled - results are NOT real measurements");
    s_sensor_initialized = true;
    return 0;
#else
    const sensor_backend_t *backend = selected_backend();
    if (backend == NULL) {
        return -1;
    }

    /* The bus is created once and shared with every other device on it; a
     * backend never makes its own. See sensor_bus.h. */
    if (sensor_bus_acquire() == NULL) {
        ESP_LOGE(TAG, "i2c bus unavailable; cannot bring up %s", backend->name);
        return -1;
    }

    /* Release whatever the previous attempt left behind before trying again. */
    if (s_active != NULL) {
        s_active->teardown();
        s_active = NULL;
    }

    if (backend->init() != 0) {
        backend->teardown();
        return -1;
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
