/*
 * sensor_bus.c — see sensor_bus.h.
 */

#include "sensor_bus.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "config_include.h"

static const char *TAG = "sensor_bus";

static i2c_master_bus_handle_t s_bus = NULL;

/*
 * Free a bus whose lines are being held low by a slave.
 *
 * A slave that is mid-transaction when the master resets — or that has been asked
 * for clock stretching the master will not perform — can keep holding SDA or SCL.
 * Nothing on the bus works after that, and a plain probe just times out. This was
 * observed on hardware: SCL read low, all three devices (SHT30, BH1750, display)
 * timed out, and they all answered again after the sequence below.
 *
 * The fix is the standard one: clock SCL by hand at least nine times while SDA is
 * released, so the slave shifts out the rest of its byte, then issue a STOP.
 *
 * Doing this at bring-up matters more than it looks: without it, a node that
 * happened to reset mid-transfer would stay dead across every retry, because the
 * bus itself is what is broken.
 */
static void bus_recover_lines(void)
{
    const int sda = CONFIG_AS_SENSOR_SDA_GPIO;
    const int scl = CONFIG_AS_SENSOR_SCL_GPIO;

    gpio_config_t od_config = {
        .pin_bit_mask = (1ULL << sda) | (1ULL << scl),
        .mode = GPIO_MODE_OUTPUT_OD, /* open drain: 1 releases the line */
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&od_config);

    /* Release both lines before clocking. */
    gpio_set_level((gpio_num_t)sda, 1);
    gpio_set_level((gpio_num_t)scl, 1);
    esp_rom_delay_us(5);

    bool sda_stuck = (gpio_get_level((gpio_num_t)sda) == 0);
    bool scl_stuck = (gpio_get_level((gpio_num_t)scl) == 0);
    if (!sda_stuck && !scl_stuck) {
        gpio_reset_pin((gpio_num_t)sda);
        gpio_reset_pin((gpio_num_t)scl);
        return; /* bus is healthy; leave it alone */
    }

    ESP_LOGW(TAG, "i2c lines held low (SDA=%d SCL=%d); recovering",
             (int)sda_stuck, (int)scl_stuck);

    for (int i = 0; i < 16; i++) {
        gpio_set_level((gpio_num_t)scl, 0);
        esp_rom_delay_us(5);
        gpio_set_level((gpio_num_t)scl, 1);
        esp_rom_delay_us(5);
    }

    /* STOP condition: SDA rises while SCL is high. */
    gpio_set_level((gpio_num_t)sda, 0);
    esp_rom_delay_us(5);
    gpio_set_level((gpio_num_t)scl, 1);
    esp_rom_delay_us(5);
    gpio_set_level((gpio_num_t)sda, 1);
    esp_rom_delay_us(5);

    const bool still_stuck = (gpio_get_level((gpio_num_t)sda) == 0) ||
                             (gpio_get_level((gpio_num_t)scl) == 0);
    ESP_LOGW(TAG, "bus recovery %s", still_stuck ? "did not clear the lines" : "released both lines");

    /* Hand the pins back so the I2C driver can claim them. */
    gpio_reset_pin((gpio_num_t)sda);
    gpio_reset_pin((gpio_num_t)scl);
}

i2c_master_bus_handle_t sensor_bus_acquire(void)
{
    if (s_bus != NULL) {
        return s_bus;
    }

    /* Before the driver claims the pins: a wedged bus must be released, or every
     * transfer below fails and no amount of retrying helps. */
    bus_recover_lines();

    const i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_AS_SENSOR_SDA_GPIO,
        .scl_io_num = CONFIG_AS_SENSOR_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };

    const esp_err_t err = i2c_new_master_bus(&bus_config, &s_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c bus init failed: %s", esp_err_to_name(err));
        s_bus = NULL;
        return NULL;
    }

    ESP_LOGI(TAG, "shared i2c bus ready: SDA=GPIO%d SCL=GPIO%d (one bus for every device on it)",
             (int)CONFIG_AS_SENSOR_SDA_GPIO, (int)CONFIG_AS_SENSOR_SCL_GPIO);
    return s_bus;
}
