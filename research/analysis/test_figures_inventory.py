"""FIGURES.md is written by the save that draws each figure: a caption is
upserted in place, later figures survive a rerun of the base twelve, and the
parser the gate and the site use reads back exactly what was written."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _figures(monkeypatch, fig_dir: Path):
    """A private instance of figures.py pointed at fig_dir -- never the
    cached `figures` module, which the other figure tests import with
    their own FIG_DIR."""
    monkeypatch.setenv("POLYFORGE_FIG_DIR", str(fig_dir))
    spec = importlib.util.spec_from_file_location("figures_inventory_probe", HERE / "figures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_caption_upserts_and_preserves_the_rest(tmp_path, monkeypatch):
    F = _figures(monkeypatch, tmp_path)
    F.record_caption("fig01_cost_by_system", "spends the least")
    F.record_caption("fig18_risk_frontier", "the knob moved the wrong way")
    F.record_caption("fig01_cost_by_system", "spends the least (rewritten)")

    text = (tmp_path / "FIGURES.md").read_text(encoding="utf-8")
    assert text.startswith(F.INVENTORY_HEADER)
    assert text.count("- **fig01_cost_by_system** — ") == 1
    assert "spends the least (rewritten)" in text and "spends the least\n" not in text
    assert F.read_inventory(tmp_path / "FIGURES.md") == {
        "fig01_cost_by_system": "spends the least (rewritten)",
        "fig18_risk_frontier": "the knob moved the wrong way",
    }


def test_save_records_the_caption_it_draws(tmp_path, monkeypatch):
    F = _figures(monkeypatch, tmp_path)
    fig, ax = F.plt.subplots()
    ax.plot([0, 1], [0, 1])
    F.save(fig, "fig99_probe", "a probe caption")

    assert F.read_inventory(tmp_path / "FIGURES.md") == {"fig99_probe": "a probe caption"}
    assert (tmp_path / "fig99_probe.png").exists()
