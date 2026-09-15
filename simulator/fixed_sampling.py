"""Fixed-rate sampling baselines.

A fixed-rate strategy takes one sample every ``interval`` seconds and uploads
every sample it takes (there is no change-awareness). This provides the
baseline against which AdaptiveSense is compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class FixedSample:
    timestamp: float
    values: Dict[str, float]
    upload: bool = True


def run_fixed_strategy(
    timestamps: List[float],
    rows: List[Dict[str, float]],
    interval: float,
    time_col: str = "timestamp",
    start_time: float = 0.0,
) -> List[FixedSample]:
    """Return the measurement(s) a fixed-rate node would have produced.

    Samples are taken at ``start_time + k * interval``. The taken sample is the
    ground-truth reading whose timestamp is closest to the sample time (nearest
    neighbour within the 1 Hz grid). Every sample is uploaded.
    """
    samples: List[FixedSample] = []
    index = 0
    n = len(timestamps)
    t_sample = start_time
    while t_sample <= timestamps[-1] and index < n:
        # advance to the last reading whose timestamp is not ahead of sample
        while index + 1 < n and timestamps[index + 1] <= t_sample:
            index += 1
        # pick nearest neighbour between index and index+1
        best = index
        if index + 1 < n:
            if (timestamps[index + 1] - t_sample) < (t_sample - timestamps[index]):
                best = index + 1
        values = {k: v for k, v in rows[best].items() if k != time_col}
        samples.append(FixedSample(timestamp=t_sample, values=values))
        t_sample += interval

    # ensure we always return at least the first reading if the interval is
    # larger than the whole trace (edge case: interval > duration)
    if not samples and rows:
        samples.append(FixedSample(timestamp=timestamps[0], values=dict(rows[0])))
    return samples


def strategy_name(interval: float) -> str:
    return f"Fixed-{int(interval)}s"