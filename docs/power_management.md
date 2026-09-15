# Power management

The node's duty cycle is:

```
WAKE → READ SENSOR → EVALUATE CHANGE → ADAPTIVE POLICY
     → PUBLISH if requested → DETERMINE NEXT WAKE → SLEEP
```

## Modes

`CONFIG_AS_SLEEP_MODE` selects one of three modes, and the mode name says what
the code actually does. The firmware logs the active mode at boot.

| `CONFIG_AS_SLEEP_MODE` | name | behaviour | radio |
|------------------------|------|-----------|-------|
| `0` | `none` | `vTaskDelay` for the remainder of the interval; CPU and radio stay on | associated |
| `1` | `light` (**default**) | `esp_sleep_enable_timer_wakeup()` + `esp_light_sleep_start()`; RAM and the Wi-Fi association are retained | associated, modem asleep between beacons |
| `2` | `deep` | `esp_deep_sleep_start()`; the chip **reboots** on wake | off (the reboot drops the session) |

### Why deep sleep is rejected at compile time

Deep sleep reboots the chip, so everything the policy depends on would be lost:

- the per-channel EMA baselines,
- the ladder rung and its confirmation counter,
- the current state,
- the event debounce (`event_potential_start`),
- the last uploaded value per channel (the normalized-delta rule).

Persisting that set in `RTC_DATA_ATTR` memory is possible but is explicitly out
of scope for v0.2, and doing it half-way would silently change the algorithm.
`config.example.h` therefore fails the build if `CONFIG_AS_SLEEP_MODE == 2`, with
a message saying why. `power_deep_sleep()` remains available as a documented
primitive for a future policy.

> v0.1 had a boolean `CONFIG_AS_DEEP_SLEEP_ENABLE`, set to `1`, guarding a call
> to `esp_light_sleep_start()`. The name described a mode that was not running.

## Wi-Fi lifecycle

```
boot
  └─ nvs → esp_netif → event loop → esp_wifi_start → STA connect
       └─ esp_wifi_set_ps(WIFI_PS_MIN_MODEM)      [modem sleep enabled ONCE]
            └─ mqtt client start
                 └─ ( sample → publish )*           [light sleep between samples]
```

**The station is never stopped between samples.** `power_sleep()` performs a
light sleep, and the Wi-Fi driver keeps the association alive while its modem
sleeps between DTIM beacons, so the node can publish again on the very next wake
with no reconnect. This is the strategy Espressif documents under "Wi-Fi and
light sleep".

This is a deliberate change from v0.1, which called `esp_wifi_stop()` before
every sleep and never restarted the station. After the first cycle the station
was down, the MQTT client was disconnected, `communication_ready()` was false and
every subsequent publish returned `-1` — which `main.c` discarded. The documented
`boot → connect → sample → sleep → wake → publish` lifecycle could not complete a
second iteration, and the failure was invisible.

`scripts/check_config_parity.py` includes a structural check that
`power_mgmt.c` contains no `esp_wifi_stop()` call, so this cannot regress
silently.

Two consequences to be aware of when the node is measured on hardware:

- `CONFIG_AS_MQTT_KEEPALIVE_S` must exceed the longest sleep, or the broker will
  drop the session while the node is asleep.
- The per-cycle `ESP_LOGI` line is transmitted over UART at every sample. That is
  what makes the device log usable as data, but it also keeps the UART clocked
  during the wake window; reduce the log level before an energy measurement.

## Measuring the sleep

`power_sleep()` returns the elapsed seconds measured with `esp_timer`, so the
requested and actual sleep durations can be compared on hardware.
`power_get_stats()` accumulates:

| field | meaning |
|-------|---------|
| `sleep_calls` | number of sleep entries |
| `light_sleep_ok` / `light_sleep_failed` | outcome of `esp_light_sleep_start()` |
| `requested_s` / `actual_s` | summed requested vs measured sleep time |

`main.c` logs this summary every 10 cycles. A growing `actual_s − requested_s`
indicates that wake-ups are costing more than the schedule asks for; a
`light_sleep_failed` counter means the node is falling back to a busy wait.

## Phases

`power_mgmt.h` defines four phases used for logging and later energy accounting:

| phase | meaning |
|-------|---------|
| `PM_ACTIVE` | general active compute |
| `PM_SAMPLE` | sensor read window |
| `PM_TRANSMIT` | Wi-Fi/MQTT transmission window |
| `PM_SLEEP` | asleep until the next scheduled sample |

## Status

The duty cycle is implemented and its configuration is build-verified. **No
current or energy measurement has been taken**: sleep currents, wake overhead and
per-cycle energy are all `Not measured yet.` See
[hardware.md](hardware.md) for the measurement procedure.
