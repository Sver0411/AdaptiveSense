# Power management

The node's duty cycle is:

```
WAKE → READ SENSOR → EVALUATE CHANGE → ADAPTIVE POLICY
     → PUBLISH if requested → DETERMINE NEXT WAKE → IDLE
```

This document describes how the "IDLE" phase is realised. It changed in v0.3, and
the reason is worth stating up front.

## How the node sleeps

The application **does not** call `esp_light_sleep_start()`. It calls
`vTaskDelay()`, and the ESP-IDF power manager decides whether the chip can enter
light sleep while the scheduler is idle:

```
power_sleep()                     firmware/main/power_mgmt.c
   └─ vTaskDelay()
        └─ FreeRTOS idle task
             └─ automatic light sleep      CONFIG_PM_ENABLE=y
                                           CONFIG_FREERTOS_USE_TICKLESS_IDLE=y
                                           esp_pm_configure(light_sleep_enable=true)
                  └─ Wi-Fi driver PM locks decide whether the modem may sleep
```

Why this matters: the radio becomes part of the sleep decision. With
`WIFI_PS_MIN_MODEM` the Wi-Fi driver releases its PM lock between DTIM beacons, so
the chip can sleep and the association survives. If the application instead slept
on its own — the earlier approach — the power manager is bypassed, the Wi-Fi
driver is never consulted about whether the modem may sleep, and whatever the
documentation claims about the association surviving is an assumption rather than
a mechanism.

This is the arrangement Espressif documents under *Wi-Fi/BT and light sleep*:
Wi-Fi modem sleep, plus FreeRTOS tickless idle, plus `CONFIG_PM_ENABLE` and
`esp_pm_configure()`.

## Configuration

`firmware/sdkconfig.defaults`:

| option | value | why |
|--------|-------|-----|
| `CONFIG_PM_ENABLE` | `y` | enable the power-management subsystem |
| `CONFIG_FREERTOS_USE_TICKLESS_IDLE` | `y` | automatic light sleep from the idle task (**depends on `CONFIG_PM_ENABLE`**) |
| `CONFIG_FREERTOS_IDLE_TIME_BEFORE_SLEEP` | `8` | 8 ticks at `FREERTOS_HZ = 1000`. Long enough not to sleep inside short internal waits, far below the node's 5–60 s intervals |
| `CONFIG_PM_LIGHT_SLEEP_CALLBACKS` | `y` | the only way the application can observe real light-sleep entries and duration (see below) |
| `CONFIG_PM_DFS_INIT_AUTO` | `n` | DFS is not used; light sleep is armed explicitly by `esp_pm_configure()`, so the behaviour is visible in the code |

`power_init()` then calls:

```c
esp_pm_config_t pm_config = {
    .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,   /* valid S3 value */
    .min_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,   /* no DFS: max == min */
    .light_sleep_enable = (mode == PM_SLEEP_AUTO_LIGHT),
};
esp_pm_configure(&pm_config);
```

`max_freq_mhz == min_freq_mhz` on purpose: this node has no CPU-bound work, so
frequency scaling would add a variable without buying anything, while light sleep
is what the duty cycle actually needs. Taking both bounds from
`CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ` keeps them valid for the target and stops them
drifting away from sdkconfig.

`scripts/check_config_parity.py` fails the build if any of the four sdkconfig
options above goes missing, if `esp_light_sleep_start` reappears in
`power_mgmt.c`, or if `esp_pm_configure()` stops being called.

## Modes

`CONFIG_AS_SLEEP_MODE` selects one, and the firmware logs the active mode at boot.

| value | name | behaviour |
|-------|------|-----------|
| `0` | `PM_SLEEP_NONE` | `vTaskDelay()` only. PM is compiled in but light sleep is not armed, so the chip stays awake. Bench/debug use. |
| `1` | `PM_SLEEP_AUTO_LIGHT` (**default**) | `vTaskDelay()` with light sleep armed; the chip sleeps whenever the scheduler is idle and the Wi-Fi driver permits it. |
| `2` | deep sleep | **rejected at compile time.** Deep sleep reboots the chip, so the detector's EMA baselines, the ladder position, the current state and the event debounce would all be lost. Persisting them in `RTC_DATA_ATTR` is future work. |

