"""Event definitions and ground-truth / detected event computation.

Events are defined ONCE in a neutral, configurable way and applied both to the
full-resolution ground-truth signal (to obtain the ground-truth event mask) and
to the sampled readings (to obtain detected events). The same event threshold
and minimum-duration configuration is used for both, so the ground-truth
definition is never tuned to favour a particular sampling strategy.

An event starts on a channel when its normalized instability score stays above
``event_threshold`` for at least ``event_min_duration_s`` seconds. The score is
computed from the same per-channel noise floors used by the scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class Event:
    """A time-bounded event detected on one channel."""

    channel: str
    start_s: float
    end_s: float


def _channel_scores(values: List[float], times: List[float], noise_floor: float,
                    variety_window: float, baseline_tau: float) -> List[float]:
    """Point-wise normalized instability score per channel (offline).

    Mirrors the live scheduler: deviation is measured against a persistent EMA
    baseline with time constant ``baseline_tau`` so that a single sample after
    a long interval still reflects a real change.
    """
    n = len(values)
    if n == 0:
        return []
    ema = values[0]
    scores: List[float] = [0.0] * n
    ema_refs: List[float] = [values[0]] * n
    for i in range(1, n):
        dt = times[i] - times[i - 1]
        ema_refs[i] = ema
        if dt > 0:
            alpha = dt / (dt + baseline_tau)
            ema = ema + alpha * (values[i] - ema)

    for i in range(n):
        t_i = times[i]
        # neighbours within the recent variety window
        idx = [j for j in range(n) if 0.0 <= (t_i - times[j]) <= variety_window]
        wins = [values[j] for j in idx]
        if len(wins) < 2:
            std = 0.0
        else:
            mean = sum(wins) / len(wins)
            var = sum((v - mean) ** 2 for v in wins) / len(wins)
            std = max(0.0, var) ** 0.5

        dev = abs(values[i] - ema_refs[i])

        roc = 0.0
        for j in idx:
            if j > 0 and times[j] > times[j - 1]:
                delta = abs(values[j] - values[j - 1]) / (times[j] - times[j - 1])
                roc = max(roc, delta)

        scores[i] = max(dev / noise_floor, std / noise_floor, roc / noise_floor)
    return scores


def ground_truth_events(
    rows: List[Dict[str, float]],
    times: List[float],
    cfg: dict,
) -> List[Event]:
    """Compute the ground-truth event mask from the full-resolution dataset."""
    adaptive = cfg["adaptive"]
    var_w = float(adaptive["analyzer"]["variety_window_s"])
    tau = float(adaptive["analyzer"]["baseline_tau_s"])
    ev_th = float(adaptive["event_threshold"])
    dur = float(adaptive["event_min_duration_s"])
    events: List[Event] = []

    for name, ch in adaptive["channels"].items():
        if not ch["use"]:
            continue
        values = [r[name] for r in rows]
        if len(values) < 2:
            continue
        scores = _channel_scores(values, times, float(ch["noise_floor"]), var_w, tau)
        # build sustained-event masks
        active_since: float | None = None
        in_event = False
        for i, t in enumerate(times):
            if scores[i] > ev_th:
                if active_since is None:
                    active_since = t
                elif t - active_since >= dur and not in_event:
                    # event confirmed starting at active_since
                    events.append(Event(channel=name, start_s=active_since, end_s=t))
                    in_event = True
            else:
                active_since = None
                if in_event and events and events[-1].channel == name:
                    events[-1].end_s = times[i - 1]
                in_event = False
        if in_event and events and events[-1].channel == name:
            events[-1].end_s = times[-1]

    events.sort(key=lambda e: e.start_s)
    return events


def event_overlap(e: Event, others: List[Event], tol_s: float = 5.0) -> bool:
    """Whether `e` temporally overlaps any of `others` within tolerance."""
    return any(o.start_s - tol_s <= e.end_s and e.start_s <= o.end_s + tol_s for o in others)