# Power management

The node duty cycle is:

```
Wake -> Read sensor -> Evaluate change -> Decide upload
     -> Determine next interval -> Sleep
```

`firmware/main/power_mgmt.h` defines four life-cycle phases used for logging and
later energy accounting:

| phase      | meaning                                       |
|------------|-----------------------------------------------|
| `PM_ACTIVE`   | general active compute (radio may be on)      |
| `PM_SAMPLE`   | sensor read window (short)                    |
| `PM_TRANSMIT` | Wi-Fi/MQTT transmission window (short)        |
| `PM_SLEEP`    | asleep until the next scheduled sample        |

Two sleep back-ends are provided and are independent of the adaptive scheduler:

- `power_sleep(duration)` — **light sleep**. Memory and peripherals are
  retained; the call returns the actual elapsed time (in seconds) measured via
  `esp_timer`. This is the default used in `main.c` because the node must resume
  and continue its loop. The measured sleep time can feed an energy proxy.
- `power_deep_sleep(duration_us)` — **deep sleep**. Lowest power; does **not**
  return. On the next timer wake the chip boots from scratch. This is exposed
  separately so a different power policy can be applied without touching the
  scheduling code.

Both functions stop Wi-Fi before sleeping so the radio is off during sleep.

> The values you use for active current, transmission current and sleep current
> define the true energy per cycle. Those are measurements you must acquire on
> hardware; the repo currently reports only an `energy proxy` based on the
> number of uploads (see `docs/methodology.md`).