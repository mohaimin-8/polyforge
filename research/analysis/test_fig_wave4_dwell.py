"""fig21 rides along the frozen B1'' scorer: on synthetic evidence it must draw
both controllers' trajectories, one churn row per AI cell with its bound, one
CI row per compared arm, write the sheet, and inventory its own caption."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, fig_dir: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)  # record_path() writes into an existing dir
    monkeypatch.setenv("POLYFORGE_FIG_DIR", str(fig_dir))
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(out_dir))
    # figures.py fixes FIG_DIR at import; the ride-along tests share one
    # pytest process, so the cached module must re-read this test's env.
    sys.path.insert(0, str(HERE))
    import figures
    importlib.reload(figures)
    spec = importlib.util.spec_from_file_location("figw4d", HERE / "fig_wave4_dwell.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_run(root: Path, arm: str, cell: str, rep: int, replicas: list[int], price: float) -> None:
    d = root / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": [
        {"bucket_start_utc": f"2026-09-16T00:00:{i:02d}Z", "cost_usd": 0.01 + 0.0001 * r,
         "cost_infra_usd": r * price} for i, r in enumerate(replicas)]}))


def test_fig21_draws_trajectories_churn_and_deltas_and_writes_the_sheet(tmp_path, monkeypatch):
    fig_dir, out_dir, evidence = tmp_path / "figs", tmp_path / "out", tmp_path / "evidence"
    mod = _load(monkeypatch, fig_dir, out_dir)
    ad = mod.ad
    monkeypatch.setattr(ad, "EVIDENCE", evidence)
    # dwell: two changes per window; calibrated: eight; tier-only: flat.
    shapes = {ad.TREATMENT: [8, 8, 8, 10, 10, 10, 10, 8, 8, 8],
              ad.REFERENCE: [8, 10, 8, 10, 8, 10, 8, 10, 8, 8],
              ad.ABLATION: [8] * 10}
    rows = []
    for cell in ad.CELLS:
        for i, arm in enumerate(ad.ARMS):
            for rep in (0, 1):
                rows.append({"system": arm, "workload": cell, "rep": rep,
                             "total_cost_usd": 0.1 + 0.01 * i, "mean_jain": 1.0, "mean_violation": 0.0,
                             "cache_hit_rate": 0.5, "ai_p95_ms": 1.0, "crud_p95_ms": 1.0,
                             "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
                _write_run(evidence, arm, cell, rep, shapes[arm], ad.PRICE_PER_REPLICA_SAMPLE)
    df = pd.DataFrame(rows)
    monkeypatch.setattr(ad, "load", lambda: df)

    assert mod.main() == 0

    content = json.loads((fig_dir / "content" / "fig21_dwell_plane.json").read_bytes())
    assert content["figure"] == "fig21_dwell_plane"
    assert (fig_dir / "fig21_dwell_plane.png").exists() and (fig_dir / "fig21_dwell_plane.pdf").exists()
    sheet = (out_dir / "FIGURE_WAVE4_DWELL.md").read_text(encoding="utf-8")
    panel_a, rest = sheet.split("## Panel B")
    panel_b, panel_c = rest.split("## Panel C")
    # every cell x both controllers x two reps on panel A
    assert panel_a.count(f"| {ad.TREATMENT} |") == 8 and panel_a.count(f"| {ad.REFERENCE} |") == 8
    # one churn row per AI cell, bound = half the calibrated arm's changes, dwell under it
    for cell in ad.AI_CELLS:
        assert f"| {cell} | 2.0 | 2 | 8.0 | 2 | 4.0 | yes |" in panel_b
    # one CI row per compared arm; only the calibrated row carries the bound
    assert f"| {ad.REFERENCE} |" in panel_c and "| +5% |" in panel_c
    assert f"| {ad.ABLATION} |" in panel_c
    # the figure inventoried its own caption
    inventory = (fig_dir / "FIGURES.md").read_text(encoding="utf-8")
    assert inventory.count("- **fig21_dwell_plane** — ") == 1
    assert "WL-H6" in inventory and "PASS" in inventory
