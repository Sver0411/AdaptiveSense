"""AdaptiveSense change-aware adaptive sampling scheduler.

This module implements the AdaptiveSense policy as a self-contained,
stateless-interleaving stream algorithm. The exact same logic is mirrored in
the embedded firmware (`firmware/`) so that simulator results and on-device
behaviour remain comparable.

The scheduler receives one measured value per channel at irregular times
(replay decides when to call :meth:`update` based on the previously returned
interval) and decides:

- the sampling interval to use for the *next* sample,
- the current STABLE / ACTIVE / ALERT state,
- whether a sustained event is detected (debounced),
- whether this sample should be uploaded to the server.

The scheduler is intentionally decoupled from any sensor driver or time base.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# States of the scheduler state machine.
STABLE = "STABLE"
ACTIVE = "ACTIVE"
ALERT = "ALERT"
STATES = (STABLE, ACTIVE, ALERT)


@dataclass
class ChannelAnalyzerConfig:
    """Per-channel configuration consumed by the scheduler."""

    noise_floor: float
    use: bool


@dataclass
class Decision:
    """Result of processing a single measured sample."""

    timestamp: float
    values: Dict[str, float]
    state: str
    interval_s: float
    score: float
    detected_event: bool
    upload: bool
    channel_scores: Dict[str, float] = field(default_factory=dict)


class AdaptiveScheduler:
    """Streaming, hysteresis-guarded adaptive sampling scheduler."""

    def __init__(self, config: dict, *, time: float = 0.0) -> None:
        self.config = config

        # --- sanitized, centralized parameters -----------------------------
        samp = config["sampling"]
        self.min_interval = float(samp["min_interval"])
        self.default_interval = float(samp["default_interval"])
        self.max_interval = float(samp["max_interval"])

        a = config["adaptive"]
        self.stable_threshold = float(a["stable_threshold"])
        self.active_threshold = float(a["active_threshold"])
        self.hyst = float(a["hysteresis_fraction"])

        # relative hysteresis on each boundary
        self._stable_high = self.stable_threshold * (1.0 + self.hyst)
        self._stable_low = self.stable_threshold * (1.0 - self.hyst)
        self._active_high = self.active_threshold * (1.0 + self.hyst)
        self._active_low = self.active_threshold * (1.0 - self.hyst)

        self.ladders: Dict[str, List[float]] = {
            s: [float(x) for x in a["ladders"][s.lower()]]
            for s in STATES
        }
        self.event_threshold = float(a["event_threshold"])
        self.event_min_duration = float(a["event_min_duration_s"])

        up = a["upload"]
        self.up_on_event = bool(up["on_event"])
        self.up_on_state_change = bool(up["on_state_change"])
        self.up_on_interval_change = bool(up["on_interval_change"])
        self.heartbeat = float(up["heartbeat_s"])
        self.delta_threshold = float(up["delta_threshold"])

        an = a["analyzer"]
        self.avg_window = float(an["variety_window_s"])
        self.roc_window = float(an["roc_window_s"])
        self.baseline_tau = float(an["baseline_tau_s"])

        # channel selection
        self.channels: Dict[str, ChannelAnalyzerConfig] = {}
        for name, ch in a["channels"].items():
            self.channels[name] = ChannelAnalyzerConfig(
                noise_floor=max(1e-9, float(ch["noise_floor"])),
                use=bool(ch["use"]),
            )

        # --- runtime state -------------------------------------------------
        self.now = float(time)
        self.state = STABLE
        self._ladder_pos: Dict[str, int] = {s: 0 for s in STATES}
        self._interval = self.default_interval

        # per-channel rolling windows keyed by name
        self._hist: Dict[str, deque] = {name: deque() for name in self.channels}
        self._last_time: Optional[float] = None
        self._prev_values: Dict[str, Optional[float]] = {
            name: None for name in self.channels
        }
        # persistent EMA baseline per channel (robust across long intervals)
        self._ema: Dict[str, Optional[float]] = {
            name: None for name in self.channels
        }

        # upload bookkeeping
        self._last_upload: Optional[float] = None
        self._last_upload_values: Dict[str, Optional[float]] = {
            name: None for name in self.channels
        }
        self._last_state = self.state
        self._last_interval = self._interval

        # event debounce
        self._event_potential_start: Optional[float] = None
        self._event_active = False
        self._notified_event = False

    # ------------------------------------------------------------------ #
    # private helpers
    # ------------------------------------------------------------------ #
    def _window_values(self, name: str, window: float) -> List[float]:
        """Values in `_hist[name]` that fall within the last `window` s."""
        out = []
        for t, v in self._hist[name]:
            if t >= self.now - window:
                out.append(v)
        return out

    def _update_ema(self, name: str, value: float) -> float:
        """Update the persistent EMA baseline, return the deviation ref (old EMA)."""
        ref = self._ema[name]
        if ref is None:
            self._ema[name] = value
            return value
        dt = 0.0
        if self._last_time is not None and self.now > self._last_time:
            dt = self.now - self._last_time
        alpha = dt / (dt + self.baseline_tau) if dt > 0 else 0.0
        new_ema = ref + alpha * (value - ref)
        self._ema[name] = new_ema
        return ref

    def _channel_score(self, name: str, value: float, ref: float) -> float:
        ch = self.channels[name]
        rows = list(self._hist[name])
        base = ch.noise_floor

        # deviation from the persistent EMA change baseline (robust across
        # long sampling intervals, because the baseline is not forgotten)
        dev = abs(value - ref)

        # variance / std of the recent variety window
        var_vals = self._window_values(name, self.avg_window)
        if len(var_vals) >= 2:
            m = sum(var_vals) / len(var_vals)
            var = sum(v * v for v in var_vals) / len(var_vals) - m * m
            std = math.sqrt(max(0.0, var))
        else:
            std = 0.0

        # rate of change: mean |dv/dt| over recent transitions
        prev_t = self._last_time
        prev_v = self._prev_values[name]
        if prev_v is not None and prev_t is not None and self.now > prev_t:
            roc_inst = abs(value - prev_v) / (self.now - prev_t)
        else:
            roc_inst = 0.0
        roc_vals = [roc_inst] + [
            abs(v2 - v1) / (t2 - t1)
            for (t1, v1), (t2, v2) in zip(rows, rows[1:])
            if t2 - t1 > 0
        ]
        roc = sum(roc_vals) / len(roc_vals) if roc_vals else 0.0

        score = max(dev / base, std / base, roc / base)
        return score

    def _compute_score(self, values: Dict[str, float], refs: Dict[str, float]) -> float:
        channel_scores: Dict[str, float] = {}
        overall = 0.0
        for name, ch in self.channels.items():
            if not ch.use or name not in values:
                channel_scores[name] = 0.0
                continue
            sc = self._channel_score(name, values[name], refs[name])
            channel_scores[name] = sc
            overall = max(overall, sc)
        return overall, channel_scores

    def _apply_state_machine(self, score: float) -> None:
        """Update state with relative hysteresis on each boundary."""
        s = self.state
        if s == STABLE:
            if score > self._stable_high:
                s = ACTIVE
        elif s == ACTIVE:
            if score < self._stable_low:
                s = STABLE
            elif score > self._active_high:
                s = ALERT
        elif s == ALERT:
            if score < self._active_low:
                s = ACTIVE
        else:  # pragma: no cover - defensive
            s = STABLE
        self.state = s

    def _advance_ladder(self) -> float:
        """Advance the interval ladder for the current state, return new interval."""
        ladder = self.ladders[self.state]
        pos = min(self._ladder_pos[self.state], len(ladder) - 1)
        self._ladder_pos[self.state] = pos + 1  # advance every evaluation
        return ladder[pos]

    def _reset_ladder_position(self) -> None:
        self._ladder_pos = {s: 0 for s in STATES}

    def _update_event(self, score: float) -> bool:
        if score > self.event_threshold:
            if self._event_potential_start is None:
                self._event_potential_start = self.now
            if self.now - self._event_potential_start >= self.event_min_duration:
                self._event_active = True
                return True
        else:
            self._event_potential_start = None
            self._event_active = False
            self._notified_event = False
        return False

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def update(self, timestamp: float, values: Dict[str, float]) -> Decision:
        """Feed one measured sample into the scheduler and get a decision."""
        self.now = float(timestamp)

        # record history and advance the per-channel EMA baseline
        refs: Dict[str, float] = {}
        for name in self.channels:
            if name in values:
                self._hist[name].append((self.now, values[name]))
                refs[name] = self._update_ema(name, values[name])
                # prune older history
                while (
                    self._hist[name]
                    and self._hist[name][0][0] < self.now - self.avg_window
                ):
                    self._hist[name].popleft()

        score, channel_scores = self._compute_score(values, refs)

        # event detection on the live (sampled) stream
        detected_event = self._update_event(score)
        if detected_event and not self._notified_event:
            self._notified_event = True

        # state machine
        prev_state = self.state
        self._apply_state_machine(score)
        state_changed = self.state != prev_state
        if state_changed:
            self._reset_ladder_position()

        # choose interval for the NEXT sample
        new_interval = self._advance_ladder()
        self._interval = new_interval
        interval_changed = not math.isclose(new_interval, self._last_interval)

        # --- upload decision ---------------------------------------------
        upload = False
        if self.up_on_event and detected_event:
            upload = True
        if self.up_on_state_change and state_changed and not upload:
            upload = True
        if self.up_on_interval_change and interval_changed and not upload:
            upload = True
        # heartbeat guarantee
        if (
            self.heartbeat > 0
            and self._last_upload is not None
            and (self.now - self._last_upload) >= self.heartbeat
        ):
            upload = True
        # change-driven delta reporting
        if not upload and self.delta_threshold > 0:
            for name in self.channels:
                if not self.channels[name].use or name not in values:
                    continue
                last_up = self._last_upload_values.get(name)
                if last_up is not None:
                    nd = abs(values[name] - last_up) / self.channels[name].noise_floor
                    if nd >= self.delta_threshold:
                        upload = True
                        break

        if upload:
            self._last_upload = self.now
            self._last_upload_values = dict(values)

        # persist transition tracking
        self._last_state = self.state
        self._last_interval = self._interval
        self._last_time = self.now
        for name, v in values.items():
            self._prev_values[name] = v

        return Decision(
            timestamp=self.now,
            values=dict(values),
            state=self.state,
            interval_s=new_interval,
            score=score,
            detected_event=detected_event,
            upload=upload,
            channel_scores=channel_scores,
        )

    @property
    def interval(self) -> float:
        """Sampling interval currently selected for the next sample."""
        return self._interval

    @property
    def current_state(self) -> str:
        return self.state