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

## Session 2 — 2026-09-17, SHT30 backend and main-chain verification

Same board as session 1. The goal was to get the real sensor into the main chain
without touching the algorithm, and the physical modules on hand are an SHT30, a
GY-302 (BH1750), an OLED and a capacitive soil sensor — no BME280.

### What changed, and what deliberately did not

The sensor layer became **sensor-agnostic**: `sensor.c` keeps the API, the read
contract and the choice of backend; chip drivers implement `sensor_backend.h`.
Two backends ship (BME280/BMP280 and SHT30/SHT3x), the bus is owned once by
`sensor_bus.c` and shared, and the SHT30 protocol (CRC, conversion, sequencing) is
a pure-C unit tested on the host.

Verified as untouched, by diff rather than by intent:

| file | status |
|------|--------|
| `change_detector.c` / `.h` | **unchanged** |
| `adaptive_scheduler.c` / `.h` | **unchanged** |
| `simulator/` (scoring, adaptive, events, metrics, replay) | **unchanged** |
| `analysis/` | **unchanged** |
| `experiments/experiment_config.yaml` | one non-comment change: `payload_bytes_per_upload` 246 → 244 |

That last change is a *reporting* constant, not a tuning parameter: the SHT30 has
no pressure channel, so `pressure` is sent as `null`, which is two bytes shorter
than the number it replaces. Every algorithm parameter (noise floors, thresholds,
hysteresis, windows, ladders, debounce, upload policy) is byte-identical.

### Noise floors: confirmed to be experiment parameters, not BME280 parameters

Checked rather than assumed, because swapping sensors is exactly when someone
would be tempted to "recalibrate" them:

| channel | floor in the config | noise sigma injected by the generator | ratio | BME280 datasheet noise |
|---------|--------------------:|--------------------------------------:|------:|-----------------------:|
| temperature | 0.15 °C | 0.08 (0.08–1.0 across scenarios) | ≈ 2× | 0.01 °C RMS |
| humidity | 0.8 %RH | 0.4 (0.4–2.5) | ≈ 2× | 0.012 %RH |
| pressure | 0.3 hPa | 0.15 (0.15–0.6) | ≈ 2× | 0.12 Pa |

Each floor sits at roughly twice the generator's baseline noise and 10–100× above
the BME280's own noise, so they describe the **benchmark**, not a chip. They were
therefore left exactly as they were. Changing them would alter the policy's
sensitivity and invalidate the published simulation results.

### Unit tests

| check | result |
|-------|--------|
| `python -m pytest tests/` | **159 passed** (was 140) |
| `python scripts/check_config_parity.py` | **PASS, 55 checks** (was 47) |
| `idf.py build` (ESP-IDF v5.4.4, clean) | **PASS, 0 warnings, 0 errors**, image 903 312 B, 14 % partition headroom |

The 19 new tests cover the SHT30 protocol against frames captured from this device,
and specifically: a correct frame decodes; a **corrupted CRC makes the measurement
absent** (outputs untouched, and no half-measurement either); a failed write is
reported without a subsequent read; a failed or short read is reported; a call with
no transport at all is refused. The 8 new configuration checks pin the architecture:
only `sensor_bus.c` creates an I²C bus, no backend does, the protocol layer has no
ESP-IDF dependency, and both backends are compiled.

### SHT30 continuous read test (120 s at 1 Hz)

A throwaway program that links the *shipping* `sht30_proto.c`, so the CRC and
conversions under test are the ones in the firmware:

```
attempts                120
successful reads        120
CRC failures            0
transport failures      0
NaN values              0
values out of range     0
temperature   min 27.14  max 27.26  mean 27.20  C
humidity      min 57.01  max 57.35  mean 57.18  %RH
largest step  temperature 0.05 C    humidity 0.07 %RH
success rate            100.0 %
```

Range, mean and step size are all consistent with a still room, and there is no
NaN, no jump and no CRC failure across 120 consecutive readings.

### Main chain on hardware

`SHT30 → sensor_read_t → change detector → adaptive scheduler`, from the serial log
(the `score` column is computed by the change detector from the SHT30 values, and
`state`/`interval` come from the scheduler):

```
main: AdaptiveSense node booting (device=node-01, sensor backend: SHT30 (temperature + humidity))
sensor_bus: shared i2c bus ready: SDA=GPIO8 SCL=GPIO9 (one bus for every device on it)
sht30: SHT30 ready at 0x44 (single shot, high repeatability, no clock stretching; channels: temperature, humidity)
sensor: sensor backend ready: SHT30 (temperature + humidity)

cycle=1  t=0.3    state=0 interval=20.0s score=0.00 event=0 upload_requested=1 publish_call_ok=0 temp=27.15 hum=57.22
cycle=2  t=20.3   state=0 interval=20.0s score=0.20 event=0 upload_requested=0 publish_call_ok=0 temp=27.13 hum=57.16
cycle=3  t=40.3   state=0 interval=40.0s score=0.18 event=0 upload_requested=1 publish_call_ok=0 temp=27.14 hum=57.35
cycle=4  t=80.3   state=0 interval=40.0s score=0.23 event=0 upload_requested=0 publish_call_ok=0 temp=27.11 hum=57.14
cycle=5  t=120.3  state=0 interval=60.0s score=0.11 event=0 upload_requested=1 publish_call_ok=0 temp=27.13 hum=57.28
```

