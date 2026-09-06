"""The figure content dump must move when the data moves, and not otherwise.

`scripts/reproduce.py` gates on these dumps, and a gate that cannot fail is
not a gate -- the defect this whole check exists to correct was a figure step
that counted 19 rebuilt files and never compared one of them. So the property
is asserted in both directions: change a plotted number and the fingerprint
must change; change only how the figure is rendered and it must not.

Run: python -m pytest research/analysis/test_figure_content.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import figure_content as fc  # noqa: E402


def _figure(second_bar: float = 1.5, colour: str = "#2a78d6"):
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    ax.bar([0, 1, 2], [3.0, second_bar, 2.25], color=colour)
    ax.plot([0, 1, 2], [1.0, 2.0, 1.5], "o-", color="#e34948", label="line")
    ax.hlines([1.0], 0.2, 1.8, color="#008300")
    ax.set(title="t", xlabel="x", ylabel="y")
    ax.legend()
    return fig


def _dump(fig, tmp_path: Path) -> bytes:
    return fc.write(fig, "figtest", tmp_path).read_bytes()


def test_identical_figures_dump_identical_bytes(tmp_path):
    a = _dump(_figure(), tmp_path)
    b = _dump(_figure(), tmp_path)
    assert a == b, "the dump is not deterministic; it cannot be gated on"


def test_a_changed_data_point_changes_the_dump(tmp_path):
    # Arrange / Act - a change far below the records' 4-significant-figure
    # reporting precision, to show the gate is not merely catching gross edits.
    baseline = _dump(_figure(second_bar=1.5), tmp_path)
    nudged = _dump(_figure(second_bar=1.5000001), tmp_path)

    # Assert
    assert baseline != nudged, "a plotted value moved and the dump did not"


def test_a_changed_colour_changes_the_dump(tmp_path):
    # Colour carries the system entity in every figure in this thesis, so a
    # swapped palette slot is a changed claim, not a cosmetic edit.
    assert _dump(_figure(), tmp_path) != _dump(_figure(colour="#1baf7a"), tmp_path)


def test_rendering_choices_do_not_change_the_dump(tmp_path):
    """The property that makes this comparable across machines at all."""
    fig = _figure()
    before = _dump(fig, tmp_path)
    fig.savefig(tmp_path / "a.png", dpi=600, bbox_inches="tight")
    fig.savefig(tmp_path / "b.png", dpi=72)
    fig.set_dpi(53)
    after = _dump(fig, tmp_path)
    assert before == after, ("the dump moved when only the rasterisation did; "
                             "it would be as platform-bound as the PNG bytes")


def test_the_dump_is_lf_only(tmp_path):
    """CRLF here would make the artifact differ between Windows and Linux --
    the exact comparison it exists to support."""
    assert b"\r" not in _dump(_figure(), tmp_path)


def test_the_renderer_stamp_sits_beside_the_dump_not_inside_it(tmp_path):
    fc.write(_figure(), "figtest", tmp_path)
    payload = json.loads((tmp_path / "content" / "figtest.json").read_text("utf-8"))
    assert "platform" not in json.dumps(payload), (
        "the dump names a platform; it is meant to be platform-free")
    stamp = json.loads((tmp_path / "content" / "rendered_on.json").read_text("utf-8"))
    assert stamp["platform"] == sys.platform
    assert stamp["matplotlib"] == matplotlib.__version__