`power_deep_sleep()` remains available as a documented primitive for a future
policy; nothing in the default configuration calls it.

## Wi-Fi lifecycle

```
boot
  └─ esp_pm_configure()                       [power_init, before Wi-Fi starts]
  └─ nvs → esp_netif → event loop → esp_wifi_start → STA connect
       └─ esp_wifi_set_ps(WIFI_PS_MIN_MODEM)  [modem sleep enabled ONCE]
            └─ mqtt client start
                 └─ ( sample → publish → vTaskDelay )*
```

**The station is never stopped between samples.** This is deliberate: an earlier
revision called `esp_wifi_stop()` before every sleep and never restarted it, so
after the first cycle the station was down, the MQTT client was disconnected and
every later publish failed with a return value nobody looked at.

`scripts/check_config_parity.py` includes a structural check that `power_mgmt.c`
contains no `esp_wifi_stop()` call, so this cannot regress silently.

Two consequences to be aware of when the node is measured on hardware:

- **MQTT keepalive is itself traffic.** PINGREQ/PINGRESP exchanges are network
  activity, and network activity can wake the chip out of light sleep through the
  Wi-Fi driver's PM locks. `CONFIG_AS_MQTT_KEEPALIVE_S` is therefore a
  robustness-versus-wake-ups trade-off, not a free parameter. It is constrained to
  at least twice `max_interval` so a sleeping node is never declared gone by the
  broker (checked at compile time in `config.example.h` and in the YAML
  validator).
- **The per-cycle `ESP_LOGI` line is transmitted over UART at every sample.**
  That is what makes the device log usable as data, but the console keeps the chip
  awake during the wake window. Set `CONFIG_LOG_DEFAULT_LEVEL_WARN` before an
  energy measurement.

## What is measured, and what is not

`power_sleep()` returns the elapsed wall-clock seconds of the delay, which is what
the caller needs to keep its schedule. That is **not** a measurement of how long
the chip spent in light sleep, and it is not reported as one.

Real light sleep is observed separately, through the PM callback that
`CONFIG_PM_LIGHT_SLEEP_CALLBACKS` provides. `power_get_stats()` therefore reports
two independent pairs:

| field | what it is |
|-------|-----------|
| `sleep_requests` | how often the duty cycle entered its idle phase |
| `scheduled_idle_s` | the summed duration it asked to be idle for |
| `light_sleep_entries` | how often the chip **really** entered light sleep |
| `light_sleep_s` | the summed **actual** light-sleep time, from the callback's exit value |
| `pm_configured` | whether `esp_pm_configure()` succeeded |

The two pairs can differ, and that difference is informative: if
`light_sleep_entries` is far below `sleep_requests`, something is holding a PM
lock (the Wi-Fi driver while it needs the modem, or a task that never blocks) and
the node is not sleeping as often as the schedule suggests. `main.c` logs both
every 10 cycles.

There is no energy field, and no joule or millijoule figure anywhere in this
repository: nothing in the firmware can measure current.

## Phases

`power_mgmt.h` defines four phases used for logging and later energy accounting:

| phase | meaning |
|-------|---------|
| `PM_ACTIVE` | general active compute |
| `PM_SAMPLE` | sensor read window |
| `PM_TRANSMIT` | Wi-Fi/MQTT transmission window |
| `PM_SLEEP` | idle until the next scheduled sample |

## Status

The duty cycle is implemented, and its configuration is build-verified (ESP-IDF
v5.4.4, `CONFIG_PM_ENABLE=y` and tickless idle confirmed in the generated
sdkconfig). **No current or energy measurement has been taken**: sleep currents,
wake overhead and per-cycle energy are all `Not measured yet.` See
[hardware.md](hardware.md) for the measurement procedure.
