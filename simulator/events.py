"""Events, independent ground-truth labels, and one-to-one matching.

Ground truth and detection are deliberately kept apart:

* **Ground truth** (:func:`load_ground_truth_events`) is read from the label
  files written by :mod:`dataset.generate_dataset`. The generator knows which
  interval it drove which channel over, so the labels do not depend on
  AdaptiveSense in any way. `docs/change_score_spec.md` section 10.

* **Detection** (:func:`detect_events`) replays the documented change score over
  the samples a node actually observed and extracts the onset intervals. It is
  applied unchanged to every strategy, so all strategies are scored by the same
  extraction code.

v0.1 derived the ground truth by running AdaptiveSense's own instability score
over the full-resolution signal, which made the evaluation circular: a strategy
was scored on whether it could reproduce its own scoring function.

Matching is channel-aware and one-to-one (:func:`match_events`).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .scoring import AnalyzerConfig, ChangeAnalyzer

__all__ = [
    "Event",
    "Match",
    "MatchResult",
    "load_ground_truth_events",
    "load_labels",
    "write_labels",
    "detect_events",
    "match_events",
]

EVENT_TYPES = ("sudden_change", "sustained_change", "detected")


@dataclass(frozen=True)
class Event:
    """A time-bounded, channel-scoped event interval."""

    channel: str
    start_s: float
    end_s: float
    event_type: str = "detected"

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass(frozen=True)
class Match:
    """One ground-truth event matched to one detection."""

    ground_truth: Event
    detected: Event

    @property
    def latency_s(self) -> float:
        """Detection latency, clamped at 0 (spec section 11)."""
        return max(0.0, self.detected.start_s - self.ground_truth.start_s)


@dataclass(frozen=True)
class MatchResult:
    """Outcome of one-to-one matching for a single run."""

    matches: Tuple[Match, ...]
    unmatched_ground_truth: Tuple[Event, ...]
    unmatched_detections: Tuple[Event, ...]
    redundant_detections: Tuple[Event, ...] = ()

    @property
    def true_positives(self) -> int:
        return len(self.matches)

    @property
    def false_negatives(self) -> int:
        return len(self.unmatched_ground_truth)

    @property
    def false_positives(self) -> int:
        """Unmatched detections, per the requested strict definition.

        This includes *redundant* detections: a single physical event seen by
        the policy as more than one rise/fall trigger leaves the extra
        detections unmatched. `redundant_detections` isolates them so that a
        repeated trigger for a real event is not confused with a detection that
        has no counterpart at all.
        """
        return len(self.unmatched_detections)

    @property
    def latencies(self) -> List[float]:
        return [m.latency_s for m in self.matches]


# ---------------------------------------------------------------------- #
# ground-truth labels (independent of AdaptiveSense)
# ---------------------------------------------------------------------- #
LABEL_FIELDS = ["channel", "start_s", "end_s", "event_type"]


def load_labels(path: Path | str) -> List[Event]:
    """Read a ground-truth label file.

    A header-only file is legal and means "this scenario contains no injected
    disturbance"; it yields an empty list, not an error.
    """
    path = Path(path)
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    events: List[Event] = []
    for r in rows:
        events.append(
            Event(
                channel=str(r["channel"]).strip(),
                start_s=float(r["start_s"]),
                end_s=float(r["end_s"]),
                event_type=str(r.get("event_type", "sudden_change")).strip() or "sudden_change",
            )
        )
    events.sort(key=lambda e: (e.start_s, e.channel))
    return events


def write_labels(path: Path | str, events: Sequence[Event]) -> None:
    """Write a ground-truth label file (header always present)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(events, key=lambda e: (e.start_s, e.channel))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LABEL_FIELDS)
        w.writeheader()
        for e in ordered:
            w.writerow(
                {
                    "channel": e.channel,
                    "start_s": _fmt(e.start_s),
                    "end_s": _fmt(e.end_s),
                    "event_type": e.event_type,
                }
            )


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def label_path_for(raw_path: Path | str, label_dir: Path | str) -> Path:
    """`dataset/raw/scenario_b_sudden.csv` -> `dataset/labels/scenario_b_sudden_events.csv`."""
    raw_path = Path(raw_path)
    return Path(label_dir) / f"{raw_path.stem}_events.csv"