What this demonstrates:

* the values are real measurements and agree with the soak test (27.2 °C / 57.2 %RH);
* the **change detector is running on them** — the score is non-zero and varies;
* the **interval ladder behaves exactly as unit-tested**: 20, 20, 40, 40, 60, which
  is STABLE `[20, 40, 60]` with `confirmations = 2`
  (`tests/test_scheduler.py::test_stable_signal_backs_off_to_max_interval`);
* the **upload policy fired on the first sample and on each interval change**, and
  not on cycles 2 and 4 — which is the documented policy;
* `publish_call_ok=0` is the *transport* failing (the broker URI is still a
  placeholder), reported as such rather than being hidden;
* no panic, no watchdog, no `ESP_ERR_*`, no CRC complaint.

Not yet exercised on hardware: a **state transition**. The room was still, so the
node correctly stayed in STABLE. Provoking ACTIVE/ALERT needs a real disturbance —
warming the sensor with a finger is enough, since a 1 °C step is ≈ 6.7 noise floors
against the ACTIVE entry bound of 4.5.

### I2C bus wedge: observed, recovered, trigger not reproduced

While probing measurement commands, the shared bus was found **wedged**: SCL held
low, and all three devices (SHT30 `0x44`, BH1750 `0x23`, display `0x3C`) replying
`ESP_ERR_TIMEOUT`. Manual recovery — clocking SCL by hand ≥ 9 times then issuing a
STOP — released it, and all three answered immediately afterwards:

| | SDA | SCL | probes |
|---|-----|-----|--------|
| before recovery | 1 | **0 (held)** | 0x44 / 0x23 / 0x3C all `ESP_ERR_TIMEOUT` |
| after recovery | 1 | 1 | 0x44 / 0x23 / 0x3C all **ACK**, 0x45 correctly absent |

**Honest limitation:** the trigger was not reproduced. Replaying the exact command
sequence that preceded the wedge (`0x2C06`, a successful read, two reads that return
`ESP_ERR_INVALID_STATE`, then a failed `0x240B` write) leaves the bus healthy
(`SDA=1 SCL=1`), so the sequence alone does not cause it. The most likely cause is
the chip being reset *during* a transfer — the wedge was noticed after a re-flash —
but that has not been demonstrated.

That notwithstanding, `sensor_bus.c` now performs the same recovery sequence at
bring-up, because the failure mode is real and its consequence is severe: a slave
holding a line low keeps the bus dead, so every retry fails and the node never
comes up. The recovery code itself has been exercised against the wedged bus
through the throwaway tool; it has not yet been needed by the firmware in the
field.

### Open items from this session

1. **A state transition has not been seen on hardware** (see above).
2. **Bus recovery runs at every bring-up but has not yet had to fire.** It is
   defensive; the trigger remains unidentified.
3. **`pressure` is unavailable on this build** — an SHT30 has no pressure sensor,
   and the `pressure` channel is reported invalid and sent as `null`.
4. **The BH1750, the OLED and the soil sensor remain unused** and no driver for
   them exists. The light channel of the benchmark therefore still has no hardware
   counterpart.
5. **Wi-Fi/MQTT still use placeholder credentials**, so `publish_call_ok` is 0 and
   the upload path is only verified up to the MQTT client call.

---

## Session 3 — 2026-09-17, state machine on hardware, and a bug in the bus recovery

Same board, same firmware as session 2. The goal was to provoke an actual state
transition, which session 2 had not managed because the room was still.

Method: the operator pressed a finger on the SHT30 for about two and a half
minutes while the serial console was captured. Long enough to matter because the
node had already backed off to a 60 s interval.

### Result: all three states, and the transitions between them

