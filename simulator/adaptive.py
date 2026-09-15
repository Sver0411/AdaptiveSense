"""AdaptiveSense policy: adaptive sampling scheduler.

Normative implementation of `docs/change_score_spec.md` sections 1-9. The
identical equations are implemented in C in
`firmware/main/change_detector.c` (score) and
`firmware/main/adaptive_scheduler.c` (state machine, ladder, upload policy);
`tests/test_parity_python_c.py` compiles the C policy for the host and compares
the two implementations on a shared fixture.

The scheduler receives one measured value per channel at irregular times and
decides:

* the sampling interval to use for the *next* sample,
* the current STABLE / ACTIVE / ALERT state,
* whether a sustained event is active (debounced),
* whether this sample should be uploaded.

It is decoupled from any sensor driver or time base: the caller says what time
it is and what was measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import analyzer_config, confirmations, ladder
from .scoring import ChangeAnalyzer

# States of the scheduler state machine.
STABLE = "STABLE"
ACTIVE = "ACTIVE"
ALERT = "ALERT"
STATES = (STABLE, ACTIVE, ALERT)

__all__ = [
    "STABLE",
    "ACTIVE",
    "ALERT",
    "STATES",
    "Decision",
    "AdaptiveScheduler",
    "transition",
    "next_rung",
    "hysteresis_bounds",
]


def hysteresis_bounds(
    stable_threshold: float, active_threshold: float, hysteresis_fraction: float
) -> Dict[str, float]:
    """Entry (upper) and retention (lower) bounds for the state machine (§6)."""
    h = hysteresis_fraction
    return {
        "stable_high": stable_threshold * (1.0 + h),
        "stable_low": stable_threshold * (1.0 - h),
        "active_high": active_threshold * (1.0 + h),
        "active_low": active_threshold * (1.0 - h),
    }


def transition(state: str, score: float, bounds: Dict[str, float]) -> str:
    """Pure state transition (spec section 6).

    Asymmetric on purpose:

    * **escalation is immediate** — a score above a state's entry bound reaches
      that state in a single sample, so STABLE can go straight to ALERT.
    * **de-escalation moves one level at a time** and only once the score has
      fallen below the current state's *retention* bound. The node is therefore
      quick to react and cautious to relax: an ALERT does not drop straight to
      STABLE (and to a 20 s interval) while the environment is still moving.
    """
    if score >= bounds["active_high"]:
        return ALERT
    if state == ALERT:
        return ALERT if score >= bounds["active_low"] else ACTIVE
    if score >= bounds["stable_high"]:
        return ACTIVE
    if state == ACTIVE:
        return ACTIVE if score >= bounds["stable_low"] else STABLE
    return STABLE


def next_rung(
    position: int, rung_count: int, ladder: List[float], confirmations: int
) -> "tuple[float, int, int]":
    """Pure ladder step (spec section 7).

    Returns ``(interval, new_position, new_rung_count)``. A rung is held for
    ``confirmations`` consecutive evaluations before the ladder advances; the
    position is clamped to the last rung, so a state that persists settles on its
    final interval instead of running off the end.
    """
    last = len(ladder) - 1
    pos = min(position, last)
    interval = ladder[pos]

    count = rung_count + 1
    if count >= confirmations:
        return interval, min(pos + 1, last), 0
    return interval, pos, count


@dataclass
class Decision:
    """Result of processing a single measured sample."""

    timestamp: float
    values: Dict[str, float]
    state: str
    interval_s: float
    score: float
    detected_event: bool
    upload_requested: bool
    channel_scores: Dict[str, float] = field(default_factory=dict)


class AdaptiveScheduler:
    """Streaming, hysteresis-guarded adaptive sampling scheduler."""

    def __init__(self, config: dict, *, time: float = 0.0) -> None:
        self.config = config
        a = config["adaptive"]

        samp = config["sampling"]
        self.min_interval = float(samp["min_interval"])
        self.default_interval = float(samp["default_interval"])
        self.max_interval = float(samp["max_interval"])

        self.stable_threshold = float(a["stable_threshold"])
        self.active_threshold = float(a["active_threshold"])
        self.hyst = float(a["hysteresis_fraction"])

        # entry (upper) and retention (lower) bounds — spec section 6
        self.bounds = hysteresis_bounds(
            self.stable_threshold, self.active_threshold, self.hyst
        )
        self.stable_high = self.bounds["stable_high"]
        self.stable_low = self.bounds["stable_low"]
        self.active_high = self.bounds["active_high"]
        self.active_low = self.bounds["active_low"]

        self.ladders: Dict[str, List[float]] = {s: ladder(config, s) for s in STATES}
        self.confirm: Dict[str, int] = {s: confirmations(config, s) for s in STATES}

        up = a["upload"]
        self.up_first_sample = bool(up.get("upload_first_sample", True))
        self.up_on_event = bool(up["on_event"])
        self.up_on_state_change = bool(up["on_state_change"])
        self.up_on_interval_change = bool(up["on_interval_change"])
        self.heartbeat = float(up["heartbeat_s"])
        self.delta_threshold = float(up["delta_threshold"])

        # --- runtime state -------------------------------------------------
        self.analyzer = ChangeAnalyzer(analyzer_config(config))
        self.now = float(time)
        self.state: str = STABLE
        self._ladder_pos: Dict[str, int] = {s: 0 for s in STATES}
        self._rung_count: Dict[str, int] = {s: 0 for s in STATES}
        self._interval = self.default_interval

        self._n_samples = 0
        self._last_interval = self.default_interval
        self._last_upload_t: Optional[float] = None
        self._last_upload_values: Dict[str, float] = {}
        self._prev_event_active = False

    # ------------------------------------------------------------------ #
    # private helpers
    # ------------------------------------------------------------------ #
    def _next_state(self, score: float, state: str) -> str:
        """State machine with immediate escalation, hysteretic retention (§6)."""
        return transition(state, score, self.bounds)

    def _reset_ladder(self) -> None:
        self._ladder_pos = {s: 0 for s in STATES}
        self._rung_count = {s: 0 for s in STATES}

    def _advance_ladder(self) -> float:
        """Return the interval for the next sample and advance the ladder (§7)."""
        state = self.state
        interval, pos, count = next_rung(
            self._ladder_pos[state],
            self._rung_count[state],
            self.ladders[state],
            self.confirm[state],
        )
        self._ladder_pos[state] = pos
        self._rung_count[state] = count
        return interval

    def _upload_decision(
        self,
        values: Dict[str, float],
        event_onset: bool,
        state_changed: bool,
        interval_changed: bool,
    ) -> bool:
        """Spec section 8."""
        if self.up_first_sample and self._n_samples == 1:
            return True
        if self.up_on_event and event_onset:
            return True
        if self.up_on_state_change and state_changed:
            return True
        if self.up_on_interval_change and interval_changed:
            return True
        if (
            self.heartbeat > 0.0
            and self._last_upload_t is not None
            and (self.now - self._last_upload_t) >= self.heartbeat
        ):
            return True
        if self.delta_threshold > 0.0:
            for cf in self.analyzer.cfg.channels:
                if not cf.use or cf.name not in values:
                    continue
                last_up = self._last_upload_values.get(cf.name)
                if last_up is None:
                    continue
                normalized = abs(float(values[cf.name]) - last_up) / cf.noise_floor
                if normalized >= self.delta_threshold:
                    return True
        return False

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def update(self, timestamp: float, values: Dict[str, float]) -> Decision:
        """Feed one measured sample into the scheduler and get a decision."""
        self.now = float(timestamp)
        self._n_samples += 1

        score, channel_scores, event_active = self.analyzer.update(self.now, values)
        event_onset = event_active and not self._prev_event_active

        prev_state = self.state
        self.state = self._next_state(score, prev_state)
        state_changed = self.state != prev_state
        if state_changed:
            self._reset_ladder()

        new_interval = self._advance_ladder()
        interval_changed = not math.isclose(new_interval, self._last_interval)
        self._interval = new_interval

        upload = self._upload_decision(
            values, event_onset, state_changed, interval_changed
        )
        if upload:
            self._last_upload_t = self.now
            self._last_upload_values = {k: float(v) for k, v in values.items()}

        self._prev_event_active = event_active
        self._last_interval = new_interval

        return Decision(
            timestamp=self.now,
            values=dict(values),
            state=self.state,
            interval_s=new_interval,
            score=score,
            detected_event=event_active,
            upload_requested=upload,
            channel_scores=channel_scores,
        )

    @property
    def interval(self) -> float:
        """Sampling interval currently selected for the next sample."""
        return self._interval

    @property
    def state_name(self) -> str:
        return self.state

    @property
    def n_samples(self) -> int:
        return self._n_samples
