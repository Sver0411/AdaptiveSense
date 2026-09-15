# Third-party notices

AdaptiveSense itself is MIT licensed (see [LICENSE](LICENSE)). It contains code
derived from a third-party project, whose licence is reproduced in full below.

---

## Bosch Sensortec BME280 Sensor API

**Used in:** `firmware/main/bme280_math.c`

**Original project:** <https://github.com/BoschSensortec/BME280_SensorAPI>
(`bme280.c`, functions `compensate_temperature`, `compensate_pressure`,
`compensate_humidity`, and the calibration-field layout used by
`parse_humidity_calib_data`)

**Copyright:** Copyright (c) 2020 Bosch Sensortec GmbH. All rights reserved.

**Licence:** BSD-3-Clause

### What was taken

The three compensation equations and their physical-range clamps are reproduced
from the vendor driver, together with the register-to-calibration-field mapping
(including the `dig_H4` / `dig_H5` nibble assembly, which is not the grouping a
plain reading of the datasheet memory map suggests). They are kept in the same
double-precision form as upstream rather than re-derived, so that they can be
diffed against the original.

AdaptiveSense adds around them: the ESP-IDF I2C transport
(`firmware/main/sensor.c`), the forced-mode measurement sequence, the
configuration-driven policy, and the host-side tests.

### Licence text

```
Copyright (c) 2020 Bosch Sensortec GmbH. All rights reserved.

BSD-3-Clause

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the
    documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
    contributors may be used to endorse or promote products derived from
    this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```

---

## Build and runtime dependencies

These are used but not vendored into this repository; their licences apply to
the copies you install or flash.

| dependency | used for | licence |
|------------|----------|---------|
| ESP-IDF (Espressif) | firmware framework, FreeRTOS, esp-mqtt, I2C driver | Apache-2.0 |
| numpy, pandas, matplotlib, PyYAML, pytest | simulation, analysis, tests | BSD-3-Clause / MIT / PSF-style |
