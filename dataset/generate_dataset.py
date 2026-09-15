"""Generate synthetic 1 Hz ground-truth datasets for the AdaptiveSense study.

These datasets are **simulated ground truth** produced by a parameterised
generator seeded for reproducibility. They exist so the offline replay
simulator, tests and analysis can run deterministically before a physical
ESP32 node is connected. They are *not* presented as real sensor measurements.

Three scenarios are generated:

  * ``scenario_a_stable.csv``  – long run, essentially stable, no events.
  * ``scenario_b_sudden.csv``  – stable baseline, one abrupt +5 deg C change
    (e.g. a door/tumbler of hot water), hold, then slow recovery.
  * ``scenario_c_mixed.csv``   – realistic 120 min workload mixing stable,
    small-change, sudden-change and recovery segments.

Usage::

    python dataset/generate_dataset.py
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
SEED = 42

FIELDS = ["timestamp", "temperature", "humidity", "pressure", "light"]


class Sim:
    """Deterministic pseudo-sensor with correlated channels."""

    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)

    def noise(self, sigma: float) -> float:
        # Box-Muller -> gaussian noise with the given std
        u = max(1e-12, self.rng.random())
        v = self.rng.random()
        return sigma * math.sqrt(-2.0 * math.log(u)) * math.cos(2 * math.pi * v)

    def light(self, t: float) -> float:
        # gentle indoor (always-lit lab) illumination: no abrupt on/off flips
        # that would inject spurious events unrelated to the studied signals
        return 320.0 + 4.0 * math.sin(2 * math.pi * t / 7200.0) + self.noise(3.0)


def write_csv(path: Path, rows) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def gen_scenario_a() -> list[dict]:
    """~2 h nearly-stable run."""
    sim = Sim()
    rows = []
    base = 24.0
    pressure = 1012.4
    for t in range(7200):
        drift = 0.002 * math.sin(2 * math.pi * t / 7200.0)
        temp = base + drift + sim.noise(0.08)
        # humidity anticorrelates mildly with temperature
        hum = 45.0 - 0.3 * (temp - base) + sim.noise(0.4)
        rows.append({
            "timestamp": float(t),
            "temperature": round(temp, 2),
            "humidity": round(hum, 2),
            "pressure": round(pressure + sim.noise(0.15), 2),
            "light": round(sim.light(t), 1),
        })
    return rows


def gen_scenario_b() -> list[dict]:
    """30 min with one abrupt change and recovery.

    The event onset is deliberately placed OFF the common sampling grids
    (5/10/20/40/60 s) so detection latency is a meaningful, non-zero quantity.
    """
    sim = Sim()
    rows = []
    base = 24.0
    pressure = 1012.4
    onset = 632  # sudden change begins here (not aligned to any fixed grid)
    for t in range(1800):
        if t < onset - 6:
            temp = base + sim.noise(0.08)
        elif t < onset:  # brief near-plateau before the jump (avoids grid edge)
            temp = base + sim.noise(0.08)
        elif t < onset + 6:
            temp = base + 5.0 * ((t - onset) / 6.0) + sim.noise(0.1)  # ramp up
        elif t < 1210:
            temp = base + 5.0 + sim.noise(0.1)  # hold hot
        elif t < 1500:
            frac = (t - 1210) / 290.0
            temp = base + 5.0 * (1.0 - frac) + sim.noise(0.1)  # recover
        else:
            temp = base + sim.noise(0.08)
        hum = 45.0 - 4.5 * (temp - base) + sim.noise(0.4)
        rows.append({
            "timestamp": float(t),
            "temperature": round(temp, 2),
            "humidity": round(hum, 2),
            "pressure": round(pressure + sim.noise(0.15), 2),
            "light": round(sim.light(t), 1),
        })
    return rows


def gen_scenario_c() -> list[dict]:
    """2 h mixed workload: stable / small-change / sudden / recovery."""
    sim = Sim()
    rows = []
    base = 24.0
    pressure = 1012.4
    T_SUDDEN = 3600  # sudden event at the midpoint
    for t in range(7200):
        temp = base
        if 0 <= t < 1500:                # stable
            temp = base + sim.noise(0.08)
        elif 1500 <= t < 2400:           # small change: low-amplitude oscillation
            osc = 0.9 * math.sin(2 * math.pi * (t - 1500) / 180.0)
            temp = base + osc + sim.noise(0.08)
        elif 2400 <= t < 3600:           # recovery/stabilise
            temp = base + sim.noise(0.1)
        elif t < T_SUDDEN + 6:           # sudden step
            temp = base + 4.5 * ((t - T_SUDDEN) / 6.0) + sim.noise(0.1)
        elif t < 4620:                   # hold
            temp = base + 4.5 + sim.noise(0.1)
        elif t < 5520:                   # decay
            frac = (t - 4620) / 900.0
            temp = base + 4.5 * (1.0 - frac) + sim.noise(0.1)
        elif t < 6600:                   # slow warm drift
            temp = base + 1.2 * ((t - 5520) / 1080.0) + sim.noise(0.08)
        else:                            # final stable
            temp = base + 1.2 + sim.noise(0.1)
        hum = 45.0 - 3.6 * (temp - base) + sim.noise(0.4)
        rows.append({
            "timestamp": float(t),
            "temperature": round(temp, 2),
            "humidity": round(hum, 2),
            "pressure": round(pressure + sim.noise(0.15), 2),
            "light": round(sim.light(t), 1),
        })
    return rows


if __name__ == "__main__":
    write_csv(RAW / "scenario_a_stable.csv", gen_scenario_a())
    write_csv(RAW / "scenario_b_sudden.csv", gen_scenario_b())
    write_csv(RAW / "scenario_c_mixed.csv", gen_scenario_c())
    print(f"Wrote 3 ground-truth scenarios to {RAW}")
    for p in sorted(RAW.glob("*.csv")):
        print(f"  {p.name}: {sum(1 for _ in open(p)) - 1} rows")