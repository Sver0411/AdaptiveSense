"""Generate synthetic datasets **and independent ground-truth event labels**.

Two artefacts are produced per scenario:

* ``dataset/raw/<scenario>.csv``    — the 1 Hz sensor signal (baseline + driven
  component + measurement noise),
* ``dataset/labels/<scenario>_events.csv`` — the intervals during which the
  generator deliberately drove a channel away from its baseline.

The labels are derived from the generator's own **noise-free driven signal**
using an absolute, per-channel rule from `experiments/experiment_config.yaml`
(``evaluation.gt_label_min_deviation`` / ``gt_label_min_duration_s``). They are
completely independent of the AdaptiveSense change score: the policy cannot
influence what counts as an event. See `docs/change_score_spec.md` section 10.

These datasets are **simulated**, produced by a seeded generator. They are not
presented as measurements.

Scenarios
---------
| file | duration | injected content | designed to test |
|------|----------|------------------|------------------|
| A stable        | 2 h    | nothing                          | overhead when nothing happens |
| B sudden        | 30 min | one abrupt +5 degC step          | step detection + latency |
| C mixed         | 2 h    | sub-threshold wobble, step up, step down | realistic mixed workload |
| D repeated      | 1 h    | 6 irregular steps + 2 light bursts | repeated detection, second modality |
| E short         | 30 min | one 26 s spike after a long quiet period | a short event missed by a long interval |
| F noisy stable  | 20 min | nothing, noise ~7x the configured floor | false positives under a mis-parameterised floor |
| G slow drift    | 1 h    | +3 degC over 30 min, hold, return | behaviour on slow (non-abrupt) change |

Label counts
------------
One injected physical disturbance can move more than one channel: the generator
couples humidity to temperature, so most temperature disturbances also produce a
humidity disturbance. The benchmark is therefore described in two units, and the
program prints both:

  * **physical disturbances** — maximal intervals in which the generator drives
    *any* channel beyond its labelling threshold (`count_disturbances`)
  * **channel-level labels** — one per (channel, interval) pair, which is what the
    evaluation matches against (`label_events`)

Do not quote one number as the other.

Usage::

    python dataset/generate_dataset.py
"""

from __future__ import annotations

import csv
import math
import random
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulator.config import load_config  # noqa: E402
from simulator.events import Event, write_labels  # noqa: E402

RAW = Path(__file__).resolve().parent / "raw"
LABELS = Path(__file__).resolve().parent / "labels"
SEED = 42

FIELDS = ["timestamp", "temperature", "humidity", "pressure", "light"]
CHANNELS = ["temperature", "humidity", "pressure", "light"]

BASELINE = {"temperature": 24.0, "humidity": 45.0, "pressure": 1012.4, "light": 320.0}


# ---------------------------------------------------------------------- #
# deterministic pseudo-sensor
# ---------------------------------------------------------------------- #
class Noise:
    """Seeded Gaussian noise (Box-Muller)."""

    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)

    def gauss(self, sigma: float) -> float:
        u = max(1e-12, self.rng.random())
        v = self.rng.random()
        return sigma * math.sqrt(-2.0 * math.log(u)) * math.cos(2 * math.pi * v)


def ramp(t: float, t0: float, t1: float, a: float, b: float) -> float:
    """Linear interpolation from `a` at `t0` to `b` at `t1`, clamped outside."""
    if t <= t0:
        return a
    if t >= t1:
        return b
    return a + (b - a) * (t - t0) / (t1 - t0)


def pulse(t: float, t0: float, up: float, hold: float, down: float, peak: float) -> float:
    """Trapezoidal pulse: rise `up` s, hold `hold` s, fall `down` s."""
    if t < t0:
        return 0.0
    if t < t0 + up:
        return ramp(t, t0, t0 + up, 0.0, peak)
    if t < t0 + up + hold:
        return peak
    if t < t0 + up + hold + down:
        return ramp(t, t0 + up + hold, t0 + up + hold + down, peak, 0.0)
    return 0.0


