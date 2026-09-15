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

| BME280 pin | ESP32-S3 |
|------------|----------|
| VCC / VIN | 3.3 V |
| GND | GND |
| SDA | GPIO 4 (configurable) |
| SCL | GPIO 5 (configurable) |
| ADDR | GND (I²C address `0x76`) or 3.3 V (`0x77`) |

Pins and the I²C address are configured in `firmware/main/config.h`
(`CONFIG_AS_SENSOR_SDA_GPIO`, `CONFIG_AS_SENSOR_SCL_GPIO`,
`CONFIG_AS_BME280_I2C_ADDR`). The driver uses the current ESP-IDF I²C master
API (`driver/i2c_master.h`); the legacy `driver/i2c.h` API emits deprecation
warnings from ESP-IDF v5.2 onwards.

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
| Host unit tests of the driver maths | **Yes** — `tests/test_bme280_math.py`, including parsing and bounds |
| On-device sensor reading | `Not measured yet.` |
| I²C bus bring-up / address probe | Implemented, `Not measured yet.` on hardware |
| Wi-Fi + MQTT publish to a live broker | Implemented, `Not measured yet.` on hardware |
| Power / energy measurement | `Not measured yet.` |

## Energy / power measurement

AdaptiveSense does **not** perform real energy measurement, and this repository
reports no energy in physical units. The available quantities are
`number_of_uploads`, `estimated_payload_bytes` and a dimensionless
`communication_energy_proxy`.

To obtain real numbers:

1. Put a current monitor on the 3.3 V rail (INA219/INA226, Joulescope, or a
   Nordic Power Profiler Kit II).
2. Record active, transmit and light-sleep currents separately.
3. Combine them with the per-phase durations that `power_mgmt.c` already measures
   (`power_get_stats()` returns the summed requested and actual sleep time).
4. Store the result under `results/hardware/` with the instrumentation described,
   clearly labelled as a measurement rather than a simulation.

### Sleep modes

See [power_management.md](power_management.md). Summary: `none` (FreeRTOS delay)
and `light` (default) are supported; `deep` is rejected at compile time because a
reboot would discard the detector's EMA baselines, the ladder position and the
event debounce state.
