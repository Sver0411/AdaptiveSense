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
| **BME280** | — | **Not found on any pin pair tested** | **This is what the firmware needs.** A BMP280 would not do: no humidity channel, and the driver rejects its chip id |

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
voltage, cautions, pins to avoid). Summary: a BME280 breakout on
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
Two backends ship (BME280 and SHT30/SHT3x), the bus is owned once by
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

## Session 4 — 2026-09-17, full network chain over a phone hotspot

The node had never published anything: the broker URI and the Wi-Fi credentials
were placeholders, so `publish_call_ok` had always been 0. This session closed the
whole chain — sensor → policy → Wi-Fi → MQTT → broker → collector — on a phone
hotspot, because the campus network was not available.

### Setup

| | |
|---|---|
| hotspot | an iPhone personal hotspot, `172.20.10.1/24` |
| Mac | `172.20.10.3`, macOS firewall **off** |
| node | `172.20.10.5` |
| broker | Mosquitto **2.1.2** on the Mac, `listener 1883 0.0.0.0` + `allow_anonymous true` |
| collector | `server/mqtt_collector.py --host 172.20.10.3 --topic 'adaptivesense/#'` |
| credentials | written to `firmware/main/config.h` only, which is git-ignored; verified that no tracked file contains them |

Note that Mosquitto 2.x binds to loopback unless a `listener` is given explicitly,
so the `listener 1883 0.0.0.0` line is what makes the broker reachable from the
node at all.

### 1–3. Association, address, MQTT session

```
I (1700) wifi:state: init -> auth (0xb0)
I (1709) wifi:state: auth -> assoc (0x0)
I (1720) wifi:state: assoc -> run (0x10)
I (1750) wifi:connected with 123, aid = 20, channel 6, BW20, bssid = 0a:5d:7f:d0:63:0f
I (1751) wifi:security: WPA2-PSK, phy: bgn, rssi: -40, cipher(pairwise:0x3, group:0x3), pmf:0
I (2803) comm: got ip 172.20.10.5
```

and on the broker side, independently:

```
New connection from 172.20.10.5:57953 on port 1883.
New client connected from 172.20.10.5:57953 as ESP32_a7A8A4 (p4, c1, k120).
```

* association completes in about 1.7 s and the node gets `172.20.10.5`;
* the hotspot negotiated **WPA2-PSK** — the firmware's
  `threshold.authmode = WIFI_AUTH_WPA2_PSK` is therefore satisfied, and no
  authentication-mode change was needed (this was the most likely failure);
* `k120` is the **keepalive of 120 s** on the wire, which independently confirms
  that wire-level change actually took effect, not just the config file;
* **no client isolation**: the node reached the Mac on port 1883 directly, so the
  fallback diagnosis plan (inspect the broker log for a connection attempt) was not
  needed. The broker log is still the right discriminator if it ever is.

### 4–5. Publishes actually land

Portion of the node log, in which `publish_call_ok=1` appears for the first time:

```
cycle=1  t=0.3   state=0 interval=20.0s score=0.00  upload_requested=1 publish_call_ok=0 temp=27.79 hum=55.94
cycle=2  t=20.2  state=0 interval=20.0s score=0.22  upload_requested=0 publish_call_ok=0 temp=27.82 hum=55.76
cycle=3  t=40.0  state=1 interval=15.0s score=11.99 upload_requested=1 publish_call_ok=1 temp=29.60 hum=63.89
cycle=4  t=54.9  state=1 interval=10.0s score=7.89  upload_requested=1 publish_call_ok=1 temp=29.43 hum=59.85
...
cycle=15 t=114.4 state=2 interval=5.0s  score=13.68 upload_requested=1 publish_call_ok=1 temp=29.22 hum=68.15
```

and the matching broker records:

```
Received PUBLISH from ESP32_a7A8A4 (d0, q0, r0, m0, 'adaptivesense/data', ... (239 bytes))
Sending PUBLISH to auto-2F83460F-F40D-F2B4-9991-8D6E6C9399B4 (... 'adaptivesense/data', ...)
```

**Cross-check, which is the point of doing it this way:** the node counted
`publish_call_ok=1` **15 times** in 200 s, and the collector's CSV contains
**15 rows**. Exact agreement, so nothing was silently dropped between the two.

The two `publish_call_ok=0` at the start are correct, not a fault: the node samples
at t = 0.3 s and the Wi-Fi/MQTT session is only up at about t = 2.8 s, so the first
upload is requested before there is anywhere to send it. The counters report it as
`call_failed=1` rather than hiding it. See the open items.

### 6. Field-by-field check of a received payload

First row of `results/live/MQTT-net.csv`:

