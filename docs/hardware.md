# Hardware

## Reference platform

- **MCU**: ESP32-S3 (documented target: ESP32-S3 N16R8 — 16 MB flash, 8 MB octal
  PSRAM; `sdkconfig.defaults` matches)
- **Toolchain**: ESP-IDF v5.4, C
- **Sensors**:
  - BME280 — temperature / humidity / pressure over I2C (implemented)
  - BH1750 — ambient light over I2C (**not implemented**; the light channel is
    reported invalid, and the change detector excludes invalid channels)
- **Connectivity**: 2.4 GHz Wi-Fi + built-in esp-mqtt (MQTT 3.1.1)

## Wiring

### What this firmware needs

One sensor on I²C. Everything else the firmware uses (Wi-Fi, flash, PSRAM) is on
the module itself.

| ESP32-S3 pin | sensor pin | voltage | notes |
|--------------|-----------|---------|-------|
| **GPIO8** | SDA | 3.3 V logic | `CONFIG_AS_SENSOR_SDA_GPIO`. Data line of the shared bus |
| **GPIO9** | SCL | 3.3 V logic | `CONFIG_AS_SENSOR_SCL_GPIO` |
| **3V3** | VCC / VIN | **3.3 V** | Do **not** use 5 V. Many breakouts have a regulator and level shifters and will *run* on 5 V, but they then pull SDA/SCL to 5 V, which is out of spec for the ESP32-S3 |
| **GND** | GND | — | Common ground is required; the bus will not work reliably without it |
| — | address pin | tie to GND or 3V3 | BME280: `0x76` (GND) / `0x77` (VCC). SHT30: `0x44` (GND) / `0x45` (VCC). The configured address must match — see below |

Bus speed is `CONFIG_AS_I2C_FREQ_HZ` = 400 kHz. The driver enables the ESP32's
internal pull-ups, but they are weak (~45 kΩ): for reliable 400 kHz operation use
the pull-ups on the breakout board (most have 4.7 kΩ–10 kΩ) and keep the wires
short, under about 15 cm.

**These pins are configuration, not code.** Change
`CONFIG_AS_SENSOR_SDA_GPIO` / `CONFIG_AS_SENSOR_SCL_GPIO` in
`firmware/main/config.h` and rebuild; no driver mentions a pin number.
`scripts/check_config_parity.py` does not check them, because the simulator has no
notion of a pin.

### Which sensor, and swapping between them

The firmware is **sensor-agnostic**. `sensor.c` owns the API, the read contract and
the shared I²C bus; the chip-specific work is in a backend behind
`sensor_backend.h`:

| `CONFIG_AS_SENSOR_BACKEND` | part | channels it provides | address macro |
|---|---|---|---|
| `1` | BME280 | temperature, humidity, **pressure** | `CONFIG_AS_BME280_I2C_ADDR` |
| `2` (default) | SHT30 / SHT3x | temperature, humidity | `CONFIG_AS_SHT30_I2C_ADDR` |

A backend marks the channels it cannot measure **invalid**, and the change detector
already ignores invalid channels — so swapping sensors changes which numbers
arrive, never how they are used. Nothing in the change detector, the scheduler, the
event logic or the simulator is aware of the choice, which is why the published
simulation results remain valid either way.

Swapping is a one-line change to `CONFIG_AS_SENSOR_BACKEND` (plus the address
macro if the new part is strapped differently). Both backends are always compiled,
so neither can rot; the unselected one is dropped by the linker and costs no flash.

Note that **pressure is only available from the BME280 backend**. With an SHT30 the
pressure channel is invalid and is sent as `"pressure": null` in the MQTT payload,
rather than as a plausible-looking number.

### Pins to leave alone

| pins | why |
|------|-----|
| GPIO26 – GPIO32 | flash and the in-package octal PSRAM (`CONFIG_SPIRAM_CLK_IO=30`, `CONFIG_SPIRAM_CS_IO=26`) |
| GPIO43 / GPIO44 | console UART to the USB bridge |
| GPIO19 / GPIO20 | native USB D− / D+ on the ESP32-S3 |
| GPIO0 / GPIO3 / GPIO45 / GPIO46 | strapping pins; a pull-up or pull-down here changes the boot mode |

