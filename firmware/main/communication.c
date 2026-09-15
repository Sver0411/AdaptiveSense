/*
 * communication.c — Wi-Fi + MQTT publish layer.
 *
 * Radio lifecycle (documented in docs/power_management.md):
 *
 *   boot -> nvs/esp_netif/event loop -> esp_wifi_start -> STA connect
 *        -> esp_wifi_set_ps(WIFI_PS_MIN_MODEM)   [modem sleep enabled once]
 *        -> MQTT client start
 *        -> (sample / publish)*                   [light sleep between samples]
 *
 * The station is never stopped between samples. `power_sleep()` performs a light
 * sleep and the Wi-Fi driver keeps the association alive while its modem sleeps
 * between beacons, so the node can publish again on the very next wake without a
 * reconnect. v0.1 called `esp_wifi_stop()` before every sleep and never
 * restarted the station, so the connection died after the first cycle and every
 * later publish failed silently.
 *
 * Publish outcomes are counted, so `upload_requested` and `publish_success` can
 * be separated in the logs and in the experiment write-up.
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

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    (void)arg;
    (void)base;
    (void)data;

    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        s_stats.mqtt_connected = true;
        xEventGroupSetBits(s_comm_events, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        s_stats.mqtt_connected = false;
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
        s_stats.mqtt_connected = false;
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
     * Combined with esp_light_sleep_start() this keeps the association alive
     * across sleeps without a reconnect. This is the documented "Wi-Fi and
     * light sleep" strategy and replaces the v0.1 esp_wifi_stop() call.
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

    ESP_LOGI(TAG, "Wi-Fi/MQTT started (modem sleep on, topic \"%s\")",
             CONFIG_AS_MQTT_TOPIC);
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
                          comm_state_t state,
                          float interval_s,
                          bool event)
{
    static const char *const state_str[3] = { "STABLE", "ACTIVE", "ALERT" };

    s_stats.publish_requested++;

    if ((int)state < 0 || (int)state > 2) {
        state = COMM_STABLE;
    }

    if (s_mqtt_client == NULL || !communication_ready()) {
        s_stats.publish_failed++;
        const int64_t now_us = esp_timer_get_time();
        if (now_us - s_last_publish_warn_us >= PUBLISH_WARN_INTERVAL_US) {
            s_last_publish_warn_us = now_us;
            ESP_LOGW(TAG, "upload requested but MQTT is not connected; "
                          "dropped (failed=%lu requested=%lu)",
                     s_stats.publish_failed, s_stats.publish_requested);
        }
        return -1;
    }

    char payload[256];
    const int written = snprintf(
        payload, sizeof(payload),
        "{\"device_id\":\"%s\",\"timestamp\":%.0f,"
        "\"temperature\":%.2f,\"humidity\":%.2f,"
        "\"pressure\":%.2f,\"light\":%.1f,"
        "\"sampling_interval\":%.1f,\"state\":\"%s\",\"event\":%s}",
        CONFIG_AS_DEVICE_ID, timestamp_ms,
        (double)values[0], (double)values[1], (double)values[2], (double)values[3],
        (double)interval_s, state_str[(int)state], event ? "true" : "false");

    if (written <= 0 || (size_t)written >= sizeof(payload)) {
        ESP_LOGE(TAG, "payload formatting failed");
        s_stats.publish_failed++;
        return -1;
    }

    const int msg_id = esp_mqtt_client_publish(
        s_mqtt_client, CONFIG_AS_MQTT_TOPIC, payload, 0,
        CONFIG_AS_MQTT_QOS, 0);
    if (msg_id < 0) {
        s_stats.publish_failed++;
        ESP_LOGW(TAG, "mqtt publish rejected (failed=%lu)", s_stats.publish_failed);
        return -1;
    }

    s_stats.publish_ok++;
    return 0;
}

const comm_stats_t *communication_stats(void)
{
    s_stats.mqtt_connected = communication_ready();
    return &s_stats;
}

void communication_log_stats(void)
{
    ESP_LOGI(TAG,
             "publish stats: requested=%lu ok=%lu failed=%lu mqtt_connected=%d",
             s_stats.publish_requested, s_stats.publish_ok, s_stats.publish_failed,
             (int)communication_ready());
}