| field | value | |
|-------|-------|---|
| `device_id` | `node-01` | as configured |
| `timestamp` | `39995` | ms since boot (not epoch — see open items) |
| `temperature` | `29.6` | matches the node log for that cycle exactly |
| `humidity` | `63.89` | matches |
| `pressure` | *(empty)* | JSON `null`; an SHT30 has no pressure channel |
| `light` | *(empty)* | JSON `null`; no light driver |
| `sampling_interval` | `15.0` | the ACTIVE rung at that moment |
| `state` | `ACTIVE` | |
| `event` | `False` | |
| `valid` | `{"humidity":true,"light":false,"pressure":false,"temperature":true}` | the map agrees with the values: the two empty channels are exactly the two marked invalid |

Over the 15 rows: states `ACTIVE` ×11 and `ALERT` ×4, temperature 28.13 – 29.85 °C,
humidity 54.55 – 68.15 %RH — i.e. the payload tracks the same physical excursion
the state machine was responding to.

The collector's field list did not include `valid`, so that map was being dropped
on the floor; it now records it (serialised as compact JSON). Without it, `pressure`
and `light` would be blank with no way to tell "no sensor" from "reading missing".

### 7–8. Several cycles, and light sleep finally happens

29 sampling cycles over 200 s, and the duty-cycle summary printed twice:

```
comm: publish stats: requested=5  call_ok=4  call_failed=1 mqtt_connected=1
main: duty cycle: idle_requests=9  scheduled_idle=89.7s  light_sleep_entries=1183 light_sleep=72.6s
comm: publish stats: requested=11 call_ok=10 call_failed=1 mqtt_connected=1
main: duty cycle: idle_requests=19 scheduled_idle=139.5s light_sleep_entries=1866 light_sleep=113.0s
```

**`light_sleep_entries` went from a permanent 0 to 1183 and then 1866.** This was
the open question from session 1, and the cause is now confirmed rather than
theorised: with placeholder credentials the Wi-Fi driver sat in a reconnect loop
and held a PM lock continuously, so the chip never reached the idle state the power
manager needs. Once the node associates and enters modem sleep, the driver releases
that lock between DTIM beacons and automatic light sleep starts immediately.

The two counters the v0.3 logging change introduced are what make this readable:
`light_sleep_s / scheduled_idle_s` = 72.6 / 89.7 = **81 %** of the requested idle
time is actually spent asleep. Note that `light_sleep_entries` (1183) is far larger
than `idle_requests` (9) — the counter ticks per sleep/wake cycle *inside* an idle
window, not per idle window, which is exactly why the two pairs are reported
separately instead of one "sleep" number.

### Open items after session 4

1. **The first publish after boot is always lost.** The node samples at t = 0.3 s,
   long before Wi-Fi associates at t ≈ 2.8 s, so the first upload is requested with
   no transport. It is counted as `call_failed` rather than hidden. Fixing it means
   deferring or buffering the first sample — a behaviour change, not a bug fix, so
   it is recorded and left.
2. **The payload is 238–240 bytes on the wire, not the documented 244.**
   `payload_bytes_per_upload` was measured with a 10-digit millisecond timestamp;
   the node actually sends *milliseconds since boot*, which is 5–6 digits early in a
   session. The constant is a representative size and the difference is ~1.5 %, but
   the assumption should be stated. Real epoch timestamps would need SNTP, which is
   not implemented.
3. **`timestamp` is uptime, not wall-clock time.** A collector cannot correlate the
   stream with anything else without knowing the boot time. SNTP is future work.
4. The broker runs with `allow_anonymous true` on a shared hotspot subnet. Fine for
   a bench session, not for anything longer-lived.
5. `light_sleep` numbers are still *observed* sleep, not *energy*. No current
   measurement was taken: `Not measured yet.`

---

## Session 5 — 2026-09-17, stability and fault recovery

Twenty-five minutes of unattended running with one injected sensor fault, then two
network faults. Everything below is from the serial log and the broker log; nothing
is inferred.

### §1 Long run — PASS

| | |
|---|---|
| duration | **25.2 min** (device uptime 1511 s) |
| total cycles / successful samples | **89 / 89** |
| read failures | **3** reads attempted and failed, all inside the injected sensor fault (§2) |
| Wi-Fi connect / disconnect | 1 / **0** |
| MQTT connect / disconnect / reconnects | 1 / 1 (the boot-time pre-association attempt) / **0** |
| `upload_requested` / `publish_call_ok` | 36 / **35** |
| **collector rows received** | **35 → difference 0** |
| `light_sleep_entries` / `light_sleep_s` | **13 731 / 835.4 s** |
| `scheduled_idle_s` / `idle_requests` | 1026.6 s / 139 |
| **sleep ratio** (`light_sleep_s / scheduled_idle_s`) | **81.4 %** |
| light-sleep counters monotonic | **yes**, over 14 samples |
| panic / watchdog / reboot | **0 / 0 / 0** |
| states seen | STABLE 19, ACTIVE 27, ALERT 43; `event=1` six times |
| STABLE eventually reaches 60 s | **yes**; interval sequence `20,40,60 → 5,15,5,15,10,5 → 20,40,60` |

