"""Change-score computation — the single Python implementation of
`docs/change_score_spec.md` sections 1-5.

Both the live scheduling policy (:mod:`simulator.adaptive`) and the offline
event detector (:mod:`simulator.events`) call :class:`ChangeAnalyzer`. There is
exactly one implementation of the EMA / STD / ROC score in the Python code base,
so the simulator cannot disagree with itself. v0.1 had two copies that used
different ROC definitions (mean over the variety window vs max over the variety
window).

The same equations are implemented in C in `firmware/main/change_detector.c`.
`tests/test_parity_python_c.py` compiles that C file for the host and compares
the two on a shared fixture.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "ChannelConfig",
    "AnalyzerConfig",
    "ChangeAnalyzer",
]


@dataclass(frozen=True)
class ChannelConfig:
    """Per-channel analyzer configuration."""

    name: str
    noise_floor: float
    use: bool


@dataclass(frozen=True)
class AnalyzerConfig:
    """Analyzer configuration shared by every channel (§1-§5)."""

    channels: Tuple[ChannelConfig, ...]
    variety_window_s: float
    roc_window_s: float
    baseline_tau_s: float
    event_threshold: float
    event_min_duration_s: float

    def enabled(self) -> Tuple[ChannelConfig, ...]:
        return tuple(c for c in self.channels if c.use)

    def channel_names(self) -> Tuple[str, ...]:
        return tuple(c.name for c in self.channels)

    def by_name(self) -> Dict[str, ChannelConfig]:
        return {c.name: c for c in self.channels}


class ChangeAnalyzer:
    """Streaming implementation of the documented change score.

    State is per channel: a time-ordered history (bounded by the variety
    window) and a persistent EMA baseline.
    """

    def __init__(self, config: AnalyzerConfig) -> None:
        self.cfg = config
        self._cfs = config.by_name()
        self._hist: Dict[str, Deque[Tuple[float, float]]] = {
            name: deque() for name in self._cfs
        }
        self._ema: Dict[str, Optional[float]] = {name: None for name in self._cfs}
        self._last_t: Dict[str, Optional[float]] = {name: None for name in self._cfs}
        # event debounce (§5)
        self._potential_start: Optional[float] = None
        self.event_active: bool = False

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _prune(self, name: str, now: float) -> None:
        hist = self._hist[name]
        limit = now - self.cfg.variety_window_s
        while hist and hist[0][0] < limit:
            hist.popleft()

    def _std(self, name: str) -> float:
        """Population std over the variety window (§2)."""
        vals = [v for _, v in self._hist[name]]
        n = len(vals)
        if n < 2:
            return 0.0
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        return math.sqrt(var) if var > 0.0 else 0.0

    def _roc(self, name: str, now: float) -> float:
        """Mean |dx/dt| over the ROC window, each pair counted once (§3)."""
        rows = self._hist[name]
        limit = now - self.cfg.roc_window_s
        terms: List[float] = []
        for (t_prev, v_prev), (t_cur, v_cur) in zip(rows, list(rows)[1:]):
            dt = t_cur - t_prev
            if dt > 0.0 and t_cur >= limit:
                terms.append(abs(v_cur - v_prev) / dt)
        if not terms:
            return 0.0
        return sum(terms) / len(terms)

    def _update_ema(self, name: str, t: float, value: float) -> float:
        """Fold `value` into the baseline; return the reference to deviate from.

        The reference is the baseline BEFORE this sample (§1), which makes the
        deviation a change measure rather than a smoothing residual.
        """
        ema = self._ema[name]
        last_t = self._last_t[name]
        if ema is None:
            self._ema[name] = value
            self._last_t[name] = t
            return value
        dt = (t - last_t) if (last_t is not None and t > last_t) else 0.0
        alpha = dt / (dt + self.cfg.baseline_tau_s) if dt > 0.0 else 0.0
        ref = ema
        self._ema[name] = ema + alpha * (value - ema)
        self._last_t[name] = t
        return ref

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def update(
        self, t: float, values: Mapping[str, float]
    ) -> Tuple[float, Dict[str, float], bool]:
        """Feed one measurement vector.

        Returns ``(overall_score, {channel: channel_score}, event_active)``.
        ``overall_score`` is the max over participating channels (§4).
        """
        channel_scores: Dict[str, float] = {}
        overall = 0.0

        for cf in self.cfg.channels:
            name = cf.name
            if not cf.use or name not in values:
                channel_scores[name] = 0.0
                continue

            value = float(values[name])
            ref = self._update_ema(name, t, value)
            self._hist[name].append((t, value))
            self._prune(name, t)

            dev = abs(value - ref)
            std = self._std(name)
            roc = self._roc(name, t)
            score = max(dev, std, roc) / cf.noise_floor
            channel_scores[name] = score
            if score > overall:
                overall = score

        event_active = self._update_event(t, overall)
        return overall, channel_scores, event_active

    def _update_event(self, t: float, score: float) -> bool:
        """Event debounce (§5)."""
        if score > self.cfg.event_threshold:
            if self._potential_start is None:
                self._potential_start = t
            self.event_active = (t - self._potential_start) >= self.cfg.event_min_duration_s
        else:
            self._potential_start = None
            self.event_active = False
        return self.event_active

    # ------------------------------------------------------------------ #
    # introspection (used by tests / diagnostics)
    # ------------------------------------------------------------------ #
    def baseline(self, name: str) -> Optional[float]:
        return self._ema[name]

    def history_length(self, name: str) -> int:
        return len(self._hist[name])

    @staticmethod
    def run_offline(
        config: AnalyzerConfig,
        samples: Iterable[Tuple[float, Mapping[str, float]]],
    ) -> List[Tuple[float, float, Dict[str, float], bool]]:
        """Convenience helper: run the analyzer over a whole sample sequence.

        Returns one tuple per sample: ``(t, overall_score, channel_scores,
        event_active)``.
        """
        analyzer = ChangeAnalyzer(config)
        out: List[Tuple[float, float, Dict[str, float], bool]] = []
        for t, values in samples:
            score, per_channel, event = analyzer.update(t, values)
            out.append((t, score, per_channel, event))
        return out
