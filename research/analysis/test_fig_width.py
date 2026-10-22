"""fig23 rides along the frozen PREREG_WIDTH scorer: on synthetic matrices it
draws one gain and one disagreement point per plant and lattice, with the
scorer's own numbers, writes the sheet, and inventories its caption."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, fig_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("POLYFORGE_FIG_DIR", str(fig_dir))
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(out_dir))
    sys.path.insert(0, str(HERE))
    import figures
    importlib.reload(figures)
    spec = importlib.util.spec_from_file_location("figwidth", HERE / "fig_width.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _test_width():
    spec = importlib.util.spec_from_file_location("tw_helpers", HERE / "test_width.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fig23_draws_the_scorers_gains_and_writes_the_sheet(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "figs", tmp_path / "out")
    tw = _test_width()
    frames = {p: mod.aw.prepare(tw._frame(mod.aw, tw.MECH if p != "live" else {}, i))
              for i, p in enumerate(mod.aw.PLANTS)}
    rows = mod.summary(frames)
    assert len(rows) == 6 and {r["lattice"] for r in rows} == {"narrow", "wide"}
    for r in rows:
        g = mod.aw.gains(frames[r["plant"]])
        col = "g_narrow" if r["lattice"] == "narrow" else "g_wide"
        dec = mod.aw.NARROW_DEC if r["lattice"] == "narrow" else mod.aw.WIDE_DEC
        assert r["g"] == float(g[col].mean())
        assert r["rate"] == float(g.rate_limit.mean())
        base = float(mod.aw._means(frames[r["plant"]], dec).loc[g.index].mean())
        assert r["pct"] == 100 * r["g"] / base     # the comparator's J, as in fig22
        assert r["lo"] <= r["pct"] <= r["hi"]
    wide_pub = next(r for r in rows if r["plant"] == "published" and r["lattice"] == "wide")
    assert wide_pub["pct"] > 0                     # MECH plants: joint better on the wide lattice
    fig, caption = mod.draw(rows)
    mod.F.save(fig, "fig23_width_mechanism", caption)
    sheet = mod.sheet(rows, caption)
    assert "fig23_width_mechanism.png" in sheet
    assert sheet.count("| published |") == 3       # two lattice rows + the WD-H5 row
    assert mod.F.read_inventory(tmp_path / "figs" / "FIGURES.md")["fig23_width_mechanism"] == caption
