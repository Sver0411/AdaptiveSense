/*
 * communication.c — Wi-Fi + MQTT publish layer.
 *
 * Radio lifecycle (documented in docs/power_management.md):
 *
 *   boot -> nvs/esp_netif/event loop -> esp_wifi_start -> STA connect
 *        -> esp_wifi_set_ps(WIFI_PS_MIN_MODEM)   [modem sleep enabled ONCE]
 *        -> MQTT client start
 *        -> ( sample / publish )*                  [automatic light sleep between]
 *
 * The station is never stopped. Between samples the application idles in
 * `vTaskDelay()`, the ESP-IDF power manager enters light sleep, and the Wi-Fi
 * driver decides whether the modem may sleep through it — that is what keeps the
 * association and the MQTT session alive across sleeps. An earlier revision
 * called `esp_wifi_stop()` before each sleep and never restarted the station, so
 * the connection died after the first cycle and every later publish failed with
 * a return value that nobody looked at.
 *
 * Publish outcomes are counted so that the policy's decision (`upload_requested`)
 * and the transport result (`publish_call_ok`) are never conflated. See
 * communication.h for what "ok" does and does not claim.
 */
#include "communication.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mqtt_client.h"
#include "nvs_flash.h"

#include "communication_payload.h"
#include "config.h"

static const char *TAG = "comm";

static esp_mqtt_client_handle_t s_mqtt_client = NULL;
static EventGroupHandle_t s_comm_events = NULL;
static comm_stats_t s_stats;

#define WIFI_CONNECTED_BIT BIT0
#define MQTT_CONNECTED_BIT BIT1

/* Rate limit for the "publish failed" warning: at most one per interval. */
#define PUBLISH_WARN_INTERVAL_US (30 * 1000 * 1000)
static int64_t s_last_publish_warn_us = 0;

static const char *const STATE_STR[3] = { "STABLE", "ACTIVE", "ALERT" };

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    (void)arg;
    (void)base;
    (void)data;

    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        xEventGroupSetBits(s_comm_events, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        xEventGroupClearBits(s_comm_events, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "MQTT error");
        break;
    default:
        break;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    (void)arg;

    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected; reconnecting");
        xEventGroupClearBits(s_comm_events, MQTT_CONNECTED_BIT);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_comm_events, WIFI_CONNECTED_BIT);
    }
}

int communication_start(void)
{
    if (s_comm_events == NULL) {
        s_comm_events = xEventGroupCreate();
        if (s_comm_events == NULL) {
            ESP_LOGE(TAG, "event group allocation failed");
            return -1;
        }
    }

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs partition unusable; erasing and retrying");
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init failed: %s", esp_err_to_name(err));
        return -1;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    if (esp_netif_create_default_wifi_sta() == NULL) {
        ESP_LOGE(TAG, "failed to create the default Wi-Fi station netif");
        return -1;
    }

    wifi_init_config_t wifi_init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_init));

    /* Register with the instance API (the current ESP-IDF recommendation). */
    static esp_event_handler_instance_t wifi_any_instance;
    static esp_event_handler_instance_t ip_got_ip_instance;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL,
        &wifi_any_instance));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL,
        &ip_got_ip_instance));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_AS_WIFI_SSID,
            .password = CONFIG_AS_WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    /*
     * Modem sleep: the radio sleeps between DTIM beacons and wakes for traffic.
     * This is a Wi-Fi power-save mode, not an energy measurement, and it is what
     * lets the Wi-Fi driver release its PM lock so the chip can enter light sleep
     * between samples while keeping the association (and the MQTT session) up.
     */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MIN_MODEM));

    esp_mqtt_client_config_t mqtt_config = {
        .broker.address.uri = CONFIG_AS_MQTT_URI,
        .credentials.username = CONFIG_AS_MQTT_USER,
        .credentials.authentication.password = CONFIG_AS_MQTT_PASS,
        .session.keepalive = CONFIG_AS_MQTT_KEEPALIVE_S,
    };
    s_mqtt_client = esp_mqtt_client_init(&mqtt_config);
    if (s_mqtt_client == NULL) {
        ESP_LOGE(TAG, "mqtt client init failed");
        return -1;
    }
    ESP_ERROR_CHECK(esp_mqtt_client_register_event(
        s_mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL));
    ESP_ERROR_CHECK(esp_mqtt_client_start(s_mqtt_client));

    ESP_LOGI(TAG,
             "Wi-Fi/MQTT started (modem sleep on, keepalive %ds, topic \"%s\")",
             (int)CONFIG_AS_MQTT_KEEPALIVE_S, CONFIG_AS_MQTT_TOPIC);
    return 0;
}

bool communication_ready(void)
{
    if (s_comm_events == NULL) {
        return false;
    }
    return (xEventGroupGetBits(s_comm_events) & MQTT_CONNECTED_BIT) != 0;
}

int communication_publish(double timestamp_ms,
                          const float values[4],
                          const bool valid[4],
                          comm_state_t state,
                          float interval_s,
                          bool event)
{
    s_stats.publish_requested++;

    const char *state_str = STATE_STR[(int)state >= 0 && (int)state <= 2
                                          ? (int)state
                                          : (int)COMM_STABLE];

    if (s_mqtt_client == NULL || !communication_ready()) {
        s_stats.publish_call_failed++;
        const int64_t now_us = esp_timer_get_time();
        if (now_us - s_last_publish_warn_us >= PUBLISH_WARN_INTERVAL_US) {
            s_last_publish_warn_us = now_us;
            ESP_LOGW(TAG, "upload requested but MQTT is not connected; dropped "
                          "(call_failed=%lu requested=%lu)",
                     s_stats.publish_call_failed, s_stats.publish_requested);
        }
        return -1;
    }

    char payload[COMM_PAYLOAD_MAX_LEN];
    const int written = comm_build_payload(payload, sizeof(payload),
                                            CONFIG_AS_DEVICE_ID, timestamp_ms,
                                            values, valid, state_str, interval_s,
                                            event);
    if (written < 0) {
        ESP_LOGE(TAG, "payload did not fit in %d bytes; not sending",
                 (int)sizeof(payload));
        s_stats.publish_call_failed++;
        return -1;
    }

    /* QoS 0: this returns once the client has accepted the request, not once the
     * broker has it. See communication.h. */
    const int msg_id = esp_mqtt_client_publish(
        s_mqtt_client, CONFIG_AS_MQTT_TOPIC, payload, written,
        CONFIG_AS_MQTT_QOS, 0);
    if (msg_id < 0) {
        s_stats.publish_call_failed++;
        ESP_LOGW(TAG, "MQTT client rejected the publish call (call_failed=%lu)",
                 s_stats.publish_call_failed);
        return -1;
    }

    s_stats.publish_call_ok++;
    return 0;
}

const comm_stats_t *communication_stats(void)
{
    return &s_stats;
}

void communication_log_stats(void)
{
    ESP_LOGI(TAG,
             "publish stats: requested=%lu call_ok=%lu call_failed=%lu "
             "mqtt_connected=%d",
             s_stats.publish_requested, s_stats.publish_call_ok,
             s_stats.publish_call_failed, (int)communication_ready());
}