| cycle | t (s) | state | interval | score | event | upload | T (°C) | RH (%) | |
|------:|------:|-------|---------:|------:|------:|-------:|-------:|-------:|---|
| 1 | 0.3 | STABLE | 20 s | 0.00 | 0 | yes | 28.61 | 58.08 | |
| 2 | 20.3 | **ACTIVE** | 15 s | 4.90 | 0 | yes | 27.88 | 55.30 | score ≥ `stable_high` (4.5) |
| 3 | 35.3 | ACTIVE | 10 s | 4.62 | 0 | yes | 27.73 | 55.41 | ladder descends |
| 4 | 45.3 | ACTIVE | 5 s | 4.07 | 0 | yes | 27.68 | 55.63 | ladder floor |
| 7 | 60.3 | ACTIVE | 5 s | 6.95 | 0 | yes | 29.16 | 56.44 | |
| 10 | 75.2 | **ALERT** | 5 s | 12.04 | 0 | yes | 29.98 | 63.31 | score ≥ `active_high` (12.0) |
| 11 | 80.2 | ALERT | 5 s | 16.45 | 0 | yes | 30.78 | 66.49 | |
| 12 | 85.2 | ALERT | 5 s | 15.47 | 0 | yes | 30.83 | 64.56 | |
| 13 | 90.2 | ALERT | 5 s | 14.94 | **1** | yes | 30.93 | 64.71 | debounce latched: score > 8 for 15 s |
| 21 | 130.2 | ALERT | 5 s | 4.29 | 0 | no | 28.62 | 53.73 | |
| 22 | 135.2 | **ACTIVE** | 15 s | 3.34 | 0 | yes | 28.55 | 54.22 | score < `active_low` (4.0) → one level down |
| 23 | 150.2 | ACTIVE | 10 s | 6.73 | 0 | yes | 28.64 | 62.08 | |
| 24 | 160.2 | **ALERT** | 5 s | 18.09 | 0 | yes | 28.98 | 72.24 | re-escalated immediately |

Temperature range 27.64 – 30.93 °C, humidity 53.19 – 72.24 %RH; the finger moved
both channels, which is what the score responds to. Interval sequence:
`20, 15, 10, 5 … 5, 15, 10, 5 …`.

What this confirms, on real hardware, with real sensor data:

* **escalation is immediate and multi-level** — STABLE went to ACTIVE on one
  sample, and ACTIVE to ALERT on one sample;
* **the ACTIVE ladder descends** 15 → 10 → 5, i.e. the node samples *faster* once
  change is detected (the defect this ladder was fixed for in v0.3);
* **the event debounce works**: the event bit latched at cycle 13, after the score
  had stayed above `event_threshold` (8.0) from t = 75.2 s to 90.2 s — 15 s, more
  than `event_min_duration_s` (10 s);
* **de-escalation moves exactly one level**: ALERT stepped down to ACTIVE when the
  score fell to 3.34 (< `active_low` 4.0), and did **not** jump to STABLE — the
  asymmetry introduced in v0.3;
* **re-escalation is immediate** again (cycle 24, score 18.09);
* uploads followed state and interval changes, as designed.

This is the end-to-end behaviour the simulator's unit tests describe, reproduced
on the physical device.

### Bug found: the bus recovery diagnosed a wedge on a healthy bus

The same boot log contained:

```
W (1128) sensor_bus: i2c lines held low (SDA=1 SCL=1); recovering
W (1134) sensor_bus: bus recovery did not clear the lines
```

The `1 1` were *booleans*, i.e. both lines had been read as low — and yet the very
next lines show the bus coming up and the SHT30 being found. So the readings were
wrong, not the bus.

**Root cause, established by experiment rather than guessed.** The first version
configured the pins as `GPIO_MODE_OUTPUT_OD` and then called
`gpio_get_level()` on them. That mode disables the pin's input buffer, so the read
returns a constant 0 — it cannot tell "released high" from "driven low". Measured
on the board with the two lines left alone between cases:

| pin mode | what was done | `gpio_get_level()` |
|----------|---------------|--------------------|
| `OUTPUT_OD` | released (written 1) | **0** |
| `OUTPUT_OD` | driven low (written 0) | **0** — indistinguishable |
| `INPUT` + pull-up | nothing (same line!) | **1** |

So the recovery's trigger was never a level measurement at all: it read a constant
0 and therefore declared *every* boot a wedge, then reported a verdict from the
same broken reading. Two alarming warnings and 16 pointless clock pulses on every
start, on a bus that was fine.

An intermediate hypothesis — that the line had not finished rising from the weak
internal pull-up — was **wrong**, and is recorded here because the first version of
this note stated it. The 2 ms settle below is still worth having, but it was not
the cause.

**Fix:** sample levels only with the pins configured as **inputs** (input buffer
enabled, internal pull-up on), and switch to open-drain exclusively for the
clocking phase. Both the trigger and the verdict now read the line honestly, and a
healthy bus produces no output at all. The build log after the fix:

```
sensor_bus: shared i2c bus ready: SDA=GPIO8 SCL=GPIO9 (one bus for every device on it)
sht30: SHT30 ready at 0x44 (single shot, high repeatability, no clock stretching; channels: temperature, humidity)
sensor: sensor backend ready: SHT30 (temperature + humidity)
```

This is the second defect in this session that only hardware could have surfaced —
it compiles, it looks reasonable, and it lies.

### Open items after session 3

1. The bus recovery **trigger remains unidentified** — it has still never been made
   to fire deliberately. It is defensive, and it now at least does not fire
   spuriously.
2. `publish_call_ok` is still 0 (placeholder broker), so the upload path is verified
   only as far as the MQTT client call.
3. The BH1750, the OLED and the soil sensor remain unused.

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
