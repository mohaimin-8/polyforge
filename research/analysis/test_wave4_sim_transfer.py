"""The WL-H3' scorer, frozen with its pre-registration: the winner / Spearman
readings and the PASS rule on synthetic data, DuckDB-free."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "aw4t", Path(__file__).resolve().parent / "analysis_wave4_sim_transfer.py")
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)


def _rows(experiment: str, cell: str, costs: dict[str, float], reps: int) -> list[dict]:
    return [{"experiment": experiment, "system": arm, "workload": cell, "rep": rep, "steps": 30,
             "total_cost_usd": c, "mean_violation": 0.0, "mean_jain": 1.0}
            for arm, c in costs.items() for rep in range(reps)]


def test_spearman_and_winner():
    a = {"jcac-calibrated": 1, "jcac": 2, "replica-only": 5, "cache-only": 4, "tier-only": 3}
    assert aw.spearman(a, a) == 1.0
    b = {k: -v for k, v in a.items()}
    assert aw.spearman(a, b) == -1.0
    assert aw.winner(a) == "jcac-calibrated"
    assert aw.spearman({"jcac": 1, "tier-only": 2}, {"jcac": 1, "tier-only": 2}) is None


def test_pass_needs_three_agreements_and_an_improvement_over_the_control(monkeypatch):
    live = {"jcac-calibrated": 0.26, "jcac": 0.33, "replica-only": 26.0, "cache-only": 1.5, "tier-only": 0.32}
    sim_rows, live_rows = [], []
    for cell in aw.CELLS:
        live_rows += _rows("live", cell, live, 2)
        # fitted agrees in three cells, control in one
        fitted = dict(live) if cell != "crud_bursty" else {**live, "tier-only": 0.10}
        control = dict(live) if cell == "joint_stress" else {**live, "cache-only": 0.05}
        sim_rows += _rows(aw.FITTED[cell], cell, fitted, 3)
        sim_rows += _rows(aw.CONTROL, cell, control, 3)
    monkeypatch.setattr(aw, "load_sim", lambda: aw._with_j(pd.DataFrame(sim_rows)))
    monkeypatch.setattr(aw, "load_live", lambda: aw._with_j(pd.DataFrame(live_rows)))
    text = aw.build()
    assert text.startswith("# WL-H3′ — the simulator with its plant fitted to the live evidence: PASS (3 of 4")
    assert "the published plant: 1 of 4" in text
    assert "`ai_cacheable` PASS" in text and "`crud_bursty` PASS" in text  # calibrated below jcac everywhere

    # same agreements as the control -> no improvement -> FAIL
    sim_rows2 = [r for r in sim_rows if r["experiment"] != aw.CONTROL]
    for cell in aw.CELLS:
        fitted = dict(live) if cell != "crud_bursty" else {**live, "tier-only": 0.10}
        sim_rows2 += _rows(aw.CONTROL, cell, fitted, 3)
    monkeypatch.setattr(aw, "load_sim", lambda: aw._with_j(pd.DataFrame(sim_rows2)))
    assert "FAIL (3 of 4" in aw.build().splitlines()[0]
