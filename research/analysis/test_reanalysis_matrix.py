"""The design-point re-analysis must use the independent unit, and say so.

Audit 2026-09-26: the headline tests pool 300 (design point x rep) pairs into
one t-test, although the 5 reps of a design point share its workload, mix and
cluster -- so p shrinks with the rep count and says little. The independent
unit is the design point (60 of them). The composite J was also never
re-scored against the fair comparators the project itself built.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def _mod(monkeypatch, tmp_path):
    monkeypatch.setenv("POLYFORGE_ANALYSIS_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("ram", HERE / "reanalysis_matrix.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(effect_by_workload: dict, reps: int = 5, noise: float = 0.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for wl, eff in effect_by_workload.items():
        for mix in ("m1", "m2", "m3"):
            for size in ("s", "l"):
                for rep in range(reps):
                    base = 1.0 + rng.normal(0, noise)
                    for system, j in (("jcac", base + eff), ("hpa_fair", base)):
                        rows.append({"system": system, "workload": wl, "tenant_mix": mix,
                                     "cluster_size": size, "rep": rep, "J": j,
                                     "total_cost_usd": j, "mean_violation": 0.0, "mean_jain": 1.0})
    return pd.DataFrame(rows)


def test_design_points_are_the_unit_not_reps(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    df = _frame({"a": -0.1, "b": 0.1}, reps=5)
    res = mod.compare(df, "jcac", "hpa_fair", "J")
    assert res["n"] == 12                        # 2 workloads x 3 mixes x 2 sizes
    assert abs(res["mean_diff"]) < 1e-12         # the two workloads cancel
    assert res["better_share"] == 0.5


def test_per_workload_rows_carry_their_own_sign(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    rows = {r["workload"]: r for r in mod.per_workload(_frame({"a": -0.1, "b": 0.1}), "jcac", "hpa_fair", "J")}
    assert rows["a"]["mean_diff"] < 0 < rows["b"]["mean_diff"]
    assert rows["a"]["better"] == 6 and rows["b"]["better"] == 0


def test_block_bootstrap_widens_an_autocorrelated_interval(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    rng = np.random.default_rng(3)
    x = np.zeros(72)
    for i in range(1, 72):                       # strongly autocorrelated AR(1)
        x[i] = 0.9 * x[i - 1] + rng.normal()
    lo_iid, hi_iid = mod.bootstrap_ci(x, block=1, n=4000, seed=1)
    lo_blk, hi_blk = mod.bootstrap_ci(x, block=8, n=4000, seed=1)
    assert (hi_blk - lo_blk) > (hi_iid - lo_iid)


def test_record_builds_from_the_committed_data(monkeypatch, tmp_path):
    mod = _mod(monkeypatch, tmp_path)
    assert mod.main() == 0
    text = (tmp_path / mod.RECORD).read_text(encoding="utf-8")
    assert "exploratory" in text.lower() and "hpa_fair" in text and "design point" in text.lower()
