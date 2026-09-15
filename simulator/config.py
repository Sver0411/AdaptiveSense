"""Load the central experiment configuration.

All scripts resolve the repository root relative to their own location so that
no absolute paths are required and the project runs from any checkout.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# repository root: this file lives in <root>/simulator/
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "experiments" / "experiment_config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    """Load the YAML experiment configuration."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    samp = cfg["sampling"]
    if not (samp["min_interval"] <= samp["default_interval"] <= samp["max_interval"]):
        raise ValueError(
            "sampling intervals must satisfy min <= default <= max"
        )