The 60 s heartbeat and the 120 s keepalive both ran for the whole window: the
collector's rows are 60 s apart in the settled phase, and the broker log shows
`k120` with PINGREQ/PINGRESP exchanges.

> **Corrected in session 6.** This row read *59 read failures* when it was first
> written. Only **3** reads were ever attempted; the counter was being inflated by
> the accounting defect fixed in session 6 §1, which counted every cycle spent
> waiting for a re-probe as another failed read. The corrected number is above.

> **Not measured:** heap usage. The firmware does not log free heap, so "no memory
> leak" cannot be claimed — only "no crash, no reboot, and the light-sleep counters
> grew normally for 25 minutes". Adding a heap line would be a small change; it is
> recorded here as an unmeasured item rather than asserted.

### §2 Sensor runtime failure and recovery — FIXED AND RETESTED

**Reproduced first, before any change.** With the previous firmware, pulling the
sensor produced this every 5 s for the whole outage:

```
W main: no usable reading at cycle 4 (sensor ready); next attempt in 5s
```

The state said **ready** while every read failed, and there were **zero**
`sensor init failed` lines — `sensor_init()` was never called again, because
`initialized` was set once and never cleared. Recovery happened only by luck: the
driver's device handle was still registered, so when the part reappeared the
existing handle worked again.

**Fix.** `sensor_supervisor.{c,h}` now track consecutive read failures and clear
`initialized` once they reach `CONFIG_AS_SENSOR_FAILURES_BEFORE_UNAVAILABLE` (3),
which makes bring-up reachable again under the same rate limit. The state name
distinguishes `ready`, `ready (reads failing)` and `unavailable (will retry)`.
Counters were added so an outage can be counted rather than inferred.
`change_detector` and `adaptive_scheduler` were **not touched**, and a failed read
still `continue`s before the detector, so nothing bad can reach the EMA.

**Retested on hardware, same procedure.** The fault was injected by removing the
whole sensor module for about five minutes:

```
[1173ms]   I sensor initialised after 1 attempt(s)                boot, healthy
[241091ms] W no usable reading at cycle 7 (sensor ready (reads failing))   1st failure
[246092ms] W no usable reading at cycle 8 (sensor ready (reads failing))   2nd, tolerated
[251093ms] E sensor became unavailable after 3 consecutive read failures (3 in total); will re-probe
[251094ms] W no usable reading at cycle 9 (sensor unavailable (will retry))
[256145ms] W sensor init failed (2 attempt(s)); retrying no sooner than 5s from now
...
[526107ms] W sensor init failed (29 attempt(s))
[536138ms] I sensor initialised after 30 attempt(s)               recovered via re-probe
```

* the state name is now honest at every step;
* bring-up became reachable again and was attempted **29 times**, rate-limited;
* recovery went through an actual **re-probe**, not a lucky stale handle;
* the detector saw nothing during the outage: cycles jump from **6 to 66**, so the
  reading cycles in between never reached it. **3** reads were actually attempted
  and failed; the other cycles attempted nothing. The *59 failures* this line used
  to quote came from the counting defect corrected in session 6 (§1) — it counted
  every waiting cycle as a failed read.

**Two behaviours worth recording, neither a defect:**

1. The re-probe cadence on hardware is **~10 s**, not the configured 5 s. The
   supervisor schedules the next attempt at `t + retry_interval` while the main loop
   also wakes at `t + retry_interval`, and `pdMS_TO_TICKS` truncation makes the loop
   wake marginally early, so the `now >= next_attempt_t` test only passes on
   alternate iterations. It still means "at most one attempt per interval", which is
   what the limit is for. Left as is, and recorded rather than quietly tightened.
2. The **first good sample after recovery reads as a large change** (score 14.93,
   ALERT) because the EMA baseline is still where it was before the outage. Here the
   temperature really had moved (the module was handled), so the report is correct.
   Removing the effect entirely would mean rebuilding the baseline on recovery, which
   is a behaviour change and was not done.

**Tests added:** 8 in `tests/test_sensor_supervisor.py`, including one that sets the
threshold to 0 to reproduce the old behaviour, so the difference is asserted rather
than described.

### §3 Wi-Fi failure and recovery — PASS, with a caveat about this test setup

Two halves, because of a timing slip on my side.

**Half 1, hotspot removed while the node ran normally** (what the protocol asked for):

```
W comm: Wi-Fi disconnected; reconnecting      ×32, every 2.41 s, over 74.7 s
cyc=7 t=238.7 ... upload_requested=1 publish_call_ok=0
panic / watchdog = 0,  read failures = 0
```

The reconnect attempts are bounded and evenly spaced — not a busy loop — though
2.41 s is frequent: that is ESP-IDF's own reconnect cadence, not a backoff. The
scheduler kept running on the same ladder throughout, and the failed upload was
counted rather than hidden.