def load_ground_truth_events(
    raw_path: Path | str, label_dir: Path | str
) -> List[Event]:
    """Load the ground-truth events belonging to a raw scenario file.

    Raises ``FileNotFoundError`` if the label file is missing: silently
    synthesising labels is exactly the circularity this design removes.
    """
    path = label_path_for(raw_path, label_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"ground-truth labels missing for {Path(raw_path).name}: expected {path}. "
            f"Regenerate them with `python dataset/generate_dataset.py`."
        )
    return load_labels(path)


# ---------------------------------------------------------------------- #
# detection over an actually-observed sample stream
# ---------------------------------------------------------------------- #
def detect_events(
    samples: Sequence[Mapping[str, float]],
    config: AnalyzerConfig,
    channels: Optional[Iterable[str]] = None,
) -> List[Event]:
    """Extract detected events from the samples a node actually observed.

    `samples` is a time-ordered sequence of mappings that contain at least the
    time column and the sensor channels. Detection follows
    `docs/change_score_spec.md` sections 1-5, then collects the intervals during
    which ``event_active`` was true, per channel.
    """
    if not samples:
        return []

    names: List[str] = (
        [c for c in channels if c in samples[0]]
        if channels is not None
        else [c.name for c in config.channels if c.name in samples[0]]
    )

    # One analyzer per channel: the detector must score a channel from that
    # channel's own samples only, exactly as the on-device analyzer does.
    analyzers: Dict[str, ChangeAnalyzer] = {n: ChangeAnalyzer(config) for n in names}
    open_at: Dict[str, Optional[float]] = {n: None for n in names}
    last_active_at: Dict[str, float] = {n: 0.0 for n in names}
    events_by_channel: Dict[str, List[Event]] = {n: [] for n in names}

    for row in samples:
        t = float(row["timestamp"])
        for name in names:
            _, _, active = analyzers[name].update(t, {name: float(row[name])})
            if active:
                if open_at[name] is None:
                    open_at[name] = t
                last_active_at[name] = t
            elif open_at[name] is not None:
                events_by_channel[name].append(
                    Event(name, open_at[name], last_active_at[name], "detected")
                )
                open_at[name] = None

    for name in names:
        if open_at[name] is not None:
            events_by_channel[name].append(
                Event(name, open_at[name], last_active_at[name], "detected")
            )

    events: List[Event] = []
    for name in names:
        events.extend(events_by_channel[name])
    events.sort(key=lambda e: (e.start_s, e.channel))
    return events


# ---------------------------------------------------------------------- #
# one-to-one, channel-aware matching
# ---------------------------------------------------------------------- #
def match_events(
    ground_truth: Sequence[Event],
    detected: Sequence[Event],
    tolerance_s: float,
) -> MatchResult:
    """Channel-aware one-to-one matching (spec section 11).

    A detection may match at most one ground-truth event and vice versa; the
    best candidate is the one whose onset is closest to the ground-truth onset.
    """
    gt_sorted = sorted(ground_truth, key=lambda e: (e.start_s, e.channel))
    available = list(enumerate(detected))  # (index, event); identity-safe
    matches: List[Match] = []
    matched_gt_idx: List[int] = []
    matched_det_idx: List[int] = []

    for gi, g in enumerate(gt_sorted):
        best_pos = -1
        best_distance = float("inf")
        for pos, (_, d) in enumerate(available):
            if d.channel != g.channel:
                continue
            if not (g.start_s - tolerance_s <= d.start_s <= g.end_s + tolerance_s):
                continue
            distance = abs(d.start_s - g.start_s)
            if distance < best_distance:
                best_distance = distance
                best_pos = pos
        if best_pos >= 0:
            di, d = available.pop(best_pos)
            matches.append(Match(ground_truth=g, detected=d))
            matched_gt_idx.append(gi)
            matched_det_idx.append(di)

    unmatched_gt = [g for gi, g in enumerate(gt_sorted) if gi not in matched_gt_idx]
    unmatched_det = [d for di, d in enumerate(detected) if di not in matched_det_idx]

    # A detection that overlaps a same-channel ground-truth event but lost the
    # one-to-one assignment is a *repeat trigger for a physical event that was
    # already reported*, not an alarm with no counterpart. Reporting it
    # separately keeps the strict false-positive count honest while making the
    # cause visible in the results table.
    redundant = [
        d
        for d in unmatched_det
        if any(
            g.channel == d.channel
            and (g.start_s - tolerance_s <= d.start_s <= g.end_s + tolerance_s)
            for g in gt_sorted
        )
    ]

    return MatchResult(
        matches=tuple(matches),
        unmatched_ground_truth=tuple(unmatched_gt),
        unmatched_detections=tuple(unmatched_det),
        redundant_detections=tuple(redundant),
    )