To find a sensor you are unsure about, scan the bus rather than guessing:
`i2c_master_probe()` across `0x08`–`0x77`. Be aware that a *floating* SCL line
makes a naive scan report whole blocks of addresses as present; characterise the
lines electrically first (see the method note in
[`hardware_test_log.md`](hardware_test_log.md)).

The driver uses the current ESP-IDF I²C master API (`driver/i2c_master.h`); the
legacy `driver/i2c.h` API emits deprecation warnings from ESP-IDF v5.2 onwards.

## Driver

`firmware/main/sensor.c` owns the I²C transport and the measurement sequence.
`firmware/main/bme280_math.c` owns the calibration parsing and the compensation
equations; it has **no ESP-IDF dependency**, so it is compiled and unit-tested on
the host.

### Measurement mode: forced, one conversion per sample

```
wake → write ctrl_hum + ctrl_meas (mode = 0b01 forced)
     → poll STATUS.measuring with a bounded timeout
     → burst read 0xF7..0xFE → compensate → sleep
```

The chip is never left converting while the MCU sleeps, and it is never left
running in normal mode sampling in the background. `ctrl_meas` bits `[1:0]` are
therefore always `0b01` (forced).

Timing is bounded twice over: `CONFIG_AS_BME280_MEAS_SETTLE_MS` (2 ms) lets the
`measuring` bit be asserted before polling begins, and
`CONFIG_AS_BME280_MEAS_TIMEOUT_MS` (50 ms) is a hard upper bound on the wait. At
oversampling ×1/×1/×1 the datasheet's worst-case conversion time is well under
20 ms, so the timeout is generous; the point is that it exists and that the
driver never blocks indefinitely. If the measurement does not complete, the
sample is marked failed and the main loop retries after the minimum interval
instead of feeding a stale or zeroed reading to the detector.

### Bring-up, and why a failed sensor is not a reading of zeros

`sensor_init()` returns 0 only when the chip answered, the calibration parsed and
the configuration was written; `sensor.c` records that and `sensor_read()` refuses
to do anything until it is true, leaving the caller's buffer untouched. The
contract is enforced in the driver and asserted on the host by
`tests/test_sensor_contract.py`.

When to *retry* bring-up is a separate decision, and it lives in
`sensor_supervisor.c` (pure C, host-tested, `tests/test_sensor_supervisor.py`):

```
while the sensor has never come up:
    if now >= next_attempt_t:
        ok = sensor_init()
        note_attempt(now, ok)        # failure schedules the next attempt at now + min_interval
```

So a board whose BME280 is detached at boot — or connected later — recovers by
itself, at one attempt per `min_interval`, instead of producing an endless stream
of failed reads and an unusable node. The counters (`init_attempts`,
`init_failures`) are logged.

This matters beyond tidiness: the change detector's EMA baseline is fed by
whatever `sensor_read()` returns. Zeros from a dead sensor would be absorbed as
measurements and the node would report a room at 0 °C rather than reporting that
it cannot measure.

### Calibration and compensation

Register map used (BME280 datasheet, Table 16):

| address | field |
|---------|-------|
| `0x88` … `0xA1` (26 bytes) | `dig_T1`…`dig_T3`, `dig_P1`…`dig_P9`, then `dig_H1` at `0xA1` |
| `0xE1` … `0xE7` (7 bytes) | `dig_H2`, `dig_H3`, `dig_H4`, `dig_H5`, `dig_H6` |

`dig_H1` shares the 26-byte burst read that starts at `0x88`; it is **not** part
of the humidity block at `0xE1`. `dig_H4` and `dig_H5` are 12-bit signed fields
assembled from a byte and a nibble of `0xE5`:

```
dig_H4 = (0xE4 << 4) | (0xE5 & 0x0F)
dig_H5 = (0xE6 << 4) | (0xE5 >> 4)
```

That nibble grouping is the one Bosch's own driver implements
(`parse_humidity_calib_data()` in `BoschSensortec/BME280_SensorAPI`), which is
the authority for what the silicon returns; a naive reading of the datasheet
memory map suggests a different grouping for `dig_H5`.
`tests/test_bme280_math.py` builds a register image from known coefficients and
asserts that the parser recovers them, including sign extension of the 12-bit
fields.

The compensation equations are the vendor's double-precision implementations
(`compensate_temperature` / `compensate_pressure` / `compensate_humidity`) with
their physical-range clamps. Using them directly rather than re-transcribing the
integer/Q-format form is deliberate: the integer humidity routine leaves its
result in a 22-fractional-bit accumulator which an earlier revision of this
project divided by 1024 instead of 4096, reporting humidity four thousand times
too large. See [audit_v0.2.md](audit_v0.2.md), issues #1 and #1b.

## Mock sensor (no hardware required)

With `CONFIG_AS_USE_MOCK_SENSOR = 1`, `sensor_read()` returns a deterministic
synthetic sequence so the whole stack (change detection, policy, MQTT, power
management) can be exercised without a board. Mock output is logged as a mock and
is never presented as a measurement.

## Hardware status — what has actually been verified

| item | status |
|------|--------|
| Firmware compiles for `esp32s3` | **Yes** — ESP-IDF v5.4.4, 0 warnings ([build_validation.md](build_validation.md)) |
| Host tests of the driver maths | **Yes** — `tests/test_bme280_math.py`: calibration parsing, nibble assembly, compensation against the vendor equations |
| Host tests of the bring-up contract | **Yes** — `tests/test_sensor_contract.py`, `tests/test_sensor_supervisor.py` |
| Power-management configuration | **Yes, in the build** — `CONFIG_PM_ENABLE=y`, tickless idle and light-sleep callbacks confirmed in the generated sdkconfig |
| On-device sensor reading | `Not measured yet.` |
| I²C bus bring-up / address probe | Implemented, `Not measured yet.` on hardware |
| Wi-Fi + MQTT publish to a live broker | Implemented, `Not measured yet.` on hardware |
| Light-sleep entry on hardware | Implemented and observable (`light_sleep_entries`), `Not measured yet.` |
| Power / energy measurement | `Not measured yet.` |

## Energy / power measurement

AdaptiveSense does **not** perform real energy measurement, and this repository
reports no energy in physical units. The available quantities are
`number_of_uploads`, `estimated_payload_bytes` (from a measured 246-byte payload)
and a dimensionless `upload_energy_proxy`.

`esp_wifi_set_ps(WIFI_PS_MIN_MODEM)` is a **Wi-Fi modem power-save mode**, not a
power measurement and not evidence that the node is low-power. It lets the radio
sleep between DTIM beacons; whether the device actually saves energy is exactly
what has not been measured.

To obtain real numbers:

1. Put a current monitor on the 3.3 V rail (INA219/INA226, Joulescope, or a
   Nordic Power Profiler Kit II).
2. Record active, transmit and light-sleep currents separately.
3. Combine them with the per-phase durations. `power_get_stats()` already reports
   the two sides of the duty cycle: what the application asked for
   (`sleep_requests`, `scheduled_idle_s`) and what the chip actually did
   (`light_sleep_entries`, `light_sleep_s`, from the PM callback). If those two
   diverge, something is holding a PM lock and the node is not sleeping as often
   as the schedule suggests — worth knowing before attaching the meter.
4. Store the result under `results/hardware/` with the instrumentation described,
   clearly labelled as a measurement rather than a simulation.

### Sleep modes

See [power_management.md](power_management.md). Summary: `none` (vTaskDelay only)
and `auto light` (default, via ESP-IDF automatic light sleep) are supported; deep
sleep is rejected at compile time because a reboot would discard the detector's
EMA baselines, the ladder position and the event debounce state.