# ---------------------------------------------------------------------- #
# scenario definition
# ---------------------------------------------------------------------- #
@dataclass
class Scenario:
    """One synthetic scenario.

    `driven_temp` returns the *driven* temperature offset at time `t` (noise
    free). Humidity is coupled to it by `hum_coupling`; `driven_light` adds an
    independent light disturbance. Everything else stays on its baseline.
    """

    name: str
    duration_s: int
    noise_sigma: Dict[str, float]
    driven_temp: Callable[[float], float]
    hum_coupling: float = -4.5
    driven_light: Callable[[float], float] = lambda t: 0.0
    description: str = ""


def build(spec: Scenario) -> Tuple[List[Dict[str, float]], Dict[str, List[float]]]:
    """Return (rows, driven) for a scenario.

    `driven` holds the noise-free offset from the baseline for each channel at
    every second; it is what the labelling rule is applied to.
    """
    # NOTE: a deterministic per-scenario seed. `hash()` is deliberately NOT
    # used here because Python randomises string hashing per process, which
    # would make the generated dataset non-reproducible.
    rng = Noise(SEED + zlib.crc32(spec.name.encode("utf-8")) % 1000)
    rows: List[Dict[str, float]] = []
    driven: Dict[str, List[float]] = {c: [] for c in CHANNELS}

    for t in range(spec.duration_s):
        d_temp = spec.driven_temp(float(t))
        d_hum = spec.hum_coupling * d_temp
        d_press = 0.0
        d_light = spec.driven_light(float(t))
        dev = {
            "temperature": d_temp,
            "humidity": d_hum,
            "pressure": d_press,
            "light": d_light,
        }
        for c in CHANNELS:
            driven[c].append(dev[c])

        rows.append(
            {
                "timestamp": float(t),
                "temperature": round(
                    BASELINE["temperature"] + d_temp + rng.gauss(spec.noise_sigma["temperature"]), 2
                ),
                "humidity": round(
                    BASELINE["humidity"] + d_hum + rng.gauss(spec.noise_sigma["humidity"]), 2
                ),
                "pressure": round(
                    BASELINE["pressure"] + rng.gauss(spec.noise_sigma["pressure"]), 2
                ),
                "light": round(
                    BASELINE["light"] + d_light + rng.gauss(spec.noise_sigma["light"]), 1
                ),
            }
        )
    return rows, driven


# ---------------------------------------------------------------------- #
# independent labelling rule
# ---------------------------------------------------------------------- #
def classify(driven_ch: Sequence[float], i0: int, i1: int, peak: float) -> str:
    """Mechanical event type: abrupt if the peak is reached quickly."""
    max_step = 0.0
    for i in range(max(1, i0), i1 + 1):
        max_step = max(max_step, abs(driven_ch[i] - driven_ch[i - 1]))
    if max_step <= 0.0:
        return "sustained_change"
    rise_time_s = peak / max_step
    return "sudden_change" if rise_time_s <= 30.0 else "sustained_change"


def label_events(driven: Dict[str, List[float]], cfg: dict) -> List[Event]:
    """Apply the absolute labelling rule to the noise-free driven signal."""
    ev = cfg["evaluation"]
    min_dur = float(ev["gt_label_min_duration_s"])
    thresholds = {k: float(v) for k, v in ev["gt_label_min_deviation"].items()}

    events: List[Event] = []
    for channel in CHANNELS:
        thr = thresholds.get(channel)
        series = driven[channel]
        if thr is None or not series:
            continue
        above = [abs(v) >= thr for v in series]
        i = 0
        n = len(series)
        while i < n:
            if not above[i]:
                i += 1
                continue
            i0 = i
            while i < n and above[i]:
                i += 1
            i1 = i - 1
            duration = float(i1 - i0)  # seconds, 1 Hz
            if duration < min_dur:
                continue
            peak = max(abs(series[j]) for j in range(i0, i1 + 1))
            events.append(
                Event(
                    channel=channel,
                    start_s=float(i0),
                    end_s=float(i1),
                    event_type=classify(series, i0, i1, peak),
                )
            )
    events.sort(key=lambda e: (e.start_s, e.channel))
    return events


