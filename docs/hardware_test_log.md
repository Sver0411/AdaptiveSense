# Hardware test log

Real-device sessions of the AdaptiveSense firmware. Every entry is something
that actually happened on the board described below; `Not tested yet.` means it
has not been run, not that it is expected to work.

---

## Session 1 — 2026-09-17, first bring-up

### Board under test

| item | value | how it was read |
|------|-------|-----------------|
| Chip | **ESP32-S3 (QFN56), revision v0.2** | `esptool.py flash_id` |
| MAC | `80:65:99:a7:a8:a4` | `esptool.py flash_id` |
| Flash | **16 MB**, manufacturer `0x1C` / device `0x7118`, quad, 3.3 V (eFuse) | `esptool.py flash_id` |
| PSRAM | **8 MB in-package**, octal, 80 MHz, memory test OK | boot log |
| Module | consistent with an **ESP32-S3-WROOM-1 N16R8** | inferred from the two rows above |
| USB bridge | WCH `USB Single Serial` (VID `0x1A86`, PID `0x55D3`) | `ioreg` |
| Serial port | `/dev/cu.usbmodem5C930836161` | `ls /dev/cu.*` |
| Console UART | UART0 on **GPIO43 (TX) / GPIO44 (RX)**, 115200 | boot log |

Host: macOS 15.7.7 · toolchain: ESP-IDF **v5.4.4** · target `esp32s3`.

### Result summary

| step | result |
|------|--------|
| Compile (clean, `set-target` + `build`) | **PASS** — 0 warnings, 0 errors, image 901 792 B |
| Chip identification | **PASS** — see table above; flash size and PSRAM mode match the build configuration |
| Flash | **PASS** — 901 792 B written at `0x10000`, hash verified |
| Boot | **PASS** — no panic, no watchdog, no abort, no reset loop over 80+ s |
| Power-management configuration | **PASS** — automatic light sleep armed, confirmed in the log |
| Sensor bring-up | **BLOCKED** — no BME280 on the bus; failure path verified |
| Wi-Fi / MQTT | **BLOCKED** — placeholder credentials; failure path verified |
| Light sleep actually entered | **NOT OBSERVED** — 0 entries; cause identified, see below |
| Power measurement | `Not tested yet.` |

### Boot log (abridged, first session)

```
I (37) boot.esp32s3: SPI Mode       : DIO
I (41) boot.esp32s3: SPI Flash Size : 16MB
I (279) octal_psram: vendor id    : 0x0d (AP)
I (322) esp_psram: Found 8MB PSRAM device
I (326) esp_psram: Speed: 80MHz
I (758) esp_psram: SPI SRAM memory test OK
I (766) cpu_start: GPIO 44 and 43 are used as console UART I/O pins
I (767) cpu_start: cpu freq: 160000000 Hz
I (777) app_init: App version:      e00fc4a
I (790) app_init: ESP-IDF:          v5.4.4
I (884) main: AdaptiveSense node booting (device=node-01)
I (888) pm: Frequency switching config: CPU_MAX: 160, APB_MAX: 160, APB_MIN: 160, Light sleep: ENABLED
I (897) sleep: Code start at 0x42000020, ...
I (906) pm: power management: mode=automatic light sleep (ESP-IDF PM + FreeRTOS tickless idle), cpu 160-160 MHz, light_sleep=1
I (1072) wifi:Set ps type: 1, coexist: 0
I (1076) comm: Wi-Fi/MQTT started (modem sleep on, keepalive 120s, topic "adaptivesense/data")
E (1080) sensor: BME280 not responding at 0x76: ESP_ERR_NOT_FOUND
W (1104) main: sensor init failed (1 attempt(s)); retrying no sooner than 5s from now
W (1112) main: no usable reading at cycle 1 (sensor unavailable (will retry)); next attempt in 5s
...
I (46081) main: duty cycle: idle_requests=9 scheduled_idle=44.9s light_sleep_entries=0 light_sleep=0.0s
```

`Set ps type: 1` is `WIFI_PS_MIN_MODEM`, i.e. the Wi-Fi modem-sleep power save is
in force. The PSRAM line confirms that `CONFIG_SPIRAM_MODE_OCT` matches the
silicon — a wrong PSRAM mode would have failed here rather than later.

