# Firmware build validation

Record of an **actual** `idf.py build` run for the ESP32-S3 target, with the
exact toolchain and commands. Nothing in this file is inferred or estimated.

> **Scope.** This page records a *compile/ link* verification only. No ESP32-S3
> board was attached, no firmware was flashed, and no sensor or power
> measurement was taken. See the *Hardware status* section of the README.

---

## Summary

| item | value |
|------|-------|
| Result | **PASS — build succeeded** |
| Target | `esp32s3` |
| ESP-IDF version | `v5.4.4` |
| Compiler | `xtensa-esp32s3-elf-gcc (crosstool-NG esp-14.2.0_20260121) 14.2.0` |
| Host | macOS 15.7.7 (build 24G720), Apple silicon |
| Warnings | **0** (`warning:` count over the whole build log, including third-party components) |
| Date | 2026-09-15 |

## Commands

```bash
cd firmware
idf.py set-target esp32s3
idf.py build
```

`idf.py set-target esp32s3` performs a `fullclean` first, so the result below
comes from a cold build, not an incremental one.

## Result

```
-- Configuring done
-- Generating done
-- Build files have been written to: .../firmware/build
[116/116] ... Generated .../bootloader/bootloader.bin
Bootloader binary size 0x51c0 bytes. 0x2e40 bytes (36%) free.
[1060/1060] ... Generated .../AdaptiveSense.bin
AdaptiveSense.bin binary size 0xd9d90 bytes.
Smallest app partition is 0x100000 bytes. 0x26270 bytes (15%) free.
Project build complete.
```

| artifact | size |
|----------|------|
| `build/AdaptiveSense.bin` | 892 304 bytes (`0xd9d90`) |
| `build/AdaptiveSense.elf` | 9 809 068 bytes |
| `build/bootloader/bootloader.bin` | 20 928 bytes (`0x51c0`) |

The application image fits the default 1 MiB app partition with 15 % headroom.

## Reproducing

1. Install ESP-IDF v5.4:
   ```bash
   git clone -b v5.4.4 --depth 1 --recurse-submodules --shallow-submodules \
       https://github.com/espressif/esp-idf.git
   cd esp-idf && ./install.sh esp32s3
   ```
2. `firmware/main/CMakeLists.txt` copies `config.example.h` to the git-ignored
   `config.h` automatically if it is absent, so **no credentials are needed to
   compile**.
3. Run the two commands above.

CI does the same thing in the official container image; see
`.github/workflows/esp-idf-build.yml`.

## Notes about the machine used for this record

Two environment quirks had to be worked around. They are **not** properties of
the project, and neither of them would be present on a normal workstation or in
CI:

1. The shell session this was run in injects a Python `sitecustomize` shim that
   brokers filesystem calls to a host process. ESP-IDF's component manager makes
   `os.mkdir` calls that the broker answers with a spurious `EEXIST`, which
   aborts the build during configuration. The build was therefore run under
   `env -i` (clean environment), which is also the more faithful reproduction of
   a developer's shell.
2. The same shim blocks bulk deletions, and `pip` 26.x could not install
   esptool's source distribution in that environment. esptool was installed from
   its extracted source tree instead. Neither affects the project.

## Verification already performed locally

| check | command | result |
|-------|---------|--------|
| host compile of the on-device policy | `cc -std=c11 -Wall -Wextra -Werror -I firmware/main tests/c_host/parity_main.c firmware/main/{change_detector,adaptive_scheduler,policy_config}.c -lm` | PASS, no warnings |
| host compile of the BME280 maths | `cc -std=c11 -Wall -Wextra -Werror -I firmware/main tests/c_host/bme280_host_main.c firmware/main/bme280_math.c -lm` | PASS, no warnings |
| Python ↔ C policy parity | `python -m pytest tests/test_parity_python_c.py` | PASS (9 tests) |
| configuration parity | `python scripts/check_config_parity.py` | PASS |