def count_disturbances(driven: Dict[str, List[float]], cfg: dict) -> int:
    """Number of injected *physical* disturbances, not channel labels.

    A disturbance is a maximal interval during which the generator drives **any**
    channel beyond that channel's labelling threshold; channels that move together
    (temperature and its coupled humidity) count as one disturbance. The same
    minimum-duration filter as `label_events` is applied, so a blip that produces
    no label also produces no disturbance.

    This is the smaller of the two units the benchmark can be described in, and it
    is the one that describes what was physically injected. See the module
    docstring.
    """
    ev = cfg["evaluation"]
    min_dur = float(ev["gt_label_min_duration_s"])
    thresholds = {k: float(v) for k, v in ev["gt_label_min_deviation"].items()}

    length = len(next(iter(driven.values()))) if driven else 0
    above = [False] * length
    for channel, threshold in thresholds.items():
        series = driven.get(channel)
        if not series:
            continue
        for i, value in enumerate(series):
            if abs(value) >= threshold:
                above[i] = True

    count = 0
    i = 0
    while i < length:
        if not above[i]:
            i += 1
            continue
        start = i
        while i < length and above[i]:
            i += 1
        if float(i - 1 - start) >= min_dur:
            count += 1
    return count


# ---------------------------------------------------------------------- #
# the seven scenarios
# ---------------------------------------------------------------------- #
def scenario_a() -> Scenario:
    return Scenario(
        name="scenario_a_stable",
        duration_s=7200,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=lambda t: 0.002 * math.sin(2 * math.pi * t / 7200.0),
        hum_coupling=-0.3,
        driven_light=lambda t: 0.0,
        description="2 h essentially stable run, no injected disturbance",
    )


def scenario_b() -> Scenario:
    onset = 632.0
    hold_end = 1210.0
    recover_end = 1500.0

    def temp(t: float) -> float:
        if t < onset:
            return 0.0
        if t < onset + 6:
            return ramp(t, onset, onset + 6, 0.0, 5.0)
        if t < hold_end:
            return 5.0
        if t < recover_end:
            return ramp(t, hold_end, recover_end, 5.0, 0.0)
        return 0.0

    return Scenario(
        name="scenario_b_sudden",
        duration_s=1800,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=temp,
        hum_coupling=-4.5,
        description="one abrupt +5 degC step with slow recovery, onset off any sampling grid",
    )


def scenario_c() -> Scenario:
    T1 = 3300.0          # sudden step up
    H1, D1 = 4200.0, 5100.0
    T2 = 5940.0          # sudden step down
    H2, R2 = 6600.0, 7200.0

    def temp(t: float) -> float:
        if t < 1500:
            return 0.0
        if t < 2400:      # sub-threshold wobble: a real change that is NOT an event
            return 0.9 * math.sin(2 * math.pi * (t - 1500) / 180.0)
        if t < T1:
            return 0.0
        if t < T1 + 6:
            return ramp(t, T1, T1 + 6, 0.0, 4.5)
        if t < H1:
            return 4.5
        if t < D1:
            return ramp(t, H1, D1, 4.5, 0.0)
        if t < T2:
            return 0.0
        if t < T2 + 6:
            return ramp(t, T2, T2 + 6, 0.0, -2.5)
        if t < H2:
            return -2.5
        if t < R2:
            return ramp(t, H2, R2, -2.5, 0.0)
        return 0.0

    return Scenario(
        name="scenario_c_mixed",
        duration_s=7200,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=temp,
        hum_coupling=-3.6,
        description="mixed workload: wobble, step up with decay, step down and return",
    )


def scenario_d() -> Scenario:
    # irregular onsets, varying amplitude and hold time
    events = [
        (240.0, 5.5, 150.0),
        (780.0, 4.0, 90.0),
        (1200.0, 5.0, 120.0),
        (1860.0, 4.5, 100.0),
        (2400.0, 5.2, 140.0),
        (3060.0, 4.2, 80.0),
    ]

    def temp(t: float) -> float:
        value = 0.0
        for onset, amplitude, hold in events:
            value += pulse(t, onset, up=6.0, hold=hold, down=60.0, peak=amplitude)
        return value

    def light(t: float) -> float:
        value = 0.0
        value += pulse(t, 540.0, up=1.0, hold=80.0, down=1.0, peak=350.0)
        value += pulse(t, 2700.0, up=1.0, hold=60.0, down=1.0, peak=300.0)
        return value

    return Scenario(
        name="scenario_d_repeated",
        duration_s=3600,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=temp,
        hum_coupling=-4.0,
        driven_light=light,
        description="6 irregular temperature steps plus 2 light bursts",
    )