### Modules present on the board, and what the firmware can use

The attached modules were identified by reading the bus, not by assumption. Full
method and raw evidence are in the session notes below.

| module | bus / pin | identification evidence | usable by this firmware? |
|--------|-----------|-------------------------|--------------------------|
| **SHT30** temperature + humidity | I²C `0x44` | ACKs the SHT3x status command (`0xF3 0x2D`) and returns `80 10 E1`; serial number reads back `2D B1 93 E9 03 67` | **No** — the driver is BME280 register-level; SHT30 is a different chip |
| **GY-302 / BH1750** light sensor | I²C `0x23` | Accepts the BH1750 command set (power-on `0x01`, continuous H-resolution `0x10`) and returns 66 counts = 55.0 lx, a plausible indoor value | **No** — the light channel is documented as not implemented |
| **0.96" OLED** | I²C `0x3C` | Reads back a constant `45 45 45 ...` for every register pointer: the signature of a write-only display controller | **No** — not part of the project |
| **Capacitive soil moisture** | **GPIO1**, analogue output | Not visible to an I²C scan (confirmed by the owner). The electrical diagnostic found GPIO1 held high against the internal pull-down (`up=1 dn=1`), which is what an analogue output sitting near 3.3 V looks like — i.e. the method detected it without being told | **No** — not part of the project |
| **BME280 / BMP280** | — | **Not found on any pin pair tested** | **This is what the firmware needs** |

The four I²C modules all sit on **GPIO8 (SDA) / GPIO9 (SCL)**. The project uses
**GPIO4 / GPIO5**, which the electrical diagnostic reports as floating — there is
nothing on the project's bus.

> **Wiring caution for the soil sensor.** A capacitive soil moisture module
> powered from 5 V can drive its analogue output above 3.3 V, which is outside the
> ESP32-S3's absolute maximum on an ADC pin. Power it from **3.3 V** (the v1.2
> modules work down to 3.3 V) unless the module is known to regulate its output.
> GPIO1 is `ADC1_CH0`, so the signal is readable in principle; this firmware does
> not read it.

### Wiring required for this project

See [`hardware.md`](hardware.md#wiring) for the authoritative table (pins,
voltage, cautions, pins to avoid). Summary: a BME280 or BMP280 breakout on
**SDA = GPIO4, SCL = GPIO5, VCC = 3.3 V, GND = GND, ADDR/SDO = GND** (→ address
`0x76`). Do **not** feed 5 V into SDA/SCL.

### Problems found on hardware

| # | severity | finding | status |
|---|----------|---------|--------|
| H1 | **high** | `sensor_init()` created the I2C bus but never released it on its eight failure paths, so the *second* bring-up attempt died in `i2c_new_master_bus` (`I2C bus id(0) has already been acquired`). A node whose sensor was connected after boot could never recover — which defeated the retry supervisor added in v0.3 | **Fixed** — `sensor_i2c_teardown()`, called at the start of each attempt and on every failure path |
| H2 | low | The `duty cycle:` line was only emitted on cycles where the sensor read succeeded, so a node with a dead sensor — exactly the node whose sleep behaviour matters — never reported it | **Fixed** — hoisted into `log_periodic_stats()`, called on both paths |
| H3 | medium | **Light sleep is never entered: `idle_requests=9` but `light_sleep_entries=0`.** Consistent with the Wi-Fi driver holding a PM lock while it is stuck in a reconnect loop (no valid credentials), so the chip never reaches the idle state the PM subsystem needs | **Open** — needs valid Wi-Fi credentials, or a run with `WIFI_PS_NONE`/radio off, to confirm the cause |
| H4 | medium | Wi-Fi reconnect has **no back-off**: with placeholder credentials the log shows `Wi-Fi disconnected; reconnecting` roughly every 2 s indefinitely, burning CPU and radio | **Open** — proposed fix is a bounded retry interval; not applied yet because it is a behaviour change rather than a crash |
| H5 | — | `esptool` reports the flash as quad while `CONFIG_ESPTOOLPY_FLASHMODE_DIO` is set: consistent, no action | closed |

