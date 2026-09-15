/*
 * communication.c — MQTT publish layer.
 *
 * Uses the upstream esp-mqtt component. Because the main loop also sleeps
 * between samples, publishing happens synchronously in the active phase. The
 * Wi-Fi/MQTT connection is established once at boot and kept alive during the
 * awake window; when deep sleep is used the connection is torn down before
 * sleeping (see power_mgmt.c).
 */
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "mqtt_client.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "communication.h"
#include "config.h"

static const char *TAG = "comm";

static esp_mqtt_client_handle_t mqtt_client = NULL;
static EventGroupHandle_t comm_events;
#define WIFI_CONNECTED_BIT  BIT0
#define MQTT_CONNECTED_BIT  BIT1
#define WIFI_FAIL_BIT       BIT2

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    esp_mqtt_event_handle_t ev = (esp_mqtt_event_handle_t)data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        xEventGroupSetBits(comm_events, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        xEventGroupClearBits(comm_events, MQTT_CONNECTED_BIT);
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "MQTT error");
        break;
    default:
        break;
    }
    (void)base;
    (void)arg;
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected; retrying");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "got ip " IPSTR, IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(comm_events, WIFI_CONNECTED_BIT);
    }
    (void)arg;
}

int communication_start(void)
{
    if (comm_events == NULL) comm_events = xEventGroupCreate();

    ESP_ERROR_CHECK(nvs_flash_init());   /* requires partition table; ok */

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler, NULL));

    wifi_config_t wifi_cfg = {
        .sta = {
            .ssid = CONFIG_AS_WIFI_SSID,
            .password = CONFIG_AS_WIFI_PASS,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = CONFIG_AS_MQTT_URI,
        .credentials.username = CONFIG_AS_MQTT_USER,
        .credentials.authentication.password = CONFIG_AS_MQTT_PASS,
        .session.keepalive = 30,
    };
    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    if (mqtt_client == NULL) return -1;
    esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID,
                                   mqtt_event_handler, NULL);
    esp_mqtt_client_start(mqtt_client);
    return 0;
}

bool communication_ready(void)
{
    if (comm_events == NULL) return false;
    return (xEventGroupGetBits(comm_events) & MQTT_CONNECTED_BIT) != 0;
}

int communication_publish(double timestamp_ms,
                          const float values[4],
                          comm_state_t state,
                          float interval_s,
                          bool event)
{
    if (mqtt_client == NULL || !communication_ready()) return -1;

    static const char *state_str[3] = {"STABLE", "ACTIVE", "ALERT"};
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"device_id\":\"%s\",\"timestamp\":%.0f,"
             "\"temperature\":%.2f,\"humidity\":%.2f,"
             "\"pressure\":%.2f,\"light\":%.1f,"
             "\"sampling_interval\":%.1f,\"state\":\"%s\",\"event\":%s}",
             CONFIG_AS_DEVICE_ID, timestamp_ms,
             values[0], values[1], values[2], values[3],
             interval_s, state_str[state], event ? "true" : "false");

    return esp_mqtt_client_publish(mqtt_client, CONFIG_AS_MQTT_TOPIC,
                                   payload, 0, CONFIG_AS_MQTT_QOS, 0);
}