def scenario_e() -> Scenario:
    def temp(t: float) -> float:
        # A short spike (26 s) after a long quiet stretch, so the policy has
        # backed off to its longest interval. At 1 Hz or a 5 s interval the
        # spike is observable; at a 60 s interval it can fall entirely between
        # two samples. This is the "no policy can react before a change has
        # been sampled" case discussed in the README limitations.
        return pulse(t, 1560.0, up=1.0, hold=24.0, down=1.0, peak=6.0)

    return Scenario(
        name="scenario_e_short_event",
        duration_s=1800,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=temp,
        hum_coupling=-4.0,
        description="one short (26 s) spike after 26 min of stability",
    )


def scenario_f() -> Scenario:
    return Scenario(
        name="scenario_f_noisy_stable",
        duration_s=1200,
        # noise amplitude far above the configured noise floor (0.15 degC):
        # this is a deliberately mis-parameterised node.
        noise_sigma={"temperature": 1.0, "humidity": 2.5, "pressure": 0.6, "light": 25.0},
        driven_temp=lambda t: 0.0,
        hum_coupling=0.0,
        description="no injected disturbance, noise ~7x the configured temperature floor",
    )


def scenario_g() -> Scenario:
    def temp(t: float) -> float:
        if t < 600:
            return 0.0
        if t < 2400:      # +3.0 degC over 30 min => 0.00167 degC/s
            return ramp(t, 600, 2400, 0.0, 3.0)
        if t < 3000:
            return 3.0
        if t < 3600:
            return ramp(t, 3000, 3600, 3.0, 0.0)
        return 0.0

    return Scenario(
        name="scenario_g_slow_drift",
        duration_s=3600,
        noise_sigma={"temperature": 0.08, "humidity": 0.4, "pressure": 0.15, "light": 3.0},
        driven_temp=temp,
        hum_coupling=-4.0,
        description="slow +3 degC drift over 30 min, hold, then return",
    )


SCENARIOS = [
    scenario_a,
    scenario_b,
    scenario_c,
    scenario_d,
    scenario_e,
    scenario_f,
    scenario_g,
]


# ---------------------------------------------------------------------- #
# io
# ---------------------------------------------------------------------- #
def write_csv(path: Path, rows: Sequence[Dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    cfg = load_config()
    RAW.mkdir(parents=True, exist_ok=True)
    LABELS.mkdir(parents=True, exist_ok=True)

    total_labels = 0
    total_disturbances = 0
    print(f"evaluation.gt_label_min_deviation = {cfg['evaluation']['gt_label_min_deviation']}")
    print(f"evaluation.gt_label_min_duration_s = {cfg['evaluation']['gt_label_min_duration_s']}")
    print()
    print(f"{'scenario':26s} {'rows':>6s} {'disturb':>8s} {'labels':>7s}  channels")
    for factory in SCENARIOS:
        spec = factory()
        rows, driven = build(spec)
        events = label_events(driven, cfg)
        disturbances = count_disturbances(driven, cfg)
        write_csv(RAW / f"{spec.name}.csv", rows)
        write_labels(LABELS / f"{spec.name}_events.csv", events)
        total_labels += len(events)
        total_disturbances += disturbances
        channels = ", ".join(sorted({e.channel for e in events})) or "-"
        print(f"{spec.name:26s} {len(rows):6d} {disturbances:8d} {len(events):7d}  {channels}")
        print(f"{'':26s} {spec.description}")
    print()
    print(f"wrote {len(SCENARIOS)} scenarios to {RAW}")
    print(
        f"wrote {total_labels} channel-level labels to {LABELS}, derived from "
        f"{total_disturbances} injected physical disturbances"
    )
    print(
        "  (one injected disturbance can label several channels: humidity is "
        "coupled to temperature)"
    )


if __name__ == "__main__":
    main()