Both fixes are local to `firmware/main/`. `python -m pytest tests/` still reports
140 passed, and `scripts/check_config_parity.py` 47/47, after them.

### Verified on hardware in this session

* Clean build for `esp32s3` with 0 warnings, and flashing it.
* Boot to `app_main()` with no panic / watchdog / abort, over 80+ s.
* Flash size (16 MB) and octal PSRAM (8 MB) match the build configuration — no
  header mismatch, no PSRAM init failure.
* `esp_pm_configure()` reports `CPU_MAX: 160, APB_MAX: 160, APB_MIN: 160,
  Light sleep: ENABLED`, and the mode line prints
  `automatic light sleep (ESP-IDF PM + FreeRTOS tickless idle)`.
* Wi-Fi station starts, `esp_wifi_set_ps(WIFI_PS_MIN_MODEM)` applied
  (`Set ps type: 1`), MQTT client starts with keepalive 120 s.
* **The sensor-failure path behaves as designed**: the node logs a warning, does
  not feed zeros to the change detector, and retries bring-up at the configured
  rate limit (measured: attempts at ≈1 s, 11 s, 21 s … — one per ~10 s, not a
  busy loop).
* The I2C bus is released correctly between retries after fix H1 (each attempt
  now reaches the address probe and fails cleanly with `ESP_ERR_NOT_FOUND`).

### Not yet verified — needs hardware or credentials

* `ESP32-S3` sensor reading with a **BME280 actually attached** (the whole
  sampling → score → policy → upload chain on real data).
* Compensation accuracy against a reference (the maths is host-tested; the
  sensor has never produced a real number on this board).
* **Light sleep entry** and the duty cycle (`light_sleep_entries` > 0).
* Wi-Fi association, MQTT publish, and a broker actually receiving a payload.
* `publish_call_ok` / `publish_call_failed` counters under a live broker.
* Multi-cycle stability over hours, and the heartbeat / upload policy in the field.
* Sleep current, wake energy, average power — **`Not tested yet.`**; they need a
  current monitor (INA219 / Joulescope / Nordic Power Profiler).
* The BH1750 and the OLED are present but unused by this project; wiring the
  BH1750 would let the benchmark's light channel be validated on hardware, but
  that is a driver addition and is out of scope for this version.

---

## Method notes

Two things were needed to test at all from a scripted session, and both are
recorded so they can be reproduced:

* **Clean-environment builds.** The host shell injects Python/shell shims that
  broker filesystem calls and break ESP-IDF's `os.mkdir` (spurious `EEXIST`).
  Running `idf.py` under `env -i` reproduces a normal developer's shell. See
  `docs/build_validation.md`.
* **Bounded serial capture.** `idf.py monitor` needs an interactive TTY. The
  captures in this log were taken with a short pyserial script that pulses the
  auto-reset line (RTS → EN) and then reads for a fixed number of seconds, so the
  capture starts at the boot ROM output. The script lives outside the repository;
  ask if you want it added under `scripts/`.

The I2C investigation used a separate throwaway ESP-IDF project (also outside the
repository) so that identifying the attached modules did not require modifying
AdaptiveSense. Its method, and the reason a naive address sweep is not
trustworthy, are in the next section.

### Why the first bus sweep was rejected

A plain "probe every address on every pin pair" sweep reported 116 "devices",
including **sixteen consecutive addresses** (0x68–0x77) on `SDA=1/SCL=2`. No I2C
bus can look like that. The pins were electrically characterised instead — input
with internal pull-up, then input with internal pull-down:

| reading | meaning |
|---------|---------|
| pull-up 1, pull-down 0 | floating; nothing attached |
| pull-up 1, pull-down 1 | external pull-up present → a real I2C bus |
| pull-up 0 | something is holding the line low; not a usable idle bus |

Only pairs where both lines idle high were then scanned, and an ACK was rejected
if it was part of a run of four or more consecutive addresses. The 16-address
"block" turned out to be a genuinely floating SCL line (the probe times out rather
than acknowledging); the bus on GPIO8/GPIO9 is real, shows external pull-ups in
both orientations, and only responds in the SDA=8/SCL=9 orientation.