**Half 2, network returns.** The node had rebooted when I restarted the capture
tool, so this is the harder case: a cold boot with no network at all. It retried 13
times, then when the hotspot reappeared:

```
[32479ms] wifi:state: init -> auth (0xb0) -> assoc -> run
[32570ms] wifi:connected with <hotspot>, channel 6, rssi -40, security: WPA2-PSK
[33687ms] comm: got ip 172.20.10.5
[130087ms] I comm: MQTT connected                      ← automatic
cyc=7 t=238.8 ... upload_requested=1 publish_call_ok=1  ← publishing resumed
```

The collector received the post-recovery payload (its last row's timestamp matches
cycle 7), so the whole chain came back **with no intervention**. The reboot happened
at the start of the capture, while the hotspot was still off — before there was
anything to recover — so this is not "recovering by rebooting the node".

**The caveat, and it is a real operational trap.** Between the two halves, MQTT
failed for 97 s for a reason unrelated to the firmware: **when the hotspot went away,
the Mac joined a different network** (`10.60.24.32`, gateway `10.60.255.254`) and the
node's configured broker URI — a hard-coded `mqtt://172.20.10.3:1883` — pointed at an
address that no longer existed on any shared subnet. The node kept retrying in a
bounded way (`esp-tls: select() timeout` three times, ~15-25 s apart) and reconnected
by itself within seconds of the Mac rejoining the hotspot.

Two lessons for anyone repeating this:

* **the Mac must stay on the hotspot** for the duration; if it roams, the node
  cannot reach the broker no matter what the firmware does;
* **a hard-coded broker IP is fragile.** The node has no way to discover the broker,
  so if the address moves, the configuration must be rebuilt. mDNS or a static lease
  would fix it; neither is implemented.

### §4 MQTT broker failure and recovery — PASS

Mosquitto was stopped at T+70 s and restarted at T+222 s; the node was not touched.

```
[16206ms]  MQTT connected
[41128ms]  cyc=3 up=1 ok=1                             publishing normally
[70384ms]  MQTT error / disconnected                   broker stopped
[85421ms]  ... every 15.0 s, 11 attempts in total      bounded retry
[121126ms] cyc=5 up=1 ok=0                             policy wanted to send, transport failed → counted
[235756ms] MQTT connected                              broker back → reconnected in 14 s
[241127ms] cyc=7 up=1 ok=1                             publishing resumed
```

* the node did not reboot and did not crash (0 panic/watchdog), and the sensor and
  scheduler were unaffected — the interval ladder continued `40, 40, 60, 60` and
  there were zero read failures;
* retries were uniformly 15.0 s apart, i.e. bounded;
* the broker log shows **both** clients reconnecting by themselves:
  `New client connected from 172.20.10.5 as ESP32_a7A8A4 (p4, c1, k120)` and
  `... from 172.20.10.3 as auto-A1C2FFEC...` (the collector).

**Honest limitation.** The publish the node made at `cyc=5` was accepted by the
broker but nobody was subscribed at that moment — the collector was still in its own
reconnect backoff (paho backs off exponentially, ~83 s here), so it missed that one
message. The node's `call_ok` is truthful; the equality "call_ok count == collector
rows" only holds while the collector is continuously connected.

### Two problems I caused myself during this session

Recorded because they cost time and would cost anyone else the same:

1. **Starting the broker with `nohup … &` inside a script killed it when the script
   exited** (`Reloading config.` then gone). Background processes in this
   environment need to be launched as managed background tasks to survive.
2. **A "just look, do not reset" serial read still reset the board.** `pyserial`
   drives DTR/RTS when the port is opened, which pulses the ESP32 auto-reset circuit;
   setting them before `open()` is not sufficient. The node rebooted mid-session,
   which is why §3 is split into two halves.

### Not tested in this session

* **BH1750, OLED and the soil sensor** — deliberately out of scope for this round.
* **SNTP / wall-clock timestamps** — the payload still carries milliseconds since boot.
* **Buffering or deferring the first sample**, which is still lost because the node
  samples before Wi-Fi associates. Counted as `call_failed`, by design for now.
* **MQTT TLS or authentication** — the broker ran anonymous.
* **Heap usage over time** — no heap logging exists.
* **Energy** — no current measurement was taken.

---

## Session 6 — 2026-09-18, cleanup: accounting, sensor state, and what the docs claim

A narrow cleanup pass. No new features, and no algorithm changes: the change
detector, the adaptive scheduler, the interval ladder, the event threshold, the
upload policy and the simulator are untouched (`git diff` over those paths is
empty). What follows is four corrections and the hardware evidence for them.

### §1 Read-failure accounting — FIXED AND RETESTED

**The defect.** `main.c` combined readiness with the read result in one condition:

