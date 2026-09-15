"""Figure output guarantees.

Figures are deliberately **not** required to be byte-identical across machines:
PNG rasterisation depends on the matplotlib/freetype/harfbuzz builds, not on the
experiment. (The reproducibility guarantee covers the numeric artefacts, which CI
checks separately — see `.github/workflows/python-tests.yml` and
`results/README.md`.)

What the figures must not do is embed the plotting library's version, because that
makes the committed bytes churn on every dependency bump and invites the reader to
mistake a dependency change for a change in the results. That is what this test
pins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import plots  # noqa: E402

METRICS_ALL = ROOT / "results" / "metrics_all.csv"
METRICS_SUMMARY = ROOT / "results" / "metrics_summary.csv"

VERSION_MARKERS = (b"Matplotlib version", b"matplotlib version")


def _png_text_chunks(path: Path):
    """Yield the (type, payload) of every ancillary text chunk in a PNG."""
    import struct

    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        chunk_type = data[i + 4 : i + 8].decode("latin1")
        payload = data[i + 8 : i + 8 + length]
        yield chunk_type, payload
        if chunk_type == "IEND":
            break
        i += 12 + length


def test_figures_embed_no_library_version(tmp_path, monkeypatch):
    monkeypatch.setattr(plots, "PLOTS_DIR", tmp_path)

    df = pd.read_csv(METRICS_ALL)
    plots.plot_sampling_count(df)
    plots.plot_communication_reduction(df)
    plots.plot_event_detection(df)
    plots.plot_detection_latency(df)
    plots.plot_tradeoff(pd.read_csv(METRICS_SUMMARY))

    produced = sorted(tmp_path.glob("*.png"))
    assert len(produced) == 5, [p.name for p in produced]

    for path in produced:
        raw = path.read_bytes()
        for marker in VERSION_MARKERS:
            assert marker not in raw, (
                f"{path.name} embeds the plotting library version; "
                f"pass metadata={{'Software': None}} to savefig"
            )
        # and it really is a decodable PNG with pixel data
        types = [t for t, _ in _png_text_chunks(path)]
        assert types[0] == "IHDR"
        assert types[-1] == "IEND"
        assert "IDAT" in types


def test_committed_figures_are_present_and_non_trivial():
    plots_dir = ROOT / "results" / "plots"
    expected = {
        "sampling_count.png",
        "communication_reduction.png",
        "event_detection.png",
        "detection_latency.png",
        "accuracy_efficiency_tradeoff.png",
    }
    found = {p.name for p in plots_dir.glob("*.png")}
    assert expected <= found, f"missing figures: {sorted(expected - found)}"
    for name in sorted(expected):
        size = (plots_dir / name).stat().st_size
        assert size > 10_000, f"{name} looks like an empty figure ({size} bytes)"
