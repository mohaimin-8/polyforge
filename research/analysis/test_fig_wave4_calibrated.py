"""fig20 rides along the frozen B1' scorer: on synthetic evidence it must draw
every arm in every cell, one CI row per compared arm, and write the sheet."""

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
    spec = importlib.util.spec_from_file_location("figw4c", HERE / "fig_wave4_calibrated.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_run(root: Path, arm: str, cell: str, rep: int, costs: list[float]) -> None:
    d = root / "runs" / f"{arm}__{cell}__uniform__small__rep{rep}"
    d.mkdir(parents=True)
    (d / "eval-export-fine.json").write_text(json.dumps({"buckets": [
        {"bucket_start_utc": f"2026-09-16T00:00:{i:02d}Z", "cost_usd": c} for i, c in enumerate(costs)]}))


def test_fig20_draws_every_arm_and_cell_and_writes_the_sheet(tmp_path, monkeypatch):
    fig_dir, out_dir, evidence = tmp_path / "figs", tmp_path / "out", tmp_path / "evidence"
    mod = _load(monkeypatch, fig_dir, out_dir)
    aw = mod.aw
    monkeypatch.setattr(aw, "EVIDENCE", evidence)
    rows = []
    for cell in aw.CELLS:
        for i, arm in enumerate(aw.ARMS):
            for rep in (0, 1):
                per_bucket = 0.010 + 0.002 * i
                rows.append({"system": arm, "workload": cell, "rep": rep,
                             "total_cost_usd": per_bucket * 30, "mean_jain": 1.0, "mean_violation": 0.0,
                             "cache_hit_rate": 0.5, "ai_p95_ms": 1.0, "crud_p95_ms": 1.0,
                             "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
                _write_run(evidence, arm, cell, rep, [per_bucket] * 30)
    df = pd.DataFrame(rows)
    monkeypatch.setattr(aw, "load", lambda: df)

    assert mod.main() == 0

    content = json.loads((fig_dir / "content" / "fig20_calibrated_plane.json").read_bytes())
    assert content["figure"] == "fig20_calibrated_plane"
    assert (fig_dir / "fig20_calibrated_plane.png").exists() and (fig_dir / "fig20_calibrated_plane.pdf").exists()
    sheet = (out_dir / "FIGURE_WAVE4_CALIBRATED.md").read_text(encoding="utf-8")
    # every arm in every cell on panel A, one CI row per compared arm on panel B
    assert sheet.count("| ai_cacheable |") == 5 and sheet.count("| joint_stress |") == 5
    for arm in aw.ARMS:
        if arm != aw.TREATMENT:
            assert f"| {arm} |" in sheet.split("## Panel B")[1]
    # the calibrated arm is the cheapest in the synthetic data: all four won
    assert "Won against PolyForge (published), replica-only, cache-only, tier-only." in sheet


def test_fig20_survives_a_void_comparison(tmp_path, monkeypatch):
    """An arm with no window buckets gets an 'n/a' row, not a crash."""
    fig_dir, out_dir, evidence = tmp_path / "figs", tmp_path / "out", tmp_path / "evidence"
    mod = _load(monkeypatch, fig_dir, out_dir)
    aw = mod.aw
    monkeypatch.setattr(aw, "EVIDENCE", evidence)
    rows = []
    for arm in ("jcac-calibrated", "tier-only"):
        for rep in (0, 1):
            rows.append({"system": arm, "workload": "joint_stress", "rep": rep,
                         "total_cost_usd": 0.3, "mean_jain": 1.0, "mean_violation": 0.0,
                         "cache_hit_rate": 0.5, "ai_p95_ms": 1.0, "crud_p95_ms": 1.0,
                         "crud_p99_ms": 1.0, "ai_p99_ms": 1.0})
    _write_run(evidence, "jcac-calibrated", "joint_stress", 0, [0.01] * 30)
    monkeypatch.setattr(aw, "load", lambda: pd.DataFrame(rows))
    assert mod.main() == 0
    sheet = (out_dir / "FIGURE_WAVE4_CALIBRATED.md").read_text(encoding="utf-8")
    assert "| tier-only | n/a | n/a | 0 | no |" in sheet
