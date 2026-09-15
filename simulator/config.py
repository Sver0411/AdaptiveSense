"""Load and validate the central experiment configuration.

All scripts resolve the repository root relative to their own location so that
no absolute paths are required and the project runs from any checkout.

Validation is deliberately strict: it rejects configurations that would make the
experiment incoherent (a ladder outside the interval bounds, a ROC window larger
than the variety window, a STABLE ladder that shortens, an ACTIVE ladder that
lengthens, ...). Several of these were live defects in v0.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from .scoring import AnalyzerConfig, ChannelConfig

# repository root: this file lives in <root>/simulator/
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "experiments" / "experiment_config.yaml"

STATES = ("STABLE", "ACTIVE", "ALERT")


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    """Load and validate the YAML experiment configuration."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    samp = cfg["sampling"]
    lo = float(samp["min_interval"])
    default = float(samp["default_interval"])
    hi = float(samp["max_interval"])
    if not (lo <= default <= hi):
        raise ValueError("sampling intervals must satisfy min <= default <= max")
    if lo <= 0:
        raise ValueError("sampling.min_interval must be positive")

    a = cfg["adaptive"]
    an = a["analyzer"]
    variety = float(an["variety_window_s"])
    roc = float(an["roc_window_s"])
    if roc <= 0 or variety <= 0:
        raise ValueError("analyzer windows must be positive")
    if roc > variety:
        raise ValueError(
            "adaptive.analyzer.roc_window_s must not exceed variety_window_s "
            "(the ROC window is a subset of the retained history)"
        )
    if float(an["baseline_tau_s"]) <= 0:
        raise ValueError("adaptive.analyzer.baseline_tau_s must be positive")

    stable = float(a["stable_threshold"])
    active = float(a["active_threshold"])
    if not (0 < stable < active):
        raise ValueError("require 0 < stable_threshold < active_threshold")
    hyst = float(a["hysteresis_fraction"])
    if not (0.0 <= hyst < 1.0):
        raise ValueError("hysteresis_fraction must be in [0, 1)")

    if float(a["event_min_duration_s"]) < 0:
        raise ValueError("event_min_duration_s must not be negative")

    # --- ladders ---------------------------------------------------------
    for state in STATES:
        ladder = [float(x) for x in a["ladders"][state.lower()]]
        if not ladder:
            raise ValueError(f"ladder for {state} is empty")
        for v in ladder:
            if not (lo - 1e-9 <= v <= hi + 1e-9):
                raise ValueError(
                    f"ladder value {v} for {state} is outside "
                    f"[min_interval={lo}, max_interval={hi}]"
                )

    stable_ladder = [float(x) for x in a["ladders"]["stable"]]
    active_ladder = [float(x) for x in a["ladders"]["active"]]
    alert_ladder = [float(x) for x in a["ladders"]["alert"]]

    # STABLE must back off (intervals grow or stay equal), ACTIVE must speed up
    # (intervals shrink or stay equal). v0.1's active: [60, 30, 15, 5] passed the
    # monotonicity check only by accident of direction; the real defect was the
    # starting rung, which is checked below.
    if any(b < a_ for a_, b in zip(stable_ladder, stable_ladder[1:])):
        raise ValueError("adaptive.ladders.stable must be non-decreasing")
    if any(b > a_ for a_, b in zip(active_ladder, active_ladder[1:])):
        raise ValueError("adaptive.ladders.active must be non-increasing")
    if stable_ladder[0] < default - 1e-9:
        raise ValueError(
            "the first STABLE rung must not be shorter than sampling.default_interval"
        )
    if active_ladder[0] >= stable_ladder[-1]:
        raise ValueError(
            "the first ACTIVE rung must be shorter than the longest STABLE rung, "
            "otherwise entering ACTIVE would slow the node down"
        )
    if min(alert_ladder) > min(active_ladder):
        raise ValueError("the ALERT ladder must not be slower than the ACTIVE ladder")

    conf = a["ladder_confirmations"]
    for state in STATES:
        # ALERT has a single rung, so a confirmation count is meaningless there
        # and may be omitted from the configuration.
        if int(conf.get(state.lower(), 1)) < 1:
            raise ValueError(f"ladder_confirmations.{state.lower()} must be >= 1")

    # --- channels / upload ----------------------------------------------
    if not any(bool(ch["use"]) for ch in a["channels"].values()):
        raise ValueError("at least one channel must be enabled")
    for name, ch in a["channels"].items():
        if float(ch["noise_floor"]) <= 0:
            raise ValueError(f"channel {name}: noise_floor must be positive")

    up = a["upload"]
    if float(up["heartbeat_s"]) < 0:
        raise ValueError("upload.heartbeat_s must not be negative")
    if float(up["delta_threshold"]) < 0:
        raise ValueError("upload.delta_threshold must not be negative")

    # --- evaluation ------------------------------------------------------
    ev = cfg.get("evaluation", {})
    if float(ev.get("event_match_tolerance_s", 0.0)) < 0:
        raise ValueError("evaluation.event_match_tolerance_s must not be negative")
    if float(ev.get("gt_label_min_duration_s", 0.0)) <= 0:
        raise ValueError("evaluation.gt_label_min_duration_s must be positive")
    for name, dev in ev.get("gt_label_min_deviation", {}).items():
        if float(dev) <= 0:
            raise ValueError(f"evaluation.gt_label_min_deviation.{name} must be positive")


def ladder(cfg: dict, state: str) -> List[float]:
    return [float(x) for x in cfg["adaptive"]["ladders"][state.lower()]]


def confirmations(cfg: dict, state: str) -> int:
    """Rungs advance after this many consecutive evaluations (spec section 7).

    ALERT is a single-rung ladder, so it defaults to 1 when not configured.
    """
    return int(cfg["adaptive"]["ladder_confirmations"].get(state.lower(), 1))


def analyzer_config(cfg: dict) -> AnalyzerConfig:
    """Build the normative analyzer configuration (spec sections 1-5)."""
    a = cfg["adaptive"]
    an = a["analyzer"]
    channels = tuple(
        ChannelConfig(
            name=name,
            noise_floor=max(1e-9, float(ch["noise_floor"])),
            use=bool(ch["use"]),
        )
        for name, ch in a["channels"].items()
    )
    return AnalyzerConfig(
        channels=channels,
        variety_window_s=float(an["variety_window_s"]),
        roc_window_s=float(an["roc_window_s"]),
        baseline_tau_s=float(an["baseline_tau_s"]),
        event_threshold=float(a["event_threshold"]),
        event_min_duration_s=float(a["event_min_duration_s"]),
    )


def noise_floors(cfg: dict) -> Dict[str, float]:
    return {
        name: float(ch["noise_floor"])
        for name, ch in cfg["adaptive"]["channels"].items()
    }
