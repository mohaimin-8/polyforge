"""What a figure asserts, in a form that does not depend on the machine.

`scripts/reproduce.py` compared the figure FILES byte for byte. On the machine
that drew them that check is exact and worth keeping. Across machines it cannot
hold and never could: `figures.save()` saves with `bbox_inches="tight"`, which
sizes the saved canvas from the *rendered extents of the text*, so the page
geometry of every PDF and the pixel dimensions of every PNG are a function of
the local font stack. Session 43 measured it -- CI on Linux reported 0 of 38
figure files identical to the copies drawn on Windows, in the same run that
reported all 40 records identical.

A rasterisation is not a claim. What a figure asserts is the numbers it plots,
the axes it plots them on, and the colours that carry the entity. This module
extracts exactly that from a finished Figure -- every line's vertices, every
bar's geometry, every scatter offset and segment, the scales, limits, tick
labels and legend entries -- and writes it as canonical JSON.

That artifact reproduces across platforms, so it can be gated in CI on Linux
rather than only on the author's machine, and it is strictly more sensitive to
the thing that matters: change one number in a record and the fingerprint
moves, while a different font renderer leaves it untouched.

Deliberately excluded: anything solved against rendered text extents. Legend
PLACEMENT ("best" location), axes positions, and the tight bounding box are the
platform-dependent geometry this exists to exclude. Legend LABELS are kept.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.colors import to_hex

# Doubles carry 15-17 significant digits. Twelve leaves four orders of
# magnitude of headroom above the last bit, which absorbs any library-level
# arithmetic difference, and sits eight orders below the four-significant-figure
# precision the records report -- so any change that could alter a reading moves
# the fingerprint, and a last-bit difference never does.
DIGITS = "%.12g"
CONTENT = "content"


def _num(value) -> str:
    return DIGITS % float(value)


def _nums(values) -> list[str]:
    return [_num(v) for v in values]


def _xy(points) -> list[list[str]]:
    """Vertices as fixed-precision strings. Masked points (matplotlib's way of
    breaking a line) are filled with nan so a gap is recorded as a gap."""
    array = np.ma.filled(np.ma.asanyarray(points, dtype=float), np.nan)
    return [[_num(x), _num(y)] for x, y in array.reshape(-1, 2)]


def _colour(value) -> str:
    return to_hex(value, keep_alpha=True)


def _colours(values) -> list[str]:
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return []
    return [_colour(row) for row in np.atleast_2d(array)]


def _line(line) -> dict:
    return {
        "label": str(line.get_label()),
        "colour": _colour(line.get_color()),
        "style": str(line.get_linestyle()),
        "marker": str(line.get_marker()),
        "width": _num(line.get_linewidth()),
        "points": _xy(line.get_xydata()),
    }


def _patch(patch) -> dict:
    """Bars are the common case and their geometry IS the claim, so record it
    directly rather than as a generic path."""
    out = {"kind": type(patch).__name__, "colour": _colour(patch.get_facecolor())}
    if hasattr(patch, "get_width") and hasattr(patch, "get_height"):
        out["xy"] = _nums(patch.get_xy())
        out["width"] = _num(patch.get_width())
        out["height"] = _num(patch.get_height())
    else:
        out["vertices"] = _xy(patch.get_path().vertices)
    return out


def _collection(coll) -> dict:
    out = {
        "kind": type(coll).__name__,
        "facecolours": _colours(coll.get_facecolor()),
        "edgecolours": _colours(coll.get_edgecolor()),
    }
    if hasattr(coll, "get_segments"):          # hlines, vlines, errorbar caps
        out["segments"] = [_xy(s) for s in coll.get_segments()]
    else:                                       # scatter, fill_between
        out["offsets"] = _xy(coll.get_offsets())
        out["paths"] = [_xy(p.vertices) for p in coll.get_paths()]
    return out


def _text(text) -> dict:
    return {
        "text": text.get_text(),
        "xy": _nums(text.get_position()),
        "colour": _colour(text.get_color()),
    }


def _axes(ax) -> dict:
    legend = ax.get_legend()
    return {
        "title": ax.get_title(),
        "xlabel": ax.get_xlabel(),
        "ylabel": ax.get_ylabel(),
        "xscale": ax.get_xscale(),
        "yscale": ax.get_yscale(),
        "xlim": _nums(ax.get_xlim()),
        "ylim": _nums(ax.get_ylim()),
        "xticks": _nums(ax.get_xticks()),
        "yticks": _nums(ax.get_yticks()),
        "xticklabels": [t.get_text() for t in ax.get_xticklabels()],
        "yticklabels": [t.get_text() for t in ax.get_yticklabels()],
        "lines": [_line(ln) for ln in ax.get_lines()],
        "patches": [_patch(p) for p in ax.patches],
        "collections": [_collection(c) for c in ax.collections],
        "texts": [_text(t) for t in ax.texts],
        "legend": [t.get_text() for t in legend.get_texts()] if legend else [],
    }


def fingerprint(fig, name: str) -> dict:
    return {
        "figure": name,
        "size_inches": _nums(fig.get_size_inches()),
        "texts": [_text(t) for t in fig.texts],
        "axes": [_axes(ax) for ax in fig.axes],
    }


def write(fig, name: str, fig_dir: Path) -> Path:
    """Written as bytes with LF endings explicitly.

    `Path.write_text` translates newlines to os.linesep, which would make this
    file CRLF on Windows and LF on Linux -- a byte difference in the one
    artifact whose entire purpose is to be compared byte for byte across
    exactly those two platforms.
    """
    out = fig_dir / CONTENT / (name + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    _stamp(fig_dir)
    payload = json.dumps(fingerprint(fig, name), indent=1, sort_keys=True,
                         ensure_ascii=True)
    out.write_bytes((payload + "\n").encode("utf-8"))
    return out


def _stamp(fig_dir: Path) -> None:
    """Which renderer drew the image files sitting next to this dump.

    The dump itself stays platform-free -- it is the artifact compared across
    machines -- so the platform is recorded beside it rather than inside it.
    `scripts/reproduce.py` reads this to decide whether comparing the rendered
    PDFs and PNGs byte for byte means anything on the machine it is running on.
    """
    payload = {"platform": sys.platform, "matplotlib": matplotlib.__version__}
    (fig_dir / CONTENT / "rendered_on.json").write_bytes(
        (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode("utf-8"))
