# Hardware

## Reference platform

- **MCU**: ESP32-S3 (tested target: ESP32-S3 N16R8 — 16 MB flash, 8 MB octal
  PSRAM)
- **Toolchain**: ESP-IDF v5.0+, C
- **Sensors**:
  - BME280 — temperature / humidity / pressure over I2C (primary)
  - BH1750 — ambient light over I2C (optional; not yet wired in the driver)
- **Connectivity**: built-in 2.4 GHz Wi-Fi + esp-mqtt (MQTT 3.1.1)

## Wiring

| BME280 pin | ESP32-S3         |
|------------|------------------|
| VCC / VIN  | 3.3 V            |
| GND        | GND              |
| SDA        | GPIO 4 (configurable) |
| SCL        | GPIO 5 (configurable) |
| ADDR       | GND (I2C addr `0x76`) or 3.3 V (`0x77`)

Pin values and the I2C address are configured in `firmware/main/config.h`
(`CONFIG_AS_SENSOR_SDA_GPIO`, `CONFIG_AS_SENSOR_SCL_GPIO`,
`CONFIG_AS_BME280_I2C_ADDR`).

## Software driver

The BME280 driver in `firmware/main/sensor.c` is a small register-level I2C
driver:

- reads the chip's calibration constants at startup,
- performs on-chip compensation for temperature, pressure and humidity
  following the BME280 datasheet equations,
- reports a per-channel validity flag so the rest of the stack can degrade
  gracefully.

## Mock sensor (no hardware required)

When `CONFIG_AS_USE_MOCK_SENSOR` is `1`, `sensor_read()` returns a
deterministic synthetic sequence instead. This allows the full firmware logic
(change detection, adaptive scheduler, MQTT, power management) to be exercised
without a physical board. Mock output is clearly a mock: it is never presented
as a real measurement and real results must be measured separately.

## Energy / power measurement

AdaptiveSense does **not** currently perform real energy measurement. The values
in the analysis are `energy_proxy_mj` (uploads × configurable constant) and are
marked as estimates. To obtain real energy numbers:

1. Use a bench power supply or a current monitor (e.g. INA226 on shared ground).
2. Measure active current and sleep current.
3. Multiply by the per-phase durations reported by `power_mgmt.c`.

Add these real measurements under `results/hardware/energy.csv` with the exact
instrumentation used; label them separately from simulation results.

### Sleep modes

- **Light sleep** (`power_sleep`): retains state, measurable duration, used for
  development and for measuring real sleep time.
- **Deep sleep** (`power_deep_sleep`): lowest power, non-returning; the device
  boots from scratch on timer wake.

The power-management layer is decoupled from the adaptive scheduler so that
alternative power policies (e.g. always-deep-sleep vs. keep-wifi-connected) can
be compared without changing the sampling policy.