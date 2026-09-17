#!/usr/bin/env python3
"""Check that the firmware configuration mirrors the experiment configuration.

`experiments/experiment_config.yaml` is the single source of truth for every
tunable value in this project. `firmware/main/config.example.h` mirrors it by
hand, and `firmware/main/policy_config.c` maps those macros onto the runtime
structs. This script fails if any of the three drift apart, so a parameter can
never be changed in one place and silently keep its old value on the device.

It additionally guards the specific structural mistakes found in v0.1:

* a window size hardcoded in a C header instead of coming from `config.h`
  (`CD_VARIETY_WINDOW_S` / `CD_ROC_WINDOW_S`),
* numeric tuning literals inside `policy_config.c`,
* `require(...)` calls in `CMakeLists.txt` (not an ESP-IDF build-system command).

Usage::

    python scripts/check_config_parity.py

Exit code 0 = parity holds, 1 = at least one mismatch (also used by CI).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple, Union

import yaml

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "experiments" / "experiment_config.yaml"
CONFIG_H = ROOT / "firmware" / "main" / "config.example.h"
POLICY_CONFIG_C = ROOT / "firmware" / "main" / "policy_config.c"
DETECTOR_H = ROOT / "firmware" / "main" / "change_detector.h"
MAIN_C = ROOT / "firmware" / "main" / "main.c"
POWER_MGMT_C = ROOT / "firmware" / "main" / "power_mgmt.c"
SENSOR_C = ROOT / "firmware" / "main" / "sensor.c"
SENSOR_BUS_C = ROOT / "firmware" / "main" / "sensor_bus.c"
SENSOR_BME280_C = ROOT / "firmware" / "main" / "sensor_bme280.c"
SENSOR_SHT30_C = ROOT / "firmware" / "main" / "sensor_sht30.c"
SHT30_PROTO_C = ROOT / "firmware" / "main" / "sht30_proto.c"
MAIN_CMAKE = ROOT / "firmware" / "main" / "CMakeLists.txt"
SDKCONFIG_DEFAULTS = ROOT / "firmware" / "sdkconfig.defaults"

# (yaml dotted path, C macro, expected kind)
#   kind: "float"  scalar compared with a relative tolerance
#         "bool"   yaml true/false vs C 1/0
#         "int"    exact integer
#         "list"   { a, b, c } compared element-wise
SPEC: Sequence[Tuple[str, str, str]] = (
    ("sampling.min_interval", "CONFIG_AS_MIN_INTERVAL_S", "float"),
    ("sampling.default_interval", "CONFIG_AS_DEFAULT_INTERVAL_S", "float"),
    ("sampling.max_interval", "CONFIG_AS_MAX_INTERVAL_S", "float"),

    ("adaptive.channels.temperature.noise_floor", "CONFIG_AS_NOISE_FLOOR_TEMP", "float"),
    ("adaptive.channels.humidity.noise_floor", "CONFIG_AS_NOISE_FLOOR_HUM", "float"),
    ("adaptive.channels.pressure.noise_floor", "CONFIG_AS_NOISE_FLOOR_PRESS", "float"),
    ("adaptive.channels.light.noise_floor", "CONFIG_AS_NOISE_FLOOR_LIGHT", "float"),
    ("adaptive.channels.humidity.use", "CONFIG_AS_USE_HUMIDITY", "bool"),
    ("adaptive.channels.pressure.use", "CONFIG_AS_USE_PRESSURE", "bool"),
    ("adaptive.channels.light.use", "CONFIG_AS_USE_LIGHT", "bool"),

    ("adaptive.analyzer.variety_window_s", "CONFIG_AS_VARIETY_WINDOW_S", "float"),
    ("adaptive.analyzer.roc_window_s", "CONFIG_AS_ROC_WINDOW_S", "float"),
    ("adaptive.analyzer.baseline_tau_s", "CONFIG_AS_BASELINE_TAU_S", "float"),

    ("adaptive.stable_threshold", "CONFIG_AS_STABLE_THRESHOLD", "float"),
    ("adaptive.active_threshold", "CONFIG_AS_ACTIVE_THRESHOLD", "float"),
    ("adaptive.hysteresis_fraction", "CONFIG_AS_HYSTERESIS_FRACTION", "float"),

    ("adaptive.ladders.stable", "CONFIG_AS_LADDER_STABLE", "list"),
    ("adaptive.ladders.active", "CONFIG_AS_LADDER_ACTIVE", "list"),
    ("adaptive.ladders.alert", "CONFIG_AS_LADDER_ALERT", "list"),

    ("adaptive.ladder_confirmations.stable", "CONFIG_AS_LADDER_CONFIRM_STABLE", "int"),
    ("adaptive.ladder_confirmations.active", "CONFIG_AS_LADDER_CONFIRM_ACTIVE", "int"),

    ("adaptive.event_threshold", "CONFIG_AS_EVENT_THRESHOLD", "float"),
    ("adaptive.event_min_duration_s", "CONFIG_AS_EVENT_MIN_DURATION_S", "float"),

    ("adaptive.upload.upload_first_sample", "CONFIG_AS_UP_FIRST_SAMPLE", "bool"),
    ("adaptive.upload.on_event", "CONFIG_AS_UP_ON_EVENT", "bool"),
    ("adaptive.upload.on_state_change", "CONFIG_AS_UP_ON_STATE_CHANGE", "bool"),
    ("adaptive.upload.on_interval_change", "CONFIG_AS_UP_ON_INTERVAL_CHANGE", "bool"),
    ("adaptive.upload.heartbeat_s", "CONFIG_AS_UP_HEARTBEAT_S", "float"),
    ("adaptive.upload.delta_threshold", "CONFIG_AS_UP_DELTA_THRESHOLD", "float"),

    # Firmware-only parameters. They have no effect on the simulation, but they
    # live in the same YAML so there is one place where a tunable value exists.
    ("firmware.mqtt_keepalive_s", "CONFIG_AS_MQTT_KEEPALIVE_S", "float"),
    ("firmware.sensor_failures_before_unavailable",
     "CONFIG_AS_SENSOR_FAILURES_BEFORE_UNAVAILABLE", "int"),
)

REL_TOL = 1e-6

Number = Union[int, float]


def strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def strip_hash_comments(text: str) -> str:
    """Drop whole-line `#` comments (sdkconfig.defaults / shell style).

    Needed because a comment in `sdkconfig.defaults` may legitimately mention an
    option name, and a naive substring search would then treat an explanation as
    a setting.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def parse_defines(path: Path) -> Dict[str, str]:
    """Collect `#define NAME value` from a C header (comments removed)."""
    text = strip_c_comments(path.read_text(encoding="utf-8"))
    out: Dict[str, str] = {}
    pattern = re.compile(
        r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*?)\s*$", re.M
    )
    for name, value in pattern.findall(text):
        out[name] = value.split("/*")[0].strip()
    return out