```c
if (!sensor_sup_ready(&sensor_sup) || sensor_read(&reading) != 0) {
    sensor_sup_note_read_failure(&sensor_sup, t_sample);
```

Short-circuit evaluation means the second operand is never evaluated once the first
is true. So a cycle in which the sensor was already unavailable — **no read was
attempted at all** — still incremented both failure counters. Every cycle spent
waiting for a re-probe added one more.

That is where session 5's *59 read failures* came from. Three reads were attempted;
the other 56 waiting cycles were counted anyway. The number in §1 and §2 of that
session has been corrected above.

**The fix** is a separation the defect had merged, in `main.c`:

```
1. not ready    → no read happens, and nothing is counted; wait for re-probe
2. attempted → failed → this is the only case that counts a failure
3. succeeded    → resets the consecutive-failure streak
```

Only case 2 touches the counters. The two counters also now say what they mean at
every step: case 2 prints `N consecutive, M in total`, case 1 prints the running
total, which is the line where the freezing is visible.

**Tests.** A new `cycles` subcommand in `tests/c_host/sensor_host_main.c` replays
main.c's order — bring-up first, then the three-way read — one cycle at a time, so
the assertions are made against the loop's behaviour rather than a description of
it. Eight new tests in `tests/test_sensor_supervisor.py` cover: a healthy sensor
counts nothing; the counter equals the number of reads actually attempted; the
threshold is what ends the failures; **the waiting cycles do not move it**; no read
is attempted while the sensor is known to be down; re-probes are attempted and
rate-limited; recovery clears the streak; and the total is a record of the outage
rather than live state, so recovery does not erase it.

The tests were checked against the old behaviour, not just the new one: with the
short-circuit form restored in the driver, 5 of them fail; with the fix, 22 pass.
`scripts/check_config_parity.py` also gained a structural rule that refuses the
`!sensor_sup_ready(...) || sensor_read(...)` shape in `main.c` outright, alongside
the existing `power_mgmt.c` rules, so the pattern cannot come back unnoticed.

**Hardware.** Flashed, then captured through one continuous session. The SHT30
module was pulled at device uptime ≈117 s and left out for ~3 minutes:

```
[121118ms] W sensor read failed at cycle 5 (ready (reads failing)); 1 consecutive, 1 in total
[126119ms] W sensor read failed at cycle 6 (ready (reads failing)); 2 consecutive, 2 in total
[131120ms] E sensor became unavailable after 3 consecutive read failures (3 in total); will re-probe
[131120ms] W sensor read failed at cycle 7 (unavailable (will retry)); 3 consecutive, 3 in total
[136179ms] W sensor init failed (2 attempt(s)); retrying no sooner than 5s from now
[136180ms] W no reading at cycle 8 (sensor unavailable (will retry)); 3 read failure(s) on record, waiting for re-probe in 5s
...
[281137ms] W no reading at cycle 37 (sensor unavailable (will retry)); 3 read failure(s) on record, waiting for re-probe in 5s
```

| | |
|---|---|
| read failures | **3** — cycles 5, 6, 7, each a read that was genuinely attempted |
| unavailability events | **1**, at the third failure |
| waiting cycles with no read attempted | **30** (cycles 8–37), spanning **145 s** |
| distinct `read_failures_total` values across those 30 cycles | **{3}** |
| what the old counting would have produced | 3 + 30 = **33** |

The module was plugged back in and the node recovered on its own:

```
[286168ms] I sensor initialised after 17 attempt(s)          re-probe succeeded
cyc=38..68  T=28.41→26.99  RH=56.72→59.45  continuous reads resumed
```

15 failed re-probe attempts preceded it, spaced 9.9–10.0 s apart (bounded, the
same ~10 s cadence session 5 recorded and explained). 0 panics, 0 watchdog, 0
reboots across the whole window. Nothing was restarted by hand; the capture was
started once, at the beginning, and ran through both injections.

### §2 `sensor.c` internal state — FIXED AND RETESTED

**The defect.** `sensor_init()` could fail *after* an earlier success. On that path
it returned `-1` without clearing `s_sensor_initialized`, so the state left behind
was

```
sensor_is_initialized() == true        active backend == NULL
```

— the flag said usable while the backend had already been torn down. A caller that
trusts `sensor_is_initialized()` would read through a handle that no longer existed.

**The fix.** A bring-up attempt now begins by declaring the sensor unusable and only
the success path at the bottom puts it back, which maintains

```
s_sensor_initialized == (s_active != NULL)
```

on every path, including the two early returns (no backend selected, bus
unavailable).

**Tests.** The mock build now exposes `sensor_mock_stats_t`, whose `backend_live`
stands in for a real build's `s_active != NULL`, so the two halves of the invariant
can be compared rather than assumed. Six new tests in `tests/test_sensor_contract.py`:
first init succeeds; **a failed re-init reports itself uninitialised**; no stale
handle survives it; a read after a failed re-init is refused *and the refusal never
reaches a backend*; recovery follows a later successful init; every attempt is
counted. Checked against the old behaviour: 3 of them fail with the previous code
and pass with the fix — including the one that shows the refused read reaching a
backend, which is the stale-handle defect observed rather than argued.

On hardware the invariant is visible in the log as the absence of its violation:
`initialised after 17 attempt(s)` appears exactly once, only when bring-up
succeeded, and no read was attempted during the 30 waiting cycles. There is no
hardware-visible `initialized=true / active=NULL` state, and no read was served from
a torn-down backend.

### §3 `BMP280` support claim — CORRECTED

The driver is a **BME280** backend and only that. It checks `BME280_CHIP_ID = 0x60`,
reads the humidity calibration and humidity registers, and outputs humidity. A
BMP280 reports chip id `0x58`, has no humidity channel at all, and is **rejected by
the chip-id check** — so "supports BMP280" was not merely unsupported, it
contradicted the code.

Every `BME280 / BMP280` and "supports BMP280" claim in `README.md`, `README_zh.md`,
`docs/architecture.md`, `docs/hardware.md`, `docs/hardware_test_log.md`,
`firmware/main/config.example.h`, `sensor.c`, `sensor.h`, `sensor_backend.h` and
`sensor_bme280.c` was changed to `BME280`. No BMP280 driver was added — the goal was
to stop overstating, not to add support.

Three `BMP280` mentions remain on purpose, and all three explain that it is a
*different* part that this backend will refuse. `sensor_bme280.c` now states it
directly:

```c
 * BME280 only. A BMP280 is a different part: it reports chip id 0x58 instead of
 * 0x60, has no humidity registers, and is therefore rejected by the chip-id check
 * below rather than silently mis-driven.
```

### §4 Stale BME280-only descriptions in README and main.c — CORRECTED

The default backend on this build is the SHT30 and the sensor layer has been
sensor-agnostic for some time, but the architecture description still read as if the
BME280 were the one sensor. Fixed:

* `README.md` / `README_zh.md`: the flow is now `WAKE → READ SENSOR → SCORE CHANGE
  → ADAPTIVE POLICY → PUBLISH → IDLE`, with the sensor layer shown as an abstraction
  over two backends (SHT30/SHT3x, and BME280 as the optional one), and the physical
  build identified as ESP32-S3 + SHT30.
* the Mermaid diagram nodes no longer say `sensor.c: BME280 I2C, forced mode`; they
  say `sensor abstraction / SHT30 / BME280 / shared I2C bus`.
* `main.c`'s loop comment no longer says `READ SENSOR (forced-mode BME280
  conversion)`. The BME280's forced-mode sequence is documented in
  `sensor_bme280.c`, which is where a chip-specific detail belongs — not in the main
  loop's outline.

### Verification for this session

```
pytest:          181 passed (was 167; +8 accounting, +6 contract)
config parity:   60/60 PASS (was 56; +4 structural rules on main.c)
ESP-IDF build:   clean build from scratch, rc=0
warnings:        0 warnings, 0 errors, image 903,984 B (0xdcb30)
simulation:      unchanged; analysis/ runs to completion
core algorithm:  change_detector / adaptive_scheduler / simulator / analysis —
                 zero diff
```

### Not tested in this session, and one recorded-but-not-fixed boundary

* **`scheduler requests an upload` ≠ `MQTT delivered it`.** The first sample after
  boot is still lost because the node samples before Wi-Fi associates; it is counted
  as a failed publish rather than hidden. That is a known boundary of the design and
  it was **not** changed here, on purpose.
* no BH1750, OLED, soil, SNTP, pending-publish buffering, or Wi-Fi reconnect
  backoff — out of scope for this pass.
* heap usage still unmeasured (no heap logging), so "no leak" is still not claimed,
  only "no crash and no reboot".

---

## Session 7 — 2026-09-18, BH1750 light channel: driver, merge, and hardware

The light channel was the one part of `sensor_read_t` that had never carried a real
value: every payload went out with `"light": null` and `"valid": {"light": false}`.
This session puts a BH1750 / GY-302 behind it — on the bus that already existed,
without touching the primary sensor's role, the algorithm, or the payload schema.

Board: ESP32-S3, I2C SDA=GPIO8 SCL=GPIO9, SHT30 at 0x44, BH1750 at 0x23. One bus,
owned by `sensor_bus.c`; no second bus was created.

### Architecture: an optional channel, not a third backend

`CONFIG_AS_SENSOR_BACKEND` chooses which chip provides the *primary*
environmental reading. The BH1750 was deliberately kept out of that choice: it
does not replace the SHT30, it adds one channel to it.

```
shared I2C bus (sensor_bus.c)
├── primary environmental backend   CONFIG_AS_SENSOR_BACKEND
│   ├── SHT30 / SHT3x   temperature + humidity     <- this build
│   └── BME280          temperature + humidity + pressure
└── optional light channel          CONFIG_AS_USE_BH1750
    └── BH1750 / GY-302 light