def parse_scalar(raw: str) -> Number:
    cleaned = raw.strip().rstrip("fFuUlL")
    if cleaned.lower().startswith("(bool)"):
        cleaned = cleaned[len("(bool)"):].strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1].strip()
    return float(cleaned) if ("." in cleaned or "e" in cleaned.lower()) else int(cleaned)


def parse_list(raw: str) -> List[Number]:
    body = raw.strip()
    if not (body.startswith("{") and body.endswith("}")):
        raise ValueError(f"not a C array initialiser: {raw!r}")
    items = [x.strip() for x in body[1:-1].split(",") if x.strip()]
    return [parse_scalar(x) for x in items]


def dig(doc: dict, dotted: str):
    node = doc
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def compare(path: str, macro: str, kind: str, yaml_doc: dict, defines: Dict[str, str]):
    if macro not in defines:
        return False, f"{macro} is not defined in {CONFIG_H.name}"
    raw = defines[macro]
    try:
        yaml_value = dig(yaml_doc, path)
    except KeyError:
        return False, f"{path} missing from {YAML_PATH.name}"

    try:
        if kind == "list":
            c_value = parse_list(raw)
            y_value = [parse_scalar(str(v)) for v in yaml_value]
            if len(c_value) != len(y_value):
                return False, f"length {len(c_value)} vs {len(y_value)}"
            for a, b in zip(c_value, y_value):
                if float(a) != float(b):
                    return False, f"{c_value} != {y_value}"
            return True, f"{c_value}"

        c_value = parse_scalar(raw)
        if kind == "bool":
            y_bool = bool(yaml_value)
            if bool(int(c_value)) != y_bool:
                return False, f"{bool(int(c_value))} != {y_bool}"
            return True, str(y_bool)

        if kind == "int":
            if int(c_value) != int(yaml_value):
                return False, f"{int(c_value)} != {int(yaml_value)}"
            return True, str(int(c_value))

        y_num = float(yaml_value)
        c_num = float(c_value)
        if abs(c_num - y_num) > max(REL_TOL, REL_TOL * abs(y_num)):
            return False, f"{c_num} != {y_num}"
        return True, f"{c_num:g}"
    except ValueError as exc:
        return False, f"cannot parse {raw!r} ({exc})"