```

`sensor_read()` therefore does two things in order: read the primary backend, then
merge the optional light channel into the same `sensor_read_t`. On this build that
yields temperature and humidity from the SHT30, light from the BH1750, and
pressure invalid because nothing on the board measures it.

The BH1750 knows nothing about the change detector, the scheduler, MQTT or
events: it turns I2C bytes into lux.

### One-shot, not continuous — and why

The driver uses One Time H-Resolution (`0x20`), not Continuous H-Resolution
(`0x10`). The node samples every 5-60 s; continuous mode re-converts about every
120 ms and draws measurement current the whole time, including while the ESP32 is
in light sleep and the board's 3.3 V rail is still up — on the order of ten
thousand conversions per sample, all but one discarded, in a project whose whole
point is how much it sleeps.

One-shot converts when asked and returns to power-down by itself. Its cost is a
bounded wait: `CONFIG_AS_BH1750_MEAS_TIME_MS = 180`, the datasheet maximum at the
default MTreg (typical 120 ms). That is at most 3.6 % of a 5 s interval and 0.3 %
of a 60 s one. The wait is a single constant, never a poll-for-completion loop;
every failure path returns -1. It is not claimed to be "the most efficient
possible" — only that it matches the sampling model and does not spend current on
measurements nobody reads.

Conversion is `lux = raw / 1.2`, the datasheet factor for the default MTreg of 69.
It is anchored to real light rather than taken on trust: in the first hardware
session this very module answered with raw = 66, which is 55.0 lx, and the host
test suite pins that value.

### Failure semantics: light is optional, temperature is not

| what failed | result |
|---|---|
| primary backend | `sensor_read()` returns -1, no channel valid |
| light channel | `sensor_read()` returns **0**; only `light` is invalid |

A node that exists to report temperature and humidity must not go dark because an
optional channel went quiet. The light sensor also keeps its own small state
(`available`, `consecutive_failures`, `next_probe_ms`) in `bh1750_proto.c` instead
of joining `sensor_supervisor.c`: a BH1750 failure must never trigger a primary
re-probe, and the two are verified separately below.

Two consecutive failed readings declare it absent, after which it is probed once
per `CONFIG_AS_MIN_INTERVAL_S` — a probe being simply an attempt at a measurement,
since the part has no identity register worth reading. Recovery needs no restart.

### Host tests

26 new. `tests/test_bh1750.py` (20) covers the conversion at the boundaries
(0, the module's reference 66, the register maximum 65535 -> 54612.5 lx), that it
is finite, non-negative and monotonic, and the measurement over a fake transport:
success, dark, maximum, failed command write, **short frame**, read error, and
that the conversion wait is bounded and configurable. It also replays the
availability policy: one failure tolerated, two declares it gone, no traffic at
all during backoff, and recovery after the interval without a restart.

`tests/test_sensor_contract.py` gained 6 merge tests, using a mock seam for the
light channel: primary ok + light ok -> everything valid; primary ok + light
failed or absent -> `rc == 0` with only `light` invalid; primary failed -> `rc ==
-1` with nothing valid.

Both groups were checked against the *old* behaviour, not just the new: mutating
the driver so the light channel is always invalid fails 2 tests, and the merge
tests fail as designed when the merge is removed.

```
pytest:          206 passed (was 181)
config parity:   63/63 PASS (BH1750 parameters added to the firmware section)
ESP-IDF build:   clean build, rc=0, 0 warnings, 0 errors
image size:      905,152 B (0xdcfc0), +1,168 B over the previous build
simulation:      dataset/ and results/ unchanged - the benchmark numbers did not move
```

### Hardware — one continuous capture, 62 cycles

Boot, then normal running, then the light-response experiment, then the hot-plug
test, all inside a single 20-minute capture (a second capture would have reset the
board, which would invalidate the recovery claim).

```
[1174ms] I sensor: sensor backend ready: SHT30 (temperature + humidity)
[1221ms] I bh1750: optional light channel enabled: BH1750 at 0x23
                   (one-shot H-resolution, 180 ms conversion, re-probe every 5s)
[1240ms] I main: sensor initialised after 1 attempt(s)
cycle=1  lux=141.7
```

| | |
|---|---|
| total cycles | **62** |
| light valid | **52** |
| `light=na` | 10, all inside the deliberate unplug window |
| negative lux / NaN / Inf | **0 / 0 / 0** |
| above the register maximum (54612.5) | **0** |
| lux range | 0.8 - 757.5 |
| primary unavailable events | **0** |
| panic / watchdog / reboot | **0 / 0 / 0** |

A second, unattended 35-minute capture was then left running to accumulate reads
and sleep statistics without anyone touching the board: **38 cycles, 38 light
readings, 0 `light=na`, 0 primary failures, 0 reboots**. Across both captures that
is 100 cycles and 90 valid light readings.

No narrow "plausible lux range" is asserted: the check is for obvious nonsense
(negative, NaN, out of range), not for what a room ought to look like.

### Light response, and that it reaches the algorithm

Three states, each held for at least two samples.

```
ambient      102.5 / 103.3 / 115.8 / 105.8 / 145.8 / 145.8 lx   state=STABLE  interval=60s
covered        0.8 lx                                            score 1.15 -> 4.52
                                                                 state STABLE -> ACTIVE
                                                                 interval 60 -> 15 -> 10 -> 5s
flashlight   382.5 / 410.8 ... peak 757.5 lx                     score -> 11.08
                                                                 state ACTIVE, interval 15s
```

The point is not that a number moved. The same capture shows the chain: the light
channel changed, the score responded (1.15 -> 4.52 -> 11.08), the state changed
(STABLE -> ACTIVE) and the interval ladder reacted (60 -> 15 -> 10 -> 5 s), with
upload decisions following. Once the new level held, the score decayed
(4.52 -> 2.27 -> 1.82 -> 1.56 -> 1.44 -> 1.33 -> 1.00 -> 0.75) and the interval
climbed back (5 -> 20 -> 40 -> 60 s), which is the intended behaviour rather than
a stuck alarm.

No algorithm parameter was touched: `noise_floor_light`, the thresholds, the
ladder and the upload policy are unchanged. The existing parameters were simply
given a real channel to work on.

### MQTT and the collector

The payload schema was not changed, and neither was the collector's CSV — the
`light` column and the `valid` map already existed. What changed is what arrives
in them.

```
temperature=25.07  humidity=56.94  light=152.5  (pressure empty)
valid = {"humidity":true,"light":true,"pressure":false,"temperature":true}
```

Cross-checked cycle by cycle against the serial log, over the last boot segment of
the CSV (37 rows, uptime 20.2 s - 1259.6 s):

| | |
|---|---|
| samples correlated | 36 |
| lux mismatches | **0** |
| `valid` map anomalies | **0** |
| rows with `light` non-null | **34 / 37** (the 3 nulls are the unplug window) |
| `publish_call_ok=1` vs collector rows | 36 vs 37 |

The difference of one is explained rather than waved away: the last collector row
is timestamped 1259.6 s, after this capture ended at ~1200 s, so it is a publish
the node made after the serial capture stopped.

### Hot-plug: light only, and it comes back by itself

BH1750 unplugged for about 2.5 minutes while the node ran:

```
cycle 43-52   light=na    temperature 25.0-25.2, humidity 56.3-57.1  (normal)
              adaptive sampling continued: interval 5 -> 20 -> 40 s
sensor became unavailable   0     <- the primary supervisor was never involved
sensor init failed          0     <- no primary re-probe, no teardown
no reading at cycle         0     <- the primary measurement never failed
panic / watchdog / reboot   0
```

Plugged back in, no restart:

```
cycle 53   lux=45.8    <- first successful re-probe
cycle 54   lux=140.8
cycle 55+  lux=151.7 / 152.5 / 152.5 ...
```

So: an absent light sensor costs exactly one channel, and it comes back on its own.

### Power regression

Automatic light sleep still happens. Measured over a clean, unattended window
with the BH1750 in the loop:

| | scheduled_idle_s | light_sleep_s | ratio |
|---|---|---|---|
| before the BH1750 (session 5) | 1026.6 | 835.4 | 81.4 % |
| with the BH1750 (this session) | **1614.0** | **1251.8** | **77.6 %** |

`light_sleep_entries` grew monotonically across the window (5 277 -> 13 217 ->
21 272), which is the counter that would stop moving if something were holding the
chip awake.

`light_sleep_s` is nowhere near 0, so there is no regression of the kind that
would matter — no busy loop, no background task spinning, no polling that keeps
waking the chip. The logs stay quiet (170 lines over the whole window, and no
BH1750 error or warning at all). The few percentage points of difference are not
attributed to anything in particular here: the two runs were on different days
with different Wi-Fi conditions, so this is a check for obvious degradation, not
a measurement of the BH1750's own cost. Measuring that would need a current
measurement, which was not taken.

### Not done in this session

* **No current measurement.** The sleep ratio above is a software counter, not
  power. The BH1750's own contribution is therefore *not measured*, only bounded
  by "sleep still happens".
* OLED, soil moisture, SNTP, pending-publish buffering, Wi-Fi backoff, BME280 on
  hardware, deep sleep: all untouched, all still out of scope.
* The simulator's light channel and the physical one remain different things:
  nothing here is evidence about the published detection rates, which come from
  synthetic signals.

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