def structural_checks() -> List[Tuple[str, bool, str]]:
    """The v0.1 mistakes that must not come back."""
    results: List[Tuple[str, bool, str]] = []

    detector_h = strip_c_comments(DETECTOR_H.read_text(encoding="utf-8"))
    for macro in ("CD_VARIETY_WINDOW_S", "CD_ROC_WINDOW_S"):
        # Comments are stripped first: the header documents these macro names on
        # purpose (as the v0.1 mistake that must not come back), so a raw text
        # search would always report a false positive.
        ok = macro not in detector_h
        results.append((
            f"{DETECTOR_H.name} does not define {macro}",
            ok,
            "windows must come from config.h" if not ok else "ok",
        ))

    policy_c = strip_c_comments(POLICY_CONFIG_C.read_text(encoding="utf-8"))
    float_literals = re.findall(r"(?<![\w.])\d+\.\d+f?(?![\w.])", policy_c)
    ok = not float_literals
    results.append((
        f"{POLICY_CONFIG_C.name} has no numeric literals",
        ok,
        f"found {float_literals}" if not ok else "ok",
    ))

    cmake = MAIN_CMAKE.read_text(encoding="utf-8")
    bad = re.findall(r"^\s*require\s*\(", cmake, flags=re.M)
    ok = not bad
    results.append((
        f"{MAIN_CMAKE.name} does not call require()",
        ok,
        "require() is not an ESP-IDF command; use REQUIRES" if not ok else "ok",
    ))

    has_requires = "REQUIRES" in cmake
    results.append((
        f"{MAIN_CMAKE.name} declares dependencies with REQUIRES",
        has_requires,
        "ok" if has_requires else "no REQUIRES clause found",
    ))

    manifest = (MAIN_CMAKE.parent / "idf_component.yml")
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8")
        # A managed `mqtt` component would be a second dependency mechanism.
        managed_mqtt = re.search(r"^\s{2}mqtt\s*:", text, flags=re.M) is not None
        results.append((
            "idf_component.yml does not declare a managed mqtt component",
            not managed_mqtt,
            "two mechanisms for one component" if managed_mqtt else "ok",
        ))

    # --- the sampling loop must not conflate "no read" with "read failed" -----
    #
    # A short-circuit guard used to combine readiness with the read result:
    #
    #     if (!sensor_sup_ready(&s) || sensor_read(&r) != 0) {
    #         sensor_sup_note_read_failure(&s, t);
    #
    # so a cycle that skipped the read because the sensor was unavailable was
    # counted as a failed read, and the failure counters grew once per cycle for
    # the whole outage (reported as 59 failures where 3 reads were attempted).
    # The loop now keeps the three outcomes distinct; this keeps them distinct.
    #
    # Comments are stripped first: the explanation of the defect quotes the very
    # pattern the check forbids.
    main_c = strip_c_comments(MAIN_C.read_text(encoding="utf-8"))

    conflated = re.search(r"!sensor_sup_ready\s*\([^)]*\)\s*\|\|", main_c) is not None
    results.append((
        "main.c does not short-circuit readiness into the read result",
        not conflated,
        "a skipped read would be counted as a failed one" if conflated else "ok",
    ))

    failure_sites = list(re.finditer(r"sensor_sup_note_read_failure\s*\(", main_c))
    single_site = len(failure_sites) == 1
    results.append((
        "main.c counts a read failure from exactly one place",
        single_site,
        f"{len(failure_sites)} call sites" if not single_site else "ok",
    ))

    guarded = False
    if single_site:
        idx = failure_sites[0].start()
        last_read = main_c.rfind("sensor_read(", 0, idx)
        between = main_c[last_read:idx] if last_read != -1 else ""
        guarded = last_read != -1 and "!= 0" in between
    results.append((
        "the counted failure sits inside 'if (sensor_read(...) != 0)'",
        guarded,
        "ok" if guarded else "a read failure is recorded without a read attempt",
    ))

    success_sites = list(re.finditer(r"sensor_sup_note_read_success\s*\(", main_c))
    ordered = (single_site and len(success_sites) == 1
               and success_sites[0].start() > failure_sites[0].start())
    results.append((
        "main.c resets the streak only after the failure branch",
        ordered,
        "ok" if ordered else "the success path is missing or misordered",
    ))

    sleep_ok = "esp_wifi_stop" not in (
        ROOT / "firmware" / "main" / "power_mgmt.c"
    ).read_text(encoding="utf-8")
    results.append((
        "power_mgmt.c does not stop Wi-Fi before sleeping",
        sleep_ok,
        "the radio must survive the sleep" if not sleep_ok else "ok",
    ))

    # --- power management: the sleep mechanism must be the ESP-IDF one -------
    #
    # The firmware is supposed to idle with vTaskDelay() and let the ESP-IDF
    # power manager enter light sleep, so that the Wi-Fi driver's PM locks take
    # part in the decision. Sleeping from the application layer (or leaving PM
    # disabled) silently breaks that contract, so it is checked here rather than
    # trusted.
    power_c = strip_c_comments(POWER_MGMT_C.read_text(encoding="utf-8"))

    manual_sleep = "esp_light_sleep_start" in power_c
    results.append((
        "power_mgmt.c does not call esp_light_sleep_start",
        not manual_sleep,
        "sleep must go through the ESP-IDF power manager" if manual_sleep else "ok",
    ))

    configures_pm = ("esp_pm_configure" in power_c) and ("light_sleep_enable" in power_c)
    results.append((
        "power_mgmt.c configures the PM subsystem (esp_pm_configure + light_sleep_enable)",
        configures_pm,
        "ok" if configures_pm else "automatic light sleep would never be armed",
    ))

    sdkconfig = strip_hash_comments(SDKCONFIG_DEFAULTS.read_text(encoding="utf-8"))
    for option in ("CONFIG_PM_ENABLE=y",
                   "CONFIG_FREERTOS_USE_TICKLESS_IDLE=y",
                   "CONFIG_PM_LIGHT_SLEEP_CALLBACKS=y",
                   "CONFIG_PM_DFS_INIT_AUTO=n"):
        present = re.search(rf"^{re.escape(option)}\s*$", sdkconfig, flags=re.M) is not None
        results.append((
            f"sdkconfig.defaults sets {option}",
            present,
            "ok" if present else "missing",
        ))

    # Tickless idle only exists when PM is enabled, and the light-sleep observer
    # only exists when tickless idle is on. Catching the pair together makes the
    # dependency explicit rather than leaving it to a Kconfig prompt.
    if "CONFIG_PM_ENABLE=y" in sdkconfig and "CONFIG_FREERTOS_USE_TICKLESS_IDLE=y" not in sdkconfig:
        results.append((
            "tickless idle is enabled whenever PM is enabled",
            False,
            "CONFIG_FREERTOS_USE_TICKLESS_IDLE depends on CONFIG_PM_ENABLE",
        ))
    else:
        results.append(("tickless idle is enabled whenever PM is enabled", True, "ok"))

    config_h_text = strip_c_comments(CONFIG_H.read_text(encoding="utf-8"))
    rejects_deep = bool(re.search(
        r"#if\s+CONFIG_AS_SLEEP_MODE\s*==\s*2\s*\n\s*#error", config_h_text))
    results.append((
        "config.example.h rejects deep sleep at compile time",
        rejects_deep,
        "ok" if rejects_deep else "deep sleep would silently reboot the node",
    ))

    keepalive_checked = bool(re.search(
        r"#if\s+CONFIG_AS_MQTT_KEEPALIVE_S\s*<", config_h_text))
    results.append((
        "config.example.h cross-checks the MQTT keepalive against max_interval",
        keepalive_checked,
        "ok" if keepalive_checked else "no compile-time keepalive constraint",
    ))

    pm_in_requires = bool(re.search(r"^\s*esp_pm\s*$", cmake, flags=re.M))
    results.append((
        f"{MAIN_CMAKE.name} REQUIRES esp_pm",
        pm_in_requires,
        "ok" if pm_in_requires else "power_mgmt.c includes esp_pm.h",
    ))

    # --- sensor layer: one shared bus, backends never own one ----------------
    #
    # The SHT30, the BH1750 and the display sit on the same two wires. If a
    # backend created its own bus, a bring-up retry would fail with "I2C bus id(0)
    # has already been acquired" — which is exactly what happened on hardware
    # before the bus was made shared. Checked rather than trusted.
    sensor_c = strip_c_comments(SENSOR_C.read_text(encoding="utf-8"))
    bus_c = strip_c_comments(SENSOR_BUS_C.read_text(encoding="utf-8"))

    creates_bus = "i2c_new_master_bus" in bus_c
    results.append((
        f"{SENSOR_BUS_C.name} creates the shared i2c bus",
        creates_bus,
        "ok" if creates_bus else "nothing would ever create the bus",
    ))

    for path in (SENSOR_C, SENSOR_BME280_C, SENSOR_SHT30_C):
        text = strip_c_comments(path.read_text(encoding="utf-8"))
        owns_bus = "i2c_new_master_bus" in text
        results.append((
            f"{path.name} does not create its own i2c bus",
            not owns_bus,
            "the bus is shared; a second one fails to acquire" if owns_bus else "ok",
        ))

    # The protocol layer must stay free of ESP-IDF so it can be host-tested.
    proto_c = strip_c_comments(SHT30_PROTO_C.read_text(encoding="utf-8"))
    proto_clean = not re.search(r'#\s*include\s*"(esp_|driver/|freertos/)', proto_c)
    results.append((
        f"{SHT30_PROTO_C.name} has no ESP-IDF dependency",
        proto_clean,
        "ok" if proto_clean else "the protocol layer must stay host-testable",
    ))

    # Both backends must be compiled, so neither can rot, and the selection macro
    # must be one of the two documented values.
    for src in ("sensor_bme280.c", "sensor_sht30.c"):
        listed = f'"{src}"' in cmake
        results.append((
            f"{MAIN_CMAKE.name} compiles {src}",
            listed,
            "ok" if listed else "both backends are always built",
        ))

    backend_macro = re.search(r"^#define\s+CONFIG_AS_SENSOR_BACKEND\s+(\d+)",
                              config_h_text, flags=re.M)
    backend_ok = bool(backend_macro) and backend_macro.group(1) in ("1", "2")
    results.append((
        "CONFIG_AS_SENSOR_BACKEND selects a documented backend",
        backend_ok,
        f"found {backend_macro.group(1)}" if backend_macro else "macro missing",
    ))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    args = parser.parse_args()

    with open(YAML_PATH, "r", encoding="utf-8") as fh:
        yaml_doc = yaml.safe_load(fh)
    defines = parse_defines(CONFIG_H)

    failures = 0
    print(f"config parity: {YAML_PATH.relative_to(ROOT)}  <->  "
          f"{CONFIG_H.relative_to(ROOT)}")
    print(f"{'parameter':52s} {'firmware':>18s}  result")
    print("-" * 86)

    for path, macro, kind in SPEC:
        ok, detail = compare(path, macro, kind, yaml_doc, defines)
        if not ok:
            failures += 1
        if not ok or not args.quiet:
            print(f"{path:52s} {detail:>18s}  {'OK' if ok else 'MISMATCH'}")

    print()
    print("structural checks")
    print(f"{'check':62s} result")
    print("-" * 86)
    for name, ok, detail in structural_checks():
        if not ok:
            failures += 1
        if not ok or not args.quiet:
            print(f"{name:62s} {'OK' if ok else 'FAIL: ' + detail}")

    print()
    if failures:
        print(f"FAIL: {failures} item(s) failed config parity")
        return 1
    total = len(SPEC) + len(structural_checks())
    print(f"PASS: all {total} